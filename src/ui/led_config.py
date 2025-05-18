import tkinter as tk
from tkinter import ttk
from typing import Dict, Any, Callable

class LEDColorConfig:
    def __init__(self, parent, title: str, initial_color: Dict[str, int],
                 save_callback: Callable[[Dict[str, int]], None],
                 test_callback: Callable[[Dict[str, int]], None]):
        self.window = tk.Toplevel(parent)
        self.window.title(f"Configure {title}")
        self.window.resizable(False, False)
        self.window.transient(parent)  # Make it float on top of parent
        
        # Main frame with padding
        main_frame = ttk.Frame(self.window, padding=10)
        main_frame.pack(fill='both', expand=True)
        
        # RGB sliders
        self.red_var = tk.IntVar(value=initial_color.get('red', 0))
        self.green_var = tk.IntVar(value=initial_color.get('green', 0))
        self.blue_var = tk.IntVar(value=initial_color.get('blue', 0))
        
        # Red slider
        ttk.Label(main_frame, text="Red:").pack(anchor='w')
        ttk.Scale(main_frame, from_=0, to=255, orient='horizontal',
                 variable=self.red_var, command=self._on_color_change).pack(fill='x')
        
        # Green slider
        ttk.Label(main_frame, text="Green:").pack(anchor='w')
        ttk.Scale(main_frame, from_=0, to=255, orient='horizontal',
                 variable=self.green_var, command=self._on_color_change).pack(fill='x')
        
        # Blue slider
        ttk.Label(main_frame, text="Blue:").pack(anchor='w')
        ttk.Scale(main_frame, from_=0, to=255, orient='horizontal',
                 variable=self.blue_var, command=self._on_color_change).pack(fill='x')
        
        # Color preview
        self.preview = tk.Canvas(main_frame, width=200, height=50,
                               highlightthickness=1, highlightbackground='gray')
        self.preview.pack(fill='x', pady=10)
        self._update_preview()
        
        # Button frame
        button_frame = ttk.Frame(main_frame)
        button_frame.pack(fill='x', pady=(10,0))
        
        ttk.Button(button_frame, text="Save", command=self._on_save).pack(side='left', padx=5)
        ttk.Button(button_frame, text="Cancel", command=self.window.destroy).pack(side='left', padx=5)
        ttk.Button(button_frame, text="Test", command=self._on_test).pack(side='left', padx=5)
        
        # Store callbacks
        self.save_callback = save_callback
        self.test_callback = test_callback
        
        # Center window on parent
        self.window.geometry(f"+{parent.winfo_rootx() + 50}+{parent.winfo_rooty() + 50}")
        
    def _on_color_change(self, _=None):
        """Handle color slider changes"""
        self._update_preview()
        
    def _update_preview(self):
        """Update the color preview"""
        color = self._get_current_color_hex()
        self.preview.delete('all')
        self.preview.create_rectangle(0, 0, 200, 50, fill=color, outline='')
        
    def _get_current_color(self) -> Dict[str, int]:
        """Get current RGB values"""
        return {
            'red': self.red_var.get(),
            'green': self.green_var.get(),
            'blue': self.blue_var.get()
        }
        
    def _get_current_color_hex(self) -> str:
        """Get current color as hex string"""
        color = self._get_current_color()
        return f"#{color['red']:02x}{color['green']:02x}{color['blue']:02x}"
        
    def _on_save(self):
        """Handle save button click"""
        if self.save_callback:
            self.save_callback(self._get_current_color())
        self.window.destroy()
        
    def _on_test(self):
        """Handle test button click"""
        if self.test_callback:
            self.test_callback(self._get_current_color()) 