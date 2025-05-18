import tkinter as tk
from tkinter import ttk
import datetime
from typing import Callable, List, Tuple, Optional
from ..audio.playback import AudioPlayer

class ChatWindow(ttk.Frame):
    def __init__(self, parent, audio_player: AudioPlayer, 
                 gpt_callback: Callable[[str], str],
                 debug_print_func: Callable = print):
        super().__init__(parent, padding="5")
        self.audio_player = audio_player
        self.gpt_callback = gpt_callback
        self.debug_print = debug_print_func
        self.chat_history: List[Tuple[str, str, str]] = []  # (sender, message, timestamp)
        
        self._create_widgets()
        
    def _create_widgets(self):
        """Create the chat interface widgets"""
        # Create chat history display
        chat_scroll = ttk.Scrollbar(self)
        chat_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        
        self.chat_text = tk.Text(self, wrap=tk.WORD, yscrollcommand=chat_scroll.set,
                               bg='black', fg='white', font=('Consolas', 10))
        self.chat_text.pack(fill=tk.BOTH, expand=True)
        
        chat_scroll.config(command=self.chat_text.yview)
        
        # Make chat text read-only
        self.chat_text.configure(state='disabled')
        
        # Create input area
        input_frame = ttk.Frame(self)
        input_frame.pack(fill=tk.X, pady=5)
        
        self.chat_input = ttk.Entry(input_frame)
        self.chat_input.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=5)
        
        self.send_button = ttk.Button(input_frame, text="Send", 
                                    command=self._on_send_click)
        self.send_button.pack(side=tk.RIGHT, padx=5)
        
        # Bind Enter key to send message
        self.chat_input.bind('<Return>', lambda e: self._on_send_click())
        
    def _on_send_click(self):
        """Handle send button click"""
        message = self.chat_input.get().strip()
        if not message:
            return
            
        # Clear input
        self.chat_input.delete(0, tk.END)
        
        # Add user message to chat
        self.add_to_chat("You", message)
        
        # Get response from GPT (this should be handled asynchronously)
        response = self.gpt_callback(message)
        
        # Add GPT response to chat
        self.add_to_chat("GPT", response)
        
        # Play response through voice
        self.audio_player.play_text(response)
        
    def add_to_chat(self, sender: str, message: str):
        """Add a message to the chat display"""
        self.chat_text.configure(state='normal')
        
        # Add timestamp
        timestamp = datetime.datetime.now().strftime("%H:%M:%S")
        
        # Format message
        formatted_message = f"[{timestamp}] {sender}: {message}\n\n"
        
        # Add to chat history
        self.chat_history.append((sender, message, timestamp))
        
        # Add to display
        self.chat_text.insert(tk.END, formatted_message)
        
        # Keep only last 1000 lines
        if float(self.chat_text.index('end-1c').split('.')[0]) > 1000:
            self.chat_text.delete('1.0', '2.0')
            
        # Scroll to bottom
        self.chat_text.see(tk.END)
        
        # Make read-only again
        self.chat_text.configure(state='disabled') 