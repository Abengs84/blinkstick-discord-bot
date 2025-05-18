import tkinter as tk
from tkinter import ttk, scrolledtext
import win32gui
import win32con
from typing import Dict, Any, Callable, Optional
import threading
import queue
import datetime
import json
import psutil
import time
import os
from PIL import Image, ImageTk
import sys
import tkinter.messagebox

# Monkey patch tkinter.Variable.__del__ to avoid "main thread is not in main loop" warnings
original_variable_del = tk.Variable.__del__

def patched_variable_del(self):
    """Patched version of tkinter.Variable.__del__ that doesn't raise exceptions during cleanup"""
    try:
        # Only call the original __del__ if we're not in the middle of interpreter shutdown
        # and if the tkinter interpreter still exists
        if not (self._tk._tkrunning == 0 or not hasattr(self._tk, 'call')):
            original_variable_del(self)
    except (RuntimeError, TypeError, AttributeError):
        # Ignore any errors during garbage collection
        pass

# Apply the monkey patch
tk.Variable.__del__ = patched_variable_del

class StatusWindow:
    def __init__(self, config: Dict[str, Any], debug_print_func: Callable = print):
        self.config = config
        self.debug_print = debug_print_func
        self.window = None
        self.command_queue = queue.Queue()
        self.is_visible = False
        self.log_text = None
        self.start_time = datetime.datetime.now()
        self.is_connected = False  # Track connection state
        self.is_muted = False  # Track mute state
        self._pending_status_updates = []  # Store updates until UI is ready
        self._log_buffer = []  # Buffer for storing logs before window is created
        
        # Status variables - initialize all to None
        self.status_var = None
        self.channel_var = None
        self.uptime_var = None
        self.latency_var = None
        self.quality_var = None
        self.memory_var = None
        self.memory_bar = None
        self.voice_channel_var = None
        self.voice_channel_label = None
        self.status_indicator = None
        self.status_light = None
        self.status_label = None
        self.channel_label = None
        self.quality_label = None
        
        # Callback storage
        self.on_mute_toggle = None
        self.on_join_channel = None
        self.on_leave_channel = None
        self.on_led_test = None
        self.on_debug_toggle = None
        self.on_test_announcement = None
        self.on_disconnect = None
        self.on_reconnect = None
        self.on_chat_message = None
        
        # Button references
        self.mute_button = None
        self.join_button = None
        self.leave_button = None
        self.test_led_button = None
        self.debug_checkbox = None
        self.disconnect_button = None
        self.reconnect_button = None
        
        # Start UI thread
        self.ui_thread = threading.Thread(target=self._run_ui, daemon=True)
        self.ui_ready = threading.Event()
        self.ui_thread.start()
        self.ui_ready.wait()  # Wait for UI thread to be ready

    def _run_ui(self):
        """Run the UI in a separate thread"""
        try:
            self.debug_print("Starting UI thread")
            self.root = tk.Tk()
            self.root.withdraw()
            
            # Initialize status variables
            self.status_var = tk.StringVar(value="Initializing...")
            self.channel_var = tk.StringVar(value="Not connected")
            self.uptime_var = tk.StringVar(value="00:00:00")
            self.latency_var = tk.StringVar(value="N/A")
            self.quality_var = tk.StringVar(value="Disconnected")
            self.memory_var = tk.StringVar(value="Memory Usage: Calculating...")
            self.voice_channel_var = tk.StringVar(value="Not connected")
            
            # Configure text tags and styles to be used throughout the application
            self.text_tags = {
                "warning": {"foreground": "#FFFF00", "background": "#303030"}  # Bright yellow on dark gray
            }
            
            # Add exit flag checking to the mainloop
            self.exit_flag = threading.Event()
            
            # Signal that the UI thread is ready
            self.ui_ready.set()
            
            # Start processing events
            self._process_events()
            
            # Check for exit flag periodically
            def check_exit():
                if hasattr(self, 'exit_flag') and self.exit_flag.is_set():
                    self.debug_print("Exit flag detected, closing UI thread")
                    if hasattr(self, 'root') and self.root:
                        self.root.quit()
                        return
                if hasattr(self, 'root') and self.root:
                    self.root.after(100, check_exit)
            
            # Start exit checker
            self.root.after(100, check_exit)
            
            # Start main loop
            self.root.mainloop()
            self.debug_print("UI thread mainloop exited")
            
        except Exception as e:
            self.debug_print(f"Error in UI thread: {e}")
        finally:
            self.ui_ready.set()  # Set the event even on error

    def set_callbacks(self, mute_toggle=None, join_channel=None, 
                     leave_channel=None, led_test=None, debug_toggle=None,
                     test_announcement=None, disconnect=None, reconnect=None,
                     chat_callback=None):
        """Set callback functions for buttons"""
        self.on_mute_toggle = mute_toggle
        self.on_join_channel = join_channel
        self.on_leave_channel = leave_channel
        self.on_led_test = led_test
        self.on_debug_toggle = debug_toggle
        self.on_test_announcement = test_announcement
        self.on_disconnect = disconnect
        self.on_reconnect = reconnect
        self.on_chat_message = chat_callback
        
    def _process_events(self):
        """Process events in the UI thread"""
        try:
            # Process all pending commands
            while True:
                try:
                    cmd, args = self.command_queue.get_nowait()
                    # Reduce log verbosity for common commands
                    if cmd not in ('status', 'channel', 'show'):
                        self.debug_print(f"Processing command: {cmd} with args: {args}")
                    
                    if cmd == 'show':
                        if not self.window:
                            if self._create_window():
                                self.window.deiconify()
                                self.is_visible = True
                        else:
                            self.window.deiconify()
                            self.window.lift()
                            self.is_visible = True
                            
                    elif cmd == 'hide' and self.window:
                        self.window.withdraw()
                        self.is_visible = False
                        
                    elif cmd == 'status':
                        self.update_status(args)
                        
                    elif cmd == 'channel':
                        self.update_channel(args)
                        if args != "Not connected":
                            # If we have a channel, we're definitely connected
                            self.update_status("Connected to Discord")
                            
                    elif cmd == 'log' and self.log_text:
                        self.log_text.config(state='normal')
                        self.log_text.insert('end', args + '\n')
                        self.log_text.see('end')
                        self.log_text.config(state='disabled')
                        
                    elif cmd == 'callback':
                        args()
                        
                except queue.Empty:
                    break

            # Always update system stats periodically
            self._update_system_stats()
                
        except Exception as e:
            self.debug_print(f"Error processing events: {e}")
            
        finally:
            # Schedule next update (reduced polling frequency from 100ms to 250ms)
            if self.root:
                self.root.after(250, self._process_events)
            
    def _create_window(self):
        """Create the status window"""
        try:
            # Create new Toplevel window
            self.window = tk.Toplevel(self.root)
            self.window.title("Discord Voice Assistant")
            
            # Set window position from config
            if 'window_position' in self.config:
                x = self.config['window_position'].get('x', 100)
                y = self.config['window_position'].get('y', 100)
                self.window.geometry(f"+{x}+{y}")
            
            # Configure window properties
            self.window.attributes('-topmost', True)
            self.window.resizable(True, True)
            
            # Force the classic theme which always shows tabs
            style = ttk.Style(self.window)
            style.theme_use('default')
            
            # Create main container
            main_container = ttk.Frame(self.window)
            main_container.pack(fill='both', expand=True)
            
            # Create notebook
            self.notebook = ttk.Notebook(main_container)
            self.notebook.pack(fill='both', expand=True, padx=2, pady=(2, 0))  # Reduced bottom padding
            
            # Create tabs
            status_tab = ttk.Frame(self.notebook)
            settings_tab = ttk.Frame(self.notebook)
            logs_tab = ttk.Frame(self.notebook)
            chat_tab = ttk.Frame(self.notebook)
            led_tab = ttk.Frame(self.notebook)
            
            # Add tabs to notebook
            self.notebook.add(status_tab, text='Status')
            self.notebook.add(settings_tab, text='Settings')
            self.notebook.add(logs_tab, text='Logs')
            self.notebook.add(chat_tab, text='Chat')
            self.notebook.add(led_tab, text='LED Config')
            
            # Create tab contents
            self._create_status_tab(status_tab)
            self._create_settings_tab(settings_tab)
            self._create_logs_tab(logs_tab)
            self._create_chat_tab(chat_tab)
            self._create_led_tab(led_tab)
            
            # Create bottom button frame (outside notebook)
            button_frame = ttk.Frame(main_container)
            button_frame.pack(fill='x', padx=5, pady=5)
            
            ttk.Button(button_frame, text="Exit",
                      command=self._on_exit).pack(side='right', padx=5)
            ttk.Button(button_frame, text="Close",
                      command=self.hide).pack(side='right', padx=5)
            
            # Remove window from taskbar
            hwnd = win32gui.GetParent(self.window.winfo_id())
            win32gui.SetWindowLong(hwnd, win32con.GWL_EXSTYLE,
                                 win32gui.GetWindowLong(hwnd, win32con.GWL_EXSTYLE) |
                                 win32con.WS_EX_TOOLWINDOW)
            
            # Set minimum window size
            self.window.update_idletasks()
            self.window.minsize(400, 500)
            
            # Set up window close handler
            self.window.protocol("WM_DELETE_WINDOW", self.hide)
            
            # Update window to ensure all widgets are properly displayed
            self.window.update()
            
            # Start updating UI stats - do this immediately after UI is ready
            self._update_system_stats()
            
            # Process any pending status updates
            if hasattr(self, '_pending_status_updates') and self._pending_status_updates:
                for status in self._pending_status_updates:
                    self._process_status_update(status)
                self._pending_status_updates.clear()
            
            # Force a refresh of connection status
            if self.is_connected:
                self.update_status("Connected to Discord")
                if hasattr(self, 'channel_var') and self.channel_var.get() != "Not connected":
                    # Update color to green
                    if hasattr(self, 'channel_label'):
                        self.channel_label.configure(foreground='green')
                    if hasattr(self, 'quality_label'):
                        self.quality_label.configure(foreground='green')
                        self.quality_var.set("Connected")
            
            return True
            
        except Exception as e:
            self.debug_print(f"Error creating status window: {e}")
            if self.window:
                self.window.destroy()
                self.window = None
            return False
            
    def _create_status_tab(self, parent):
        """Create the status tab content"""
        # Add padding around the entire tab
        main_frame = ttk.Frame(parent, padding=10)
        main_frame.pack(fill='both', expand=True)
        
        # Status section at top
        status_frame = ttk.Frame(main_frame)
        status_frame.pack(fill='x', pady=(0, 5))
        
        # Bot Status with indicator
        bot_frame = ttk.Frame(status_frame)
        bot_frame.pack(fill='x')
        
        # Create status indicator using a Frame with rounded appearance
        indicator_size = 10
        self.status_indicator = tk.Frame(bot_frame, width=indicator_size, height=indicator_size, 
                                       background='red', bd=0, highlightthickness=0)
        self.status_indicator.pack(side='left', padx=(0, 5))
        
        # Force the frame to not expand and keep its size
        self.status_indicator.pack_propagate(False)
        
        # Round the corners by drawing a circle on it
        # Ensure indicator size is fixed
        self.status_indicator.update()
        
        # Bot Status text
        ttk.Label(bot_frame, text="Bot Status: ", font=('TkDefaultFont', 9, 'bold')).pack(side='left')
        self.status_var = tk.StringVar(value="Initializing...")
        self.status_label = ttk.Label(bot_frame, textvariable=self.status_var)
        self.status_label.pack(side='left')
        
        # Channel Status
        channel_frame = ttk.Frame(status_frame)
        channel_frame.pack(fill='x', pady=(2, 0))
        ttk.Label(channel_frame, text="Channel: ", font=('TkDefaultFont', 9, 'bold')).pack(side='left')
        self.channel_var = tk.StringVar(value="Not connected")
        self.channel_label = ttk.Label(channel_frame, textvariable=self.channel_var)
        self.channel_label.pack(side='left')
        
        # Voice Channel Status
        voice_frame = ttk.Frame(status_frame)
        voice_frame.pack(fill='x', pady=(2, 0))
        ttk.Label(voice_frame, text="Voice Channel: ", font=('TkDefaultFont', 9, 'bold')).pack(side='left')
        self.voice_channel_var = tk.StringVar(value="Not connected")
        self.voice_channel_label = ttk.Label(voice_frame, textvariable=self.voice_channel_var)
        self.voice_channel_label.pack(side='left')
        
        # Quick Actions section
        actions_frame = ttk.LabelFrame(main_frame, text="Quick Actions")
        actions_frame.pack(fill='x', pady=10)
        
        button_frame = ttk.Frame(actions_frame)
        button_frame.pack(padx=5, pady=5)
        
        self.disconnect_button = ttk.Button(button_frame, text="Disconnect",
                                          command=self._on_disconnect_click)
        self.disconnect_button.pack(side='left', padx=2)
        
        self.reconnect_button = ttk.Button(button_frame, text="Reconnect",
                                          command=self._on_reconnect_click)
        self.reconnect_button.pack(side='left', padx=2)
        
        self.mute_button = ttk.Button(button_frame, text="Toggle Mute",
                                     command=self._on_mute_click)
        self.mute_button.pack(side='left', padx=2)
        
        # Connection Monitor section
        monitor_frame = ttk.LabelFrame(main_frame, text="Voice Connection Monitor")
        monitor_frame.pack(fill='x', pady=5)
        
        monitor_content = ttk.Frame(monitor_frame)
        monitor_content.pack(padx=5, pady=5, fill='x')
        
        # Uptime
        uptime_frame = ttk.Frame(monitor_content)
        uptime_frame.grid(row=0, column=0, sticky='w')
        ttk.Label(uptime_frame, text="Voice Uptime: ").pack(side='left')
        self.uptime_var = tk.StringVar(value="00:00:00")
        ttk.Label(uptime_frame, textvariable=self.uptime_var).pack(side='left')
        
        # Latency
        latency_frame = ttk.Frame(monitor_content)
        latency_frame.grid(row=1, column=0, sticky='w')
        ttk.Label(latency_frame, text="Voice Latency: ").pack(side='left')
        self.latency_var = tk.StringVar(value="N/A")
        ttk.Label(latency_frame, textvariable=self.latency_var).pack(side='left')
        
        # Connection Quality
        quality_frame = ttk.Frame(monitor_content)
        quality_frame.grid(row=2, column=0, sticky='w')
        ttk.Label(quality_frame, text="Voice Connection: ").pack(side='left')
        self.quality_var = tk.StringVar(value="Disconnected")
        self.quality_label = ttk.Label(quality_frame, textvariable=self.quality_var, foreground='red')
        self.quality_label.pack(side='left')
        
        # System Monitor section
        system_frame = ttk.LabelFrame(main_frame, text="System Monitor")
        system_frame.pack(fill='x', pady=5)
        
        system_content = ttk.Frame(system_frame)
        system_content.pack(padx=5, pady=5, fill='x')
        
        # Memory Usage
        self.memory_var = tk.StringVar(value="Memory Usage: Calculating...")
        ttk.Label(system_content, textvariable=self.memory_var).pack(anchor='w')
        self.memory_bar = ttk.Progressbar(system_content, length=200, mode='determinate')
        self.memory_bar.pack(fill='x', pady=2)
        
        # Announcement Configuration
        self._create_announcement_config(main_frame)
        
        # Bottom buttons
        button_frame = ttk.Frame(main_frame)
        button_frame.pack(fill='x', pady=10)
        
        # Remove duplicate Exit/Close buttons
        # Keep the space for visual consistency
        spacer = ttk.Frame(button_frame)
        spacer.pack(side='right', padx=5, pady=10)
        
    def _create_settings_tab(self, parent):
        """Create the settings tab content"""
        # Main frame with padding
        main_frame = ttk.Frame(parent, padding=10)
        main_frame.pack(fill='both', expand=True)
        
        # Discord Settings
        discord_frame = ttk.LabelFrame(main_frame, text="Discord Settings", padding="5")
        discord_frame.pack(fill='x', pady=(0, 10))
        
        # Discord Token
        token_frame = ttk.Frame(discord_frame)
        token_frame.pack(fill='x', pady=2)
        ttk.Label(token_frame, text="Discord Token:").pack(side='left')
        self.token_var = tk.StringVar(value=self.config.get('discord_token', ''))
        token_entry = ttk.Entry(token_frame, textvariable=self.token_var, show="*", width=40)
        token_entry.pack(side='left', padx=5, fill='x', expand=True)
        
        # Target User
        user_frame = ttk.Frame(discord_frame)
        user_frame.pack(fill='x', pady=2)
        ttk.Label(user_frame, text="Target User:").pack(side='left')
        self.target_user_var = tk.StringVar(value=self.config.get('target_user', ''))
        ttk.Entry(user_frame, textvariable=self.target_user_var, width=40).pack(side='left', padx=5, fill='x', expand=True)
        
        # Listen to all users
        self.listen_all_var = tk.BooleanVar(value=self.config.get('listen_all_users', False))
        ttk.Checkbutton(discord_frame, text="Listen to all users", 
                       variable=self.listen_all_var).pack(anchor='w', pady=2)
        
        # OpenAI Settings
        openai_frame = ttk.LabelFrame(main_frame, text="OpenAI Settings", padding="5")
        openai_frame.pack(fill='x', pady=(0, 10))
        
        # API Key
        api_frame = ttk.Frame(openai_frame)
        api_frame.pack(fill='x', pady=2)
        ttk.Label(api_frame, text="OpenAI API Key:").pack(side='left')
        self.api_key_var = tk.StringVar(value=self.config.get('openai_api_key', ''))
        api_entry = ttk.Entry(api_frame, textvariable=self.api_key_var, show="*", width=40)
        api_entry.pack(side='left', padx=5, fill='x', expand=True)
        
        # GPT Model Selection
        model_frame = ttk.Frame(openai_frame)
        model_frame.pack(fill='x', pady=2)
        ttk.Label(model_frame, text="GPT Model:").pack(side='left')
        
        # Model options with pricing info
        models = [
            "gpt-4-turbo-preview ($0.01/1K input, $0.03/1K output)",
            "gpt-4 ($0.03/1K input, $0.06/1K output)",
            "gpt-3.5-turbo ($0.0005/1K input, $0.0015/1K output)"
        ]
        self.model_var = tk.StringVar(value=self.config.get('gpt_model', models[0]))
        model_combo = ttk.Combobox(model_frame, textvariable=self.model_var, values=models, width=50, state='readonly')
        model_combo.pack(side='left', padx=5, fill='x', expand=True)
        
        # General settings
        general_frame = ttk.LabelFrame(main_frame, text="General Settings", padding="5")
        general_frame.pack(fill='x', pady=(0, 10))
        
        # Debug mode
        self.debug_var = tk.BooleanVar(value=self.config.get('debug_mode', False))
        ttk.Checkbutton(general_frame, text="Debug Mode", 
                       variable=self.debug_var,
                       command=self._on_debug_toggle).pack(anchor='w')
        
        # Hotkey settings
        hotkey_frame = ttk.LabelFrame(main_frame, text="Hotkey Settings", padding="5")
        hotkey_frame.pack(fill='x', pady=(0, 10))
        
        ttk.Label(hotkey_frame, text="Mute Toggle:").pack(side='left', padx=(0, 5))
        self.hotkey_var = tk.StringVar(value=self.config.get('hotkey', 'ctrl+shift+alt+o'))
        hotkey_entry = ttk.Entry(hotkey_frame, textvariable=self.hotkey_var)
        hotkey_entry.pack(side='left', fill='x', expand=True)
        
        # Save button
        button_frame = ttk.Frame(main_frame)
        button_frame.pack(fill='x', pady=10)
        ttk.Button(button_frame, text="Save Settings",
                  command=self._on_settings_save).pack(side='right', padx=5)
                  
    def _on_settings_save(self):
        """Handle settings save button click"""
        try:
            # Update config with new values
            self.config['discord_token'] = self.token_var.get()
            self.config['openai_api_key'] = self.api_key_var.get()
            self.config['target_user'] = self.target_user_var.get()
            self.config['listen_all_users'] = self.listen_all_var.get()
            self.config['debug_mode'] = self.debug_var.get()
            self.config['hotkey'] = self.hotkey_var.get()
            
            # Extract just the model name from the combo box selection
            model_selection = self.model_var.get()
            model_name = model_selection.split(" ")[0]  # Get just the model name part
            self.config['gpt_model'] = model_name
            
            # Save to file
            with open('config.json', 'w') as f:
                json.dump(self.config, f, indent=4)
            
            self.debug_print("Settings saved successfully")
            
            # Show success message
            tk.messagebox.showinfo("Success", "Settings saved successfully!\nPlease restart the application for some changes to take effect.")
            
        except Exception as e:
            self.debug_print(f"Error saving settings: {e}")
            tk.messagebox.showerror("Error", f"Failed to save settings: {e}")
        
    def _create_logs_tab(self, parent):
        """Create the logs tab content"""
        # Main frame with padding
        main_frame = ttk.Frame(parent, padding=10)
        main_frame.pack(fill='both', expand=True)
        
        # Create log text widget
        self.log_text = scrolledtext.ScrolledText(main_frame, wrap=tk.WORD, height=20, bg="black", fg="white")
        self.log_text.pack(fill='both', expand=True)
        self.log_text.config(state='disabled')
        
        # Configure text tags for different message types using the predefined styles
        if hasattr(self, 'text_tags'):
            for tag_name, tag_config in self.text_tags.items():
                self.log_text.tag_configure(tag_name, **tag_config)
        
        # Clear any existing logs on startup
        self._clear_logs()
        
        # Display buffered logs that occurred before UI was created
        if hasattr(self, '_log_buffer') and self._log_buffer:
            self.log_text.config(state='normal')
            for log_message in self._log_buffer:
                if "WARNING:" in log_message:
                    self.log_text.insert('end', log_message, "warning")
                else:
                    self.log_text.insert('end', log_message)
            self.log_text.see('end')
            self.log_text.config(state='disabled')
            # Clear buffer after showing logs
            self._log_buffer = []
            
        # Controls frame
        controls_frame = ttk.Frame(main_frame)
        controls_frame.pack(fill='x', pady=(5, 0))
        
        # Clear button
        clear_button = ttk.Button(controls_frame, text="Clear Logs",
                               command=self._clear_logs)
        clear_button.pack(side='right')
        
    def _clear_logs(self):
        """Clear the log text widget"""
        if self.log_text:
            self.log_text.config(state='normal')
            self.log_text.delete(1.0, tk.END)
            self.log_text.config(state='disabled')
            
    def add_log(self, message: str):
        """Add a message to the log"""
        try:
            # Filter out verbose log messages (use the same filtering logic as in SystrayManager)
            if not self.config.get('debug_mode', False):
                filter_patterns = [
                    "Retrying channel update:", 
                    "Received channel update:", 
                    "Queuing channel update for after window initialization",
                    "Applying pending channel update:",
                    "Found active connection in channel",
                    "Found active voice client in guild",
                    "Target user is in voice channel:",
                    "Already connected to channel",
                    "Successfully connected to voice channel",
                    "Error cleaning up temp file:",
                    "Processing command: callback with args:",
                    "Error getting latency:",
                    "Voice latency is infinity or NaN, setting to 0"
                ]
                for pattern in filter_patterns:
                    if pattern in message:
                        return  # Skip logging this message
            
            # Add timestamp to message
            timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            formatted_message = f"[{timestamp}] {message}\n"
            
            # If log_text widget doesn't exist yet, store in buffer
            if not hasattr(self, 'log_text') or not self.log_text:
                self._log_buffer.append(formatted_message)
                return
                
            # Update log in UI thread
            if hasattr(self, 'root'):
                self.root.after(0, self._append_log, formatted_message)
        except Exception as e:
            print(f"Error adding log: {e}")  # Fallback to print since logging might be broken
            
    def _append_log(self, message: str):
        """Append a message to the log text widget (must be called from UI thread)"""
        try:
            # Direct filter at append time to catch any messages that slipped through
            if "Found active connection in channel" in message:
                return
                
            self.log_text.config(state='normal')
            
            # Check if this is a warning message
            if "WARNING:" in message:
                # Insert with the warning tag
                self.log_text.insert('end', message, "warning")
            else:
                # Normal log entry
                self.log_text.insert('end', message)
                
            self.log_text.see('end')
            self.log_text.config(state='disabled')
        except Exception as e:
            print(f"Error appending log: {e}")  # Fallback to print since logging might be broken
        
    def _create_chat_tab(self, parent):
        """Create the chat tab content"""
        # Main frame with padding
        main_frame = ttk.Frame(parent, padding=10)
        main_frame.pack(fill='both', expand=True)
        
        # Create chat history display
        chat_scroll = ttk.Scrollbar(main_frame)
        chat_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        
        self.chat_text = tk.Text(main_frame, wrap=tk.WORD, yscrollcommand=chat_scroll.set,
                               bg='black', fg='white', font=('Consolas', 10))
        self.chat_text.pack(fill=tk.BOTH, expand=True)
        
        chat_scroll.config(command=self.chat_text.yview)
        
        # Make chat text read-only
        self.chat_text.configure(state='disabled')
        
        # Create input area
        input_frame = ttk.Frame(main_frame)
        input_frame.pack(fill=tk.X, pady=5)
        
        self.chat_input = ttk.Entry(input_frame)
        self.chat_input.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=5)
        
        self.send_button = ttk.Button(input_frame, text="Send", 
                                    command=self._on_chat_send)
        self.send_button.pack(side=tk.RIGHT, padx=5)
        
        # Bind Enter key to send message
        self.chat_input.bind('<Return>', lambda e: self._on_chat_send())
        
    def _on_mute_click(self):
        """Handle PTT toggle button click"""
        if self.on_mute_toggle:
            self.command_queue.put(('callback', lambda: self.on_mute_toggle('toggle_ptt')))

    def _on_join_click(self):
        """Handle join channel button click"""
        if self.on_join_channel:
            self.command_queue.put(('callback', self.on_join_channel))
            
    def _on_leave_click(self):
        """Handle leave channel button click"""
        if self.on_leave_channel:
            self.command_queue.put(('callback', self.on_leave_channel))
            
    def _on_led_test(self):
        """Handle LED test button click"""
        if hasattr(self, 'on_led_test'):
            self.command_queue.put(('callback', self.on_led_test))
            
    def _on_debug_toggle(self):
        """Handle debug mode toggle"""
        if self.on_debug_toggle:
            self.command_queue.put(('callback', self.on_debug_toggle))
            
    def _process_commands(self):
        """Process commands from the queue"""
        try:
            # Update uptime
            if hasattr(self, 'start_time') and hasattr(self, 'uptime_var'):
                uptime = datetime.datetime.now() - self.start_time
                hours = int(uptime.total_seconds() // 3600)
                minutes = int((uptime.total_seconds() % 3600) // 60)
                seconds = int(uptime.total_seconds() % 60)
                self.uptime_var.set(f"{hours:02d}:{minutes:02d}:{seconds:02d}")
            
            # Process all pending commands
            while True:
                try:
                    cmd, args = self.command_queue.get_nowait()
                    if cmd == 'show' and not self.is_visible:
                        self.window.deiconify()
                        self.is_visible = True
                    elif cmd == 'hide' and self.is_visible:
                        self.window.withdraw()
                        self.is_visible = False
                    elif cmd == 'status':
                        self.status_var.set(args)
                    elif cmd == 'channel':
                        self.channel_var.set(args)
                    elif cmd == 'log' and self.log_text:
                        self.log_text.config(state='normal')
                        self.log_text.insert('end', args + '\n')
                        self.log_text.see('end')
                        self.log_text.config(state='disabled')
                    elif cmd == 'callback':
                        args()  # Execute the callback function
                except queue.Empty:
                    break
                    
        except Exception as e:
            self.debug_print(f"Error processing commands: {e}")
            
        finally:
            # Schedule next check if window exists
            if self.window:
                self.window.after(100, self._process_commands)
            
    def show(self):
        """Show the status window"""
        self.debug_print("Show command received")
        if not self.root:
            self.debug_print("Error: UI not initialized")
            return
        self.root.after(0, lambda: self.command_queue.put(('show', None)))
            
    def hide(self):
        """Hide the status window"""
        self.debug_print("Hide command received")
        if not self.root:
            self.debug_print("Error: UI not initialized")
            return
        self.root.after(0, lambda: self.command_queue.put(('hide', None)))
            
    def toggle(self):
        """Toggle window visibility"""
        self.debug_print("Toggle command received")
        if not self.root:
            self.debug_print("Error: UI not initialized")
            return
        if self.is_visible:
            self.hide()
        else:
            self.show()
            
    def update_status(self, status: str):
        """Update status text"""
        try:
            # Process chat responses directly here to ensure they show up in the chat window
            if status.startswith("chat_response:"):
                self._process_status_update(status)
                return
                
            if not hasattr(self, 'root') or not self.root:
                self._pending_status_updates.append(status)
                return
                
            def update():
                try:
                    if hasattr(self, 'status_var') and self.status_var:
                        self.status_var.set(status)
                        
                    if "connected" in status.lower() and "dis" not in status.lower():
                        if hasattr(self, 'status_label') and self.status_label:
                            self.status_label.configure(foreground='green')
                        self._update_status_indicator('green')
                        self.is_connected = True
                        
                    elif "error" in status.lower() or "disconnected" in status.lower() or "dis" in status.lower():
                        if hasattr(self, 'status_label') and self.status_label:
                            self.status_label.configure(foreground='red')
                        self._update_status_indicator('red')
                        self.is_connected = False
                        
                    # Fix: Ensure status updates correctly in more situations
                    elif "initializing" in status.lower() and hasattr(self, 'voice_channel_var') and self.voice_channel_var.get() != "Not connected":
                        # If we're showing initializing but we have a voice channel, we're actually connected
                        if hasattr(self, 'status_label') and self.status_label:
                            self.status_label.configure(foreground='green')
                        self._update_status_indicator('green')
                        self.status_var.set("Connected to Discord")
                        self.is_connected = True
                        
                except Exception as e:
                    self.debug_print(f"Error in status update: {e}")
                    
            self.root.after(0, update)
            
        except Exception as e:
            self.debug_print(f"Error scheduling status update: {e}")

    def _update_status_indicator(self, color: str):
        """Update the status indicator color"""
        try:
            if hasattr(self, 'status_indicator') and self.status_indicator:
                try:
                    self.status_indicator.configure(bg=color)
                except Exception as e:
                    # Suppress specific configuration errors
                    if "'NoneType' object has no attribute 'configure'" not in str(e):
                        self.debug_print(f"Error configuring status indicator: {e}")
                    
            if hasattr(self, 'status_light') and self.status_light:
                try:
                    self.status_light.configure(bg=color)
                except Exception as e:
                    # Suppress specific configuration errors
                    if "'NoneType' object has no attribute 'configure'" not in str(e):
                        self.debug_print(f"Error configuring status light: {e}")
        except Exception as e:
            # Suppress this specific error to avoid log spam
            if "'NoneType' object has no attribute 'configure'" not in str(e):
                self.debug_print(f"Error updating status indicator: {e}")
            
    def update_channel(self, channel: str):
        """Update channel info"""
        try:
            if not hasattr(self, 'root') or not self.root:
                # Store update for later
                if not hasattr(self, '_pending_channel_updates'):
                    self._pending_channel_updates = []
                self._pending_channel_updates.append(channel)
                return
                
            if not hasattr(self, 'channel_var') or not self.channel_var:
                self.debug_print("Channel variable not initialized yet")
                return
            
            def update():
                try:
                    if hasattr(self, 'channel_var') and self.channel_var:
                        self.channel_var.set(channel)
                        
                    if channel != "Not connected":
                        # If we have a channel, we're definitely connected
                        if hasattr(self, 'channel_label') and self.channel_label:
                            try:
                                self.channel_label.configure(foreground='green')
                            except Exception as e:
                                if "'NoneType' object has no attribute 'configure'" not in str(e):
                                    self.debug_print(f"Error configuring channel label: {e}")
                        
                        if hasattr(self, 'status_var') and self.status_var:
                            self.status_var.set("Connected to Discord")
                            
                        if hasattr(self, 'status_label') and self.status_label:
                            try:
                                self.status_label.configure(foreground='green')
                            except Exception as e:
                                if "'NoneType' object has no attribute 'configure'" not in str(e):
                                    self.debug_print(f"Error configuring status label: {e}")
                                    
                        self._update_status_indicator('green')
                        self.is_connected = True
                        
                        # Also update voice connection display
                        if hasattr(self, 'voice_channel_var') and self.voice_channel_var:
                            self.voice_channel_var.set(channel)
                            
                        if hasattr(self, 'voice_channel_label') and self.voice_channel_label:
                            try:
                                self.voice_channel_label.configure(foreground='green')
                            except Exception as e:
                                if "'NoneType' object has no attribute 'configure'" not in str(e):
                                    self.debug_print(f"Error configuring voice channel label: {e}")
                                    
                        if hasattr(self, 'quality_var') and self.quality_var:
                            self.quality_var.set("Good")
                            
                        if hasattr(self, 'quality_label') and self.quality_label:
                            try:
                                self.quality_label.configure(foreground='green')
                            except Exception as e:
                                if "'NoneType' object has no attribute 'configure'" not in str(e):
                                    self.debug_print(f"Error configuring quality label: {e}")
                    else:
                        # Not connected
                        if hasattr(self, 'channel_label') and self.channel_label:
                            try:
                                self.channel_label.configure(foreground='red')
                            except Exception as e:
                                if "'NoneType' object has no attribute 'configure'" not in str(e):
                                    self.debug_print(f"Error configuring channel label: {e}")
                                    
                        if hasattr(self, 'voice_channel_var') and self.voice_channel_var:
                            self.voice_channel_var.set("Not connected")
                            
                        if hasattr(self, 'voice_channel_label') and self.voice_channel_label:
                            try:
                                self.voice_channel_label.configure(foreground='red')
                            except Exception as e:
                                if "'NoneType' object has no attribute 'configure'" not in str(e):
                                    self.debug_print(f"Error configuring voice channel label: {e}")
                                    
                        if hasattr(self, 'quality_var') and self.quality_var:
                            self.quality_var.set("Disconnected")
                            
                        if hasattr(self, 'quality_label') and self.quality_label:
                            try:
                                self.quality_label.configure(foreground='red')
                            except Exception as e:
                                if "'NoneType' object has no attribute 'configure'" not in str(e):
                                    self.debug_print(f"Error configuring quality label: {e}")
                                    
                        self.is_connected = False
                except Exception as e:
                    self.debug_print(f"Error in channel update: {e}")
                
            self.root.after(0, update)
                
        except Exception as e:
            self.debug_print(f"Error updating channel: {e}")
            
    def _update_uptime(self):
        """Update uptime counter"""
        try:
            if hasattr(self, '_start_time') and hasattr(self, 'root') and self.root:
                elapsed = int(time.time() - self._start_time)
                hours = elapsed // 3600
                minutes = (elapsed % 3600) // 60
                seconds = elapsed % 60
                
                if hasattr(self, 'uptime_var'):
                    self.uptime_var.set(f"{hours:02d}:{minutes:02d}:{seconds:02d}")
                    
                # Schedule next update using root.after
                self._uptime_after = self.root.after(1000, self._update_uptime)
                
        except Exception as e:
            self.debug_print(f"Error updating uptime: {e}")
            
    def cleanup(self):
        """Clean up resources and shutdown the UI thread"""
        try:
            self.debug_print("Cleaning up status window resources")
            
            # Set up a custom exception handler to ignore Tkinter errors during cleanup
            original_excepthook = sys.excepthook
            def cleanup_excepthook(exc_type, exc_value, exc_traceback):
                if issubclass(exc_type, (RuntimeError, tk.TclError)) and "main thread is not in main loop" in str(exc_value):
                    # Silently ignore this specific error
                    return
                # For all other exceptions, use the original handler
                original_excepthook(exc_type, exc_value, exc_traceback)
            
            # Install our custom exception handler
            sys.excepthook = cleanup_excepthook
            
            # Force flag to indicate we're in cleanup mode
            self._in_cleanup = True
            
            # Set exit flag if exists
            if not hasattr(self, 'exit_flag'):
                self.exit_flag = threading.Event()
            self.exit_flag.set()
            
            # Clear the command queue 
            while not self.command_queue.empty():
                try:
                    self.command_queue.get_nowait()
                except queue.Empty:
                    break
            
            # Save string var references
            tk_vars = []
            
            # Detach all Tkinter variables from the interpreter
            if hasattr(self, 'root') and self.root:
                try:
                    # Collect all Tkinter variables
                    for attr_name in dir(self):
                        if attr_name.startswith('__'):
                            continue
                            
                        try:
                            attr = getattr(self, attr_name)
                            if isinstance(attr, tk.Variable):
                                tk_vars.append((attr_name, attr))
                        except:
                            pass
                    
                    # First, clear all trace callbacks
                    for _, var in tk_vars:
                        try:
                            # Get all trace information
                            try:
                                traces = var.trace_info()
                                for mode, cbname in traces:
                                    try:
                                        var.trace_remove(mode, cbname)
                                    except:
                                        pass
                            except:
                                pass
                            
                            # Try to set to empty value to avoid errors
                            try:
                                if isinstance(var, tk.StringVar):
                                    var.set("")
                                elif isinstance(var, tk.BooleanVar):
                                    var.set(False)
                                elif isinstance(var, tk.IntVar):
                                    var.set(0)
                            except:
                                pass
                        except:
                            pass
                except:
                    pass
            
            # IMPORTANT: First destroy root window directly from this thread
            # This is more reliable than scheduling it through the event loop
            if hasattr(self, 'root') and self.root:
                try:
                    self.debug_print("Destroying root window")
                    
                    # Ensure all windows are withdrawn first
                    if hasattr(self, 'window') and self.window:
                        try:
                            self.window.withdraw()
                        except:
                            pass
                            
                    self.root.withdraw()
                    
                    # Attempt a clean shutdown of Tkinter
                    try:
                        self.root.quit()
                        self.root.update()  # Process any pending events
                    except:
                        pass
                        
                    try:
                        self.root.destroy()
                    except Exception as e:
                        self.debug_print(f"Error destroying root: {e}")
                except Exception as e:
                    self.debug_print(f"Error destroying root: {e}")
            
            # Now set Tkinter variables to None after root is destroyed
            # This breaks their connection to the Tk interpreter
            for attr_name, _ in tk_vars:
                try:
                    setattr(self, attr_name, None)
                except:
                    pass
            
            # Wait briefly for UI thread to notice the window is gone
            time.sleep(0.1)
            
            # Now aggressively terminate the UI thread if it's still alive
            if hasattr(self, 'ui_thread') and self.ui_thread and self.ui_thread.is_alive():
                try:
                    self.debug_print("UI thread still alive, attempting to join with timeout")
                    self.ui_thread.join(timeout=0.5)
                    
                    # If still alive, handle differently depending on platform
                    if self.ui_thread.is_alive():
                        self.debug_print("WARNING: UI thread won't terminate normally")
                        # We've done our best to clean up - the rest will be handled by os._exit
                except Exception as e:
                    self.debug_print(f"Error joining UI thread: {e}")
            
            # Clear all references to UI elements
            self.window = None
            self.log_text = None
            self.status_var = None
            self.channel_var = None
            self.uptime_var = None
            self.latency_var = None
            self.quality_var = None
            self.memory_var = None
            self.memory_bar = None
            self.voice_channel_var = None
            self.voice_channel_label = None
            self.status_indicator = None
            self.status_light = None
            self.status_label = None
            self.channel_label = None
            self.quality_label = None
            
            # Clear button references
            self.mute_button = None
            self.join_button = None
            self.leave_button = None
            self.test_led_button = None
            self.debug_checkbox = None
            self.disconnect_button = None
            self.reconnect_button = None
            
            # Clear callback references
            self.on_mute_toggle = None
            self.on_join_channel = None
            self.on_leave_channel = None
            self.on_led_test = None
            self.on_debug_toggle = None
            self.on_test_announcement = None
            self.on_disconnect = None
            self.on_reconnect = None
            self.on_chat_message = None
            
            self.debug_print("Status window cleanup completed")
            
        except Exception as e:
            self.debug_print(f"Error during status window cleanup: {e}")
        finally:
            # Restore the original exception handler
            sys.excepthook = original_excepthook
            
            # Ensure all UI elements are cleared
            if hasattr(self, 'root'):
                self.root = None

    def _on_disconnect_click(self):
        """Handle disconnect button click"""
        if self.on_disconnect:
            self.on_disconnect()
            self.update_status("Disconnecting...")
            self.update_voice_channel("Not connected", False)
            
    def _on_reconnect_click(self):
        """Handle reconnect button click"""
        if self.on_reconnect:
            self.on_reconnect()
            self.update_status("Reconnecting...")
            
    def _on_exit(self):
        """Handle exit button click"""
        try:
            # Save window position
            if self.window and self.window.winfo_viewable():
                x = self.window.winfo_x()
                y = self.window.winfo_y()
                self.config['window_position'] = {'x': x, 'y': y}
                with open('config.json', 'w') as f:
                    json.dump(self.config, f, indent=4)
        except Exception as e:
            self.debug_print(f"Error saving window position on exit: {e}")
            
        # Call exit callback if exists
        if hasattr(self, 'on_exit'):
            self.command_queue.put(('callback', self.on_exit))
        else:
            self.window.quit()
            
    def _on_test_announcement(self):
        """Handle test announcement button"""
        if hasattr(self, 'on_test_announcement') and self.on_test_announcement:
            self.debug_print("Sending test announcement command")
            
            def test_announcement_with_error_handling():
                try:
                    # Call the callback
                    self.on_test_announcement()
                    # Show success message after a short delay to let the announcement process start
                    if hasattr(self, 'root'):
                        self.root.after(500, lambda: tkinter.messagebox.showinfo(
                            "Test Announcement", 
                            "Announcement test initiated. Please check logs for details."
                        ))
                except Exception as e:
                    error_msg = f"Error testing announcement: {str(e)}"
                    self.debug_print(error_msg)
                    if hasattr(self, 'root'):
                        self.root.after(0, lambda: tkinter.messagebox.showerror(
                            "Announcement Error", 
                            error_msg
                        ))
            
            # Add to command queue
            self.command_queue.put(('callback', test_announcement_with_error_handling))
            
    def _on_chat_send(self):
        """Handle chat send button click"""
        message = self.chat_input.get().strip()
        if not message:
            return
            
        # Clear input
        self.chat_input.delete(0, tk.END)
        
        # Add user message to chat
        self.add_to_chat("You", message)
        
        # Send to callback if exists
        if hasattr(self, 'on_chat_message') and self.on_chat_message:
            try:
                # The issue is here - the lambda captures reference to message which might change
                # Create a fixed value to avoid capturing a changing reference
                msg = message  # Create a copy to ensure the value is fixed
                self.command_queue.put(('callback', lambda: self.on_chat_message('chat_message', msg)))
            except Exception as e:
                self.debug_print(f"Error sending chat message: {e}")
                self.add_to_chat("System", f"Error sending message: {e}")
        
    def add_to_chat(self, sender: str, message: str):
        """Add a message to the chat display"""
        self.debug_print(f"Adding message to chat from {sender}: {message[:50]}...")
        
        if not hasattr(self, 'chat_text') or not self.chat_text:
            self.debug_print("Error: chat_text widget does not exist")
            return
            
        try:
            self.debug_print("Configuring chat text widget")
            self.chat_text.configure(state='normal')
            
            # Add timestamp
            timestamp = datetime.datetime.now().strftime("%H:%M:%S")
            
            # Format message with different colors for different senders
            if sender.lower() == "system":
                color = "red"
            elif sender.lower() == "you":
                color = "cyan"
            elif sender.lower() == "assistant":
                color = "green"
            else:
                color = "white"
                
            # Insert with color
            self.chat_text.insert(tk.END, f"[{timestamp}] ", "timestamp")
            self.chat_text.insert(tk.END, f"{sender}: ", f"sender_{color}")
            self.chat_text.insert(tk.END, f"{message}\n\n", f"message_{color}")
            
            # Configure tags
            self.chat_text.tag_config("timestamp", foreground="yellow")
            self.chat_text.tag_config(f"sender_{color}", foreground=color)
            self.chat_text.tag_config(f"message_{color}", foreground=color)
            
            # Keep only last 1000 lines
            if float(self.chat_text.index('end-1c').split('.')[0]) > 1000:
                self.chat_text.delete('1.0', '2.0')
                
            # Scroll to bottom
            self.chat_text.see(tk.END)
            
            # Make read-only again
            self.chat_text.configure(state='disabled')
            self.debug_print(f"Successfully added message from {sender} to chat")
            
        except Exception as e:
            self.debug_print(f"Error adding chat message: {e}")
            if hasattr(self, 'window') and self.window:
                try:
                    # Try to show error in a message box
                    tk.messagebox.showerror("Chat Error", f"Error adding message to chat: {e}")
                except:
                    pass

    def _create_announcement_config(self, parent):
        """Create the announcement configuration section"""
        frame = ttk.LabelFrame(parent, text="Announcement Configuration")
        frame.pack(fill='x', pady=5)
        
        content = ttk.Frame(frame)
        content.pack(padx=5, pady=5, fill='x')
        
        # Enable checkbox
        self.announce_var = tk.BooleanVar(value=self.config.get('announcement_enabled', False))
        ttk.Checkbutton(content, text="Enable Announcement",
                       variable=self.announce_var).pack(anchor='w')
        
        # Time configuration
        time_frame = ttk.Frame(content)
        time_frame.pack(fill='x', pady=5)
        
        # Day selection
        day_frame = ttk.Frame(time_frame)
        day_frame.pack(fill='x')
        
        ttk.Label(day_frame, text="Day:").pack(side='left')
        self.day_var = tk.StringVar(value=str(self.config.get('announcement_day', 4)))
        day_combo = ttk.Combobox(day_frame, textvariable=self.day_var, width=15)
        day_combo['values'] = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']
        day_combo.current(int(self.day_var.get()) - 1)
        day_combo.pack(side='left', padx=5)
        
        # Time selection
        time_frame = ttk.Frame(content)
        time_frame.pack(fill='x')
        
        ttk.Label(time_frame, text="Time:").pack(side='left')
        
        # Hour spinbox
        self.hour_var = tk.StringVar(value=str(self.config.get('announcement_hour', 19)))
        hour_spin = ttk.Spinbox(time_frame, from_=0, to=23, width=3,
                               textvariable=self.hour_var)
        hour_spin.pack(side='left', padx=2)
        
        ttk.Label(time_frame, text=":").pack(side='left')
        
        # Minute spinbox
        self.minute_var = tk.StringVar(value=str(self.config.get('announcement_minute', 0)))
        minute_spin = ttk.Spinbox(time_frame, from_=0, to=59, width=3,
                                 textvariable=self.minute_var)
        minute_spin.pack(side='left', padx=2)
        
        # Button frame
        button_frame = ttk.Frame(content)
        button_frame.pack(fill='x', pady=(10, 0))
        
        # Test announcement button
        ttk.Button(button_frame, text="Test Announcement",
                  command=self._on_test_announcement).pack(side='left', padx=2)
        
        # Save button
        ttk.Button(button_frame, text="Save",
                  command=self._on_announcement_save).pack(side='right')
        
        return frame

    def _create_led_tab(self, parent):
        """Create the LED configuration tab"""
        # Main frame with padding
        main_frame = ttk.Frame(parent, padding=10)
        main_frame.pack(fill='both', expand=True)
        
        # Enable/Disable frame
        enable_frame = ttk.LabelFrame(main_frame, text="LED Control", padding="5")
        enable_frame.pack(fill='x', pady=(0, 10))
        
        self.led_enabled_var = tk.BooleanVar(value=self.config.get('led_enabled', True))
        ttk.Checkbutton(enable_frame, text="Enable LED Indicators",
                       variable=self.led_enabled_var,
                       command=self._on_led_toggle).pack(anchor='w')
        
        # LED Controls frame
        controls_frame = ttk.LabelFrame(main_frame, text="LED Controls", padding="5")
        controls_frame.pack(fill='x', pady=(0, 10))
        
        # Color settings
        color_settings = [
            ("Target Voice", "target_voice", "Default Red", (255, 0, 0)),
            ("Other Voice", "other_voice", "Default Blue", (0, 0, 255)),
            ("Hotkey", "hotkey", "Default Red", (60, 0, 0)),
            ("Notification", "notification", "Default Yellow", (255, 204, 0)),
            ("GPT Activity", "gpt_activity", "Default Purple", (128, 0, 128)),
            ("Power On", "power_on", "Default Green", (0, 100, 0))
        ]
        
        for row, (label, key, default_text, default_color) in enumerate(color_settings):
            frame = ttk.Frame(controls_frame)
            frame.pack(fill='x', pady=2)
            
            # Test button
            test_btn = ttk.Button(frame, text="Test",
                                command=lambda k=key: self._test_led_color(k))
            test_btn.pack(side='left', padx=2)
            
            # Configure button
            config_btn = ttk.Button(frame, text="Configure",
                                  command=lambda k=key, l=label: self._configure_led_color(k, l))
            config_btn.pack(side='left', padx=2)
            
            # Label with default color
            ttk.Label(frame, text=f"{label} ({default_text})").pack(side='left', padx=5)
        
        # Bottom buttons
        button_frame = ttk.Frame(main_frame)
        button_frame.pack(fill='x', pady=10)
        
        # Close button
        ttk.Button(button_frame, text="Close",
                  command=lambda: self.notebook.select(0)).pack(side='right', padx=5)
        
    def _test_led_color(self, key: str):
        """Test a specific LED color"""
        if not self.config.get('led_enabled', False):
            self.debug_print("LED is disabled")
            return
            
        try:
            color = self.config.get('led_colors', {}).get(key, {})
            if hasattr(self, 'on_led_test'):
                self.command_queue.put(('callback', lambda: self.on_led_test(key, color)))
        except Exception as e:
            self.debug_print(f"Error testing LED color: {e}")
            
    def _configure_led_color(self, key: str, label: str):
        """Open color configuration window for a specific LED setting"""
        try:
            from .led_config import LEDColorConfig
            
            # Get current color
            current_color = self.config.get('led_colors', {}).get(key, {})
            
            def save_color(new_color):
                """Save the new color configuration"""
                if 'led_colors' not in self.config:
                    self.config['led_colors'] = {}
                self.config['led_colors'][key] = new_color
                # Save to file
                try:
                    with open('config.json', 'w') as f:
                        json.dump(self.config, f, indent=4)
                    self.debug_print(f"Saved new color for {key}")
                except Exception as e:
                    self.debug_print(f"Error saving config: {e}")
                    
            def test_color(color):
                """Test the current color configuration"""
                if hasattr(self, 'on_led_test'):
                    self.command_queue.put(('callback', lambda: self.on_led_test(key, color)))
                    
            # Create color config window
            LEDColorConfig(self.window, f"{label} Color", current_color,
                         save_callback=save_color,
                         test_callback=test_color)
                         
        except Exception as e:
            self.debug_print(f"Error opening color config: {e}")

    def _on_led_toggle(self):
        """Handle LED enable/disable toggle"""
        enabled = self.led_enabled_var.get()
        self.config['led_enabled'] = enabled
        if hasattr(self, 'on_led_toggle'):
            self.command_queue.put(('callback', lambda: self.on_led_toggle(enabled)))
            
    def _on_announcement_save(self):
        """Handle announcement configuration save"""
        try:
            # Save announcement settings to config
            self.config['announcement_enabled'] = self.announce_var.get()
            self.config['announcement_day'] = self.day_var.get()
            self.config['announcement_hour'] = int(self.hour_var.get())
            self.config['announcement_minute'] = int(self.minute_var.get())
            
            # Save config to file
            with open('config.json', 'w') as f:
                json.dump(self.config, f, indent=4)
            self.debug_print("Announcement settings saved to file")
            
        except Exception as e:
            self.debug_print(f"Error saving announcement settings: {e}")

    def run(self):
        """Start the main event loop"""
        self.debug_print("Starting main event loop")
        try:
            self.root.mainloop()
        except Exception as e:
            self.debug_print(f"Error in main loop: {e}") 

    def update_voice_channel(self, status: str, is_connected: bool = False):
        """Update the voice channel status"""
        try:
            if not hasattr(self, 'root') or not self.root:
                # Store for later application
                if not hasattr(self, '_pending_voice_updates'):
                    self._pending_voice_updates = []
                self._pending_voice_updates.append((status, is_connected))
                return
                
            def update():
                try:
                    # Reset connection start time if we're newly connected
                    if is_connected and not self.is_connected:
                        self.start_time = datetime.datetime.now()
                        self.uptime_var.set("00:00:00")
                    
                    if hasattr(self, 'voice_channel_var') and self.voice_channel_var:
                        self.voice_channel_var.set(status)
                        
                    if hasattr(self, 'voice_channel_label') and self.voice_channel_label:
                        try:
                            self.voice_channel_label.configure(foreground='green' if is_connected else 'red')
                        except Exception as e:
                            if "'NoneType' object has no attribute 'configure'" not in str(e):
                                self.debug_print(f"Error configuring voice channel label: {e}")
                                
                    if hasattr(self, 'quality_var') and self.quality_var:
                        self.quality_var.set("Good" if is_connected else "Disconnected")
                        
                    if hasattr(self, 'quality_label') and self.quality_label:
                        try:
                            self.quality_label.configure(foreground='green' if is_connected else 'red')
                        except Exception as e:
                            if "'NoneType' object has no attribute 'configure'" not in str(e):
                                self.debug_print(f"Error configuring quality label: {e}")
                    
                    # If disconnecting, also reset the latency
                    if not is_connected and hasattr(self, 'latency_var'):
                        self.latency_var.set("N/A")
                        self.debug_print("Reset latency display to N/A on disconnect")
                    
                    # Also synchronize the status information
                    if is_connected:
                        if hasattr(self, 'status_var') and self.status_var:
                            self.status_var.set("Connected to Discord")
                            
                        if hasattr(self, 'status_label') and self.status_label:
                            try:
                                self.status_label.configure(foreground='green')
                            except Exception as e:
                                if "'NoneType' object has no attribute 'configure'" not in str(e):
                                    self.debug_print(f"Error configuring status label: {e}")
                                    
                        self._update_status_indicator('green')
                        self.is_connected = True
                        
                        # Start connection monitor if not already running
                        self._update_connection_monitor()
                    else:
                        self.is_connected = False
                        
                except Exception as e:
                    self.debug_print(f"Error in voice channel update: {e}")
                    
            self.root.after(0, update)
            
        except Exception as e:
            self.debug_print(f"Error scheduling voice channel update: {e}")

    def show_error(self, title: str, message: str):
        """Show an error popup message"""
        if hasattr(self, 'root'):
            self.root.after(0, lambda: tkinter.messagebox.showerror(title, message))
            
    def show_info(self, title: str, message: str):
        """Show an info popup message"""
        if hasattr(self, 'root'):
            self.root.after(0, lambda: tkinter.messagebox.showinfo(title, message))

    def _on_window_move(self, event):
        """Handle window move event"""
        if event.widget == self.window:
            # Only save position if window is fully visible
            if self.window.winfo_viewable():
                x = self.window.winfo_x()
                y = self.window.winfo_y()
                self.config['window_position'] = {'x': x, 'y': y}
                # Save to file
                try:
                    with open('config.json', 'w') as f:
                        json.dump(self.config, f, indent=4)
                except Exception as e:
                    self.debug_print(f"Error saving window position: {e}")
                    
    def _update_connection_monitor(self):
        """Update connection monitor stats"""
        if not self.is_connected:
            return
            
        try:
            # Update uptime
            if hasattr(self, 'start_time') and hasattr(self, 'uptime_var'):
                uptime = datetime.datetime.now() - self.start_time
                hours = int(uptime.total_seconds() // 3600)
                minutes = int((uptime.total_seconds() % 3600) // 60)
                seconds = int(uptime.total_seconds() % 60)
                self.uptime_var.set(f"{hours:02d}:{minutes:02d}:{seconds:02d}")
            
            # Only update latency if we don't have actual measurements yet
            if hasattr(self, 'latency_var') and self.latency_var.get() == "N/A":
                self.latency_var.set("Measuring...")
        except Exception as e:
            if "'NoneType' object has no attribute" not in str(e):
                self.debug_print(f"Error updating connection monitor: {e}")
        
        # Schedule next update (every second to keep timer accurate)
        if self.root and self.is_connected:
            self.root.after(1000, self._update_connection_monitor)

    def _update_system_stats(self):
        """Update system statistics periodically"""
        try:
            process = psutil.Process()
            
            # Get memory usage
            memory_mb = process.memory_info().rss / 1024 / 1024
            
            # Update memory stats
            if hasattr(self, 'memory_var') and self.memory_var:
                self.memory_var.set(f"Memory Usage: {memory_mb:.1f} MB")
            
            if hasattr(self, 'memory_bar') and self.memory_bar:
                try:
                    # Update progress bar - max 1024MB for scaling
                    value = min(memory_mb / 1024 * 100, 100)
                    self.memory_bar['value'] = value
                except (AttributeError, TypeError):
                    # Silently ignore attribute errors for memory_bar
                    pass
            
            # Update connection status if connected to properly keep UI in sync
            if hasattr(self, 'voice_channel_var') and self.voice_channel_var and self.voice_channel_var.get() != "Not connected":
                if hasattr(self, 'is_connected'):
                    self.is_connected = True
                    
                    if hasattr(self, 'status_var') and self.status_var and "initializing" in self.status_var.get().lower():
                        self.status_var.set("Connected to Discord")
                        if hasattr(self, 'status_label') and self.status_label:
                            try:
                                self.status_label.configure(foreground='green')
                            except (AttributeError, TypeError):
                                # Silently ignore attribute errors
                                pass
                        self._update_status_indicator('green')
                    
                    if hasattr(self, 'quality_var') and self.quality_var:
                        self.quality_var.set("Good")
                        if hasattr(self, 'quality_label') and self.quality_label:
                            try:
                                self.quality_label.configure(foreground='green')
                            except (AttributeError, TypeError):
                                # Silently ignore attribute errors
                                pass
            
            # Schedule next update every 10 seconds (reduced from 2 seconds)
            if self.root:
                self.root.after(10000, self._update_system_stats)
                
        except Exception as e:
            # Only log errors that aren't related to NoneType objects having no attributes
            if "'NoneType' object has no attribute" not in str(e):
                self.debug_print(f"Error updating system stats: {e}")
            # Reschedule even on error
            if self.root:
                self.root.after(10000, self._update_system_stats)

    def _process_status_update(self, status: str):
        """Process a single status update"""
        try:
            self.debug_print(f"Processing status update: {status[:50]}...")
            
            # Check if this is a chat response
            if status.startswith("chat_response:"):
                try:
                    response = status.split(":", 1)[1]  # Split only on first colon
                    self.debug_print(f"Extracted chat response: {response[:50]}...")
                    
                    if response.startswith("Error:"):
                        self.debug_print("Adding error message to chat")
                        self.add_to_chat("System", response)
                    else:
                        self.debug_print("Adding assistant response to chat")
                        self.add_to_chat("Assistant", response)
                    return
                except (ValueError, IndexError) as e:
                    self.debug_print(f"Error parsing chat response: {e}")
                    # Add error message to chat
                    self.add_to_chat("System", f"Error processing response: {e}")
                    return
                except Exception as e:
                    self.debug_print(f"Unexpected error in chat response handling: {e}")
                    self.add_to_chat("System", f"Error: {str(e)}")
                    return

            # For all other status updates, use the main update_status method
            self.update_status(status)

        except Exception as e:
            self.debug_print(f"Error processing status update: {e}")
            try:
                # Try to display error in chat for visibility
                self.add_to_chat("System", f"Error in status update: {e}")
            except:
                pass

    def update_latency(self, latency_ms):
        """Update the latency display with actual measurements"""
        if not hasattr(self, 'root') or not self.root:
            return
            
        def update():
            try:
                if hasattr(self, 'latency_var') and self.latency_var:
                    # Format latency nicely
                    if latency_ms > 0:
                        self.latency_var.set(f"{latency_ms} ms")
                    elif latency_ms == 0 and not self.is_connected:
                        # Special case for disconnected state
                        self.latency_var.set("N/A")
                        # Also ensure quality shows disconnected
                        if hasattr(self, 'quality_var') and self.quality_var:
                            self.quality_var.set("Disconnected")
                        if hasattr(self, 'quality_label') and self.quality_label:
                            try:
                                self.quality_label.configure(foreground='red')
                            except Exception as e:
                                if "'NoneType' object has no attribute 'configure'" not in str(e):
                                    self.debug_print(f"Error configuring quality label: {e}")
                    else:
                        self.latency_var.set("Measuring...")
            except Exception as e:
                if "'NoneType' object has no attribute" not in str(e):
                    self.debug_print(f"Error updating latency: {e}")
                    
        self.root.after(0, update) 