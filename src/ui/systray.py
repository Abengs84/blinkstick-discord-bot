import pystray
from PIL import Image
import os
from typing import Callable, Optional, Dict, Any
import threading
import asyncio
import keyboard
from functools import partial
from .status_window import StatusWindow

class SystrayManager:
    def __init__(self, config: Dict[str, Any], quit_callback: Callable, 
                 toggle_mute_callback: Callable, debug_print_func: Callable = print):
        self.config = config
        self.quit_callback = quit_callback
        self.toggle_mute_callback = toggle_mute_callback
        self._original_debug_print = debug_print_func
        # Use our custom wrapper around the debug_print function
        self.debug_print = self._filtered_debug_print
        self.icon: Optional[pystray.Icon] = None
        self.is_muted = False
        self.monitoring_active = False
        self.monitoring_after_id = None
        self.status_window = StatusWindow(config, self._filtered_debug_print)
        self.hotkey = None
        self.callbacks = {}
        
        # Filtered log message patterns
        self._filtered_log_patterns = [
            "Retrying channel update:", 
            "Received channel update:", 
            "Queuing channel update for after window initialization",
            "Applying pending channel update:"
        ]
        
        # Set up status window callbacks
        self.status_window.set_callbacks(
            mute_toggle=self._on_mute_toggle,
            join_channel=self._on_join_channel,
            leave_channel=self._on_leave_channel,
            led_test=self._on_led_test,
            debug_toggle=self._on_debug_toggle,
            test_announcement=None,
            disconnect=self._on_disconnect,
            reconnect=self._on_reconnect
        )
        
        # Set additional callbacks
        self.status_window.on_exit = self._on_quit
        self.status_window.on_announcement_toggle = self._on_announcement_toggle
        
        self._setup_icon()
        self._setup_hotkey()
        
        # Start system monitoring
        self._start_monitoring()
        
    def _filtered_debug_print(self, message):
        """Filter certain verbose debug messages"""
        # Skip filtered messages unless debug mode is on
        if not self.config.get('debug_mode', False):
            for pattern in self._filtered_log_patterns:
                if pattern in message:
                    return  # Skip logging this message
        
        # Pass through to original debug print function
        self._original_debug_print(message)
        
    def _setup_icon(self):
        """Setup the system tray icon"""
        try:
            # Try to load the icon file
            icon_path = os.path.join(os.path.dirname(__file__), '..', '..', 'assets', 'led.ico')
            png_path = os.path.join(os.path.dirname(__file__), '..', '..', 'assets', 'led.png')
            
            self.debug_print(f"Loading icon from: {icon_path}")
            self.debug_print(f"Loading PNG icon from: {png_path}")
            
            if os.path.exists(icon_path):
                icon_image = Image.open(icon_path)
                self.debug_print("Successfully loaded ICO icon")
            elif os.path.exists(png_path):
                icon_image = Image.open(png_path)
                self.debug_print("Successfully loaded PNG icon")
            else:
                # Create a simple colored square as fallback
                self.debug_print("Icon files not found, using fallback colored square")
                icon_image = Image.new('RGB', (64, 64), color = 'red')
                
            # Create the icon menu
            menu = (
                pystray.MenuItem("Show Status", self._on_status_toggle),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem("Quit", self._on_quit)
            )
            
            # Create the icon
            self.icon = pystray.Icon(
                "Discord Voice Assistant",
                icon_image,
                "Discord Voice Assistant",
                menu
            )
            
            self.debug_print("System tray icon created successfully")
            
        except Exception as e:
            self.debug_print(f"Error setting up system tray icon: {e}")
            
    def _setup_hotkey(self):
        """Setup the mute toggle hotkey"""
        try:
            hotkey = self.config.get('hotkey', 'ctrl+shift+alt+o')
            keyboard.add_hotkey(hotkey, self._on_mute_toggle)
            self.hotkey = hotkey
            self.debug_print(f"Hotkey {hotkey} registered successfully")
        except Exception as e:
            self.debug_print(f"Error setting up hotkey: {e}")
            
    def _on_status_toggle(self, _=None):
        """Toggle status window visibility"""
        try:
            if hasattr(self.status_window, 'window'):
                if not self.status_window.is_visible:
                    # Window is being shown
                    self.status_window.toggle()
                    # Start monitoring
                    self.monitoring_active = True
                    self._update_stats()
                    
                    # Apply any pending updates
                    self._apply_pending_updates()
                else:
                    # Window is being hidden
                    # Stop monitoring first
                    self.monitoring_active = False
                    if self.monitoring_after_id:
                        self.status_window.window.after_cancel(self.monitoring_after_id)
                        self.monitoring_after_id = None
                    # Then hide window
                    self.status_window.toggle()
                    
        except Exception as e:
            self.debug_print(f"Error toggling status window: {e}")
            
    def _on_mute_toggle(self, _=None):
        """Handle mute toggle from menu or hotkey"""
        self.is_muted = not self.is_muted
        self.debug_print(f"Mute toggled: {self.is_muted}")
        self.toggle_mute_callback(self.is_muted)
        if self.icon:
            self.icon.update_menu()
            
    def _on_quit(self):
        """Handle quit menu item"""
        try:
            # First cleanup the status window to ensure UI thread is handled properly
            if hasattr(self, 'status_window'):
                self.status_window.cleanup()
            
            # Then stop the icon
            if self.icon:
                self.icon.stop()
                
            # Call quit callback last
            if self.quit_callback:
                self.quit_callback()
                
        except Exception as e:
            self.debug_print(f"Error during quit: {e}")
            # Ensure quit callback is called even on error
            if self.quit_callback:
                self.quit_callback()
        
    def run(self):
        """Run the system tray icon in a separate thread"""
        if self.icon:
            self.systray_thread = threading.Thread(target=self.icon.run, daemon=True)
            self.systray_thread.start()
            self.debug_print("System tray icon is running")
        else:
            self.debug_print("System tray icon not available")
            
    def stop(self):
        """Stop the system tray icon"""
        try:
            if self.icon:
                # Remove hotkey first
                try:
                    keyboard.unhook_all()
                except Exception as e:
                    self.debug_print(f"Error unhooking keyboard: {e}")
                
                # Stop icon
                try:
                    self.icon.stop()
                except Exception as e:
                    self.debug_print(f"Error stopping icon: {e}")
                
                # Wait for systray thread if it exists and we're not in it
                if (hasattr(self, 'systray_thread') and 
                    threading.current_thread() != self.systray_thread):
                    try:
                        self.systray_thread.join(timeout=1.0)
                    except Exception as e:
                        self.debug_print(f"Error joining systray thread: {e}")
                
                self.debug_print("System tray icon stopped")
                
        except Exception as e:
            self.debug_print(f"Error stopping systray: {e}")
            
    def update_status(self, status: str):
        """Update status window"""
        self.status_window.update_status(status)
        
    def update_channel(self, channel: str):
        """Update channel info in status window"""
        try:
            # Queue the update for after window initialization
            if not hasattr(self.status_window, 'window') or not self.status_window.window:
                # Store for later application - only keep the latest update
                self._pending_channel_update = [channel]
                
                # Set a delayed task to retry applying the update
                def retry_update():
                    self.update_channel(channel)
                
                # Schedule retry in 1 second
                threading.Timer(1.0, retry_update).start()
                return
            
            # Update channel information in status window
            self.status_window.update_channel(channel)
            
            # If we have a channel, this implies a voice connection too
            if channel and channel != "Not connected":
                self.status_window.update_voice_channel(channel, is_connected=True)
                
        except Exception as e:
            self.debug_print(f"Error updating channel: {e}")
        
    def _on_join_channel(self):
        """Handle join channel button click"""
        self.debug_print("Join channel requested")
        # TODO: Implement channel joining
        
    def _on_leave_channel(self):
        """Handle leave channel button click"""
        self.debug_print("Leave channel requested")
        # TODO: Implement channel leaving
        
    def _on_led_test(self):
        """Handle LED test button click"""
        self.debug_print("LED test requested")
        # TODO: Implement LED test
        
    def _on_debug_toggle(self):
        """Handle debug mode toggle"""
        self.config['debug_mode'] = not self.config.get('debug_mode', False)
        self.debug_print(f"Debug mode: {self.config['debug_mode']}")
        if self.icon:
            self.icon.update_menu()
            
    def _start_monitoring(self):
        """Start system monitoring"""
        self.monitoring_active = False
        self.monitoring_after_id = None
        
    def _update_stats(self):
        """Update system stats if window is visible"""
        if not self.monitoring_active:
            return
            
        try:
            # Only update if window exists and is visible
            if (self.status_window and 
                hasattr(self.status_window, 'window') and 
                self.status_window.window and 
                self.status_window.window.winfo_exists() and
                self.status_window.is_visible):
                
                # Update system stats (memory only)
                import psutil
                process = psutil.Process()
                memory_mb = process.memory_info().rss / 1024 / 1024
                
                try:
                    self.status_window.update_system_stats(memory_mb)
                except Exception as e:
                    # Silently ignore errors from update_system_stats
                    if "'NoneType' object has no attribute" not in str(e):
                        self.debug_print(f"Error updating system stats: {e}")
                
                # Update connection stats
                if hasattr(self.status_window, 'update_connection_monitor'):
                    try:
                        self.status_window.update_connection_monitor()
                    except Exception as e:
                        # Silently ignore errors from connection monitor
                        if "'NoneType' object has no attribute" not in str(e):
                            self.debug_print(f"Error updating connection monitor: {e}")
                
                # Schedule next update if still monitoring
                if self.monitoring_active:
                    self.monitoring_after_id = self.status_window.window.after(1000, self._update_stats)
            
        except Exception as e:
            # Only log non-NoneType errors
            if "'NoneType' object has no attribute" not in str(e):
                self.debug_print(f"Error in system monitoring: {e}")
            
    def _on_disconnect(self):
        """Handle disconnect button"""
        self.debug_print("Disconnecting...")
        if 'on_disconnect' in self.callbacks:
            self.callbacks['on_disconnect']()
        
    def _on_reconnect(self):
        """Handle reconnect button"""
        self.debug_print("Reconnecting...")
        if 'on_reconnect' in self.callbacks:
            self.callbacks['on_reconnect']()
        
    def _on_announcement_toggle(self, enabled: bool, day: int, hour: int, minute: int):
        """Handle announcement toggle"""
        self.config['announcement_enabled'] = enabled
        self.config['announcement_day'] = day
        self.config['announcement_hour'] = hour
        self.config['announcement_minute'] = minute
        
        self.debug_print(
            f"Announcements {'enabled' if enabled else 'disabled'} "
            f"for {['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday'][day-1]} "
            f"at {hour:02d}:{minute:02d}"
        )
        
        # TODO: Save config and update announcement scheduler 

    def cleanup(self):
        """Cleanup all resources"""
        try:
            # First stop monitoring to prevent any new UI updates
            self.monitoring_active = False
            
            # Store status window reference before cleanup
            status_window = getattr(self, 'status_window', None)
            
            # Then cleanup status window if it exists
            if status_window:
                try:
                    status_window.cleanup()
                except Exception as e:
                    self.debug_print(f"Error cleaning up status window: {e}")
                finally:
                    # Remove reference to prevent further access
                    delattr(self, 'status_window')
            
            # Finally stop systray
            try:
                self.stop()
            except Exception as e:
                self.debug_print(f"Error stopping systray: {e}")
            
        except Exception as e:
            self.debug_print(f"Error during cleanup: {e}")

    def set_callbacks(self, **callbacks):
        """Set callbacks for various actions"""
        self.callbacks.update(callbacks)

    def _on_ptt_toggle(self):
        """Handle PTT toggle hotkey"""
        self.debug_print("PTT hotkey triggered")
        self.toggle_mute_callback(True)  # Call the callback to trigger PTT toggle 

    def _apply_pending_updates(self):
        """Apply any pending updates that were received before the window was ready"""
        try:
            # Apply pending channel updates if any
            if hasattr(self, '_pending_channel_update') and self._pending_channel_update:
                self.debug_print(f"Applying {len(self._pending_channel_update)} pending channel updates")
                for channel in self._pending_channel_update:
                    try:
                        self.debug_print(f"Applying pending channel update: {channel}")
                        self.status_window.update_channel(channel)
                        
                        # If we have a channel, this implies a voice connection too
                        if channel and channel != "Not connected":
                            self.status_window.update_voice_channel(channel, is_connected=True)
                    except Exception as e:
                        self.debug_print(f"Error applying pending channel update: {e}")
                
                # Clear pending updates
                self._pending_channel_update = []
        except Exception as e:
            self.debug_print(f"Error applying pending updates: {e}") 