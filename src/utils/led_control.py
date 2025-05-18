from blinkstick import blinkstick
import time
import logging
from typing import Optional, Dict, Any, Callable

class LEDController:
    def __init__(self, debug_print_func: Callable = print):
        self.bs = None
        self.debug_print = debug_print_func
        self.target_serial = "BS061825-3.0"  # Target BlinkStick serial
        
    def _log_warning(self, msg: str):
        """Log a message with WARNING level"""
        # Log to standard logging system
        logging.warning(msg)
        
        # Also log to the debug_print function (which will display in the UI)
        if self.debug_print:
            self.debug_print(f"WARNING: {msg}")
        
    def initialize(self) -> bool:
        """Initialize the BlinkStick"""
        try:
            # Find all connected BlinkSticks
            all_sticks = blinkstick.find_all()
            if not all_sticks:
                self.debug_print("No BlinkStick devices found. Please check USB connection.")
                return False
            
            # Look for the specific BlinkStick
            for stick in all_sticks:
                try:
                    serial = stick.get_serial()
                    if serial == self.target_serial:
                        self.bs = stick
                        # Validate device is responsive
                        if not self.bs.get_description():
                            self.debug_print(f"Found target BlinkStick {self.target_serial} but device is not responding")
                            continue
                        self.debug_print(f"Found and validated target BlinkStick: {self.target_serial}")
                        # Test LED functionality
                        self.set_color(0, 0, 0, 255, 0)
                        time.sleep(0.1)
                        self.turn_off()
                        return True
                except Exception as e:
                    self.debug_print(f"Error checking BlinkStick {serial}: {str(e)}")
                    continue
            
            # Use WARNING level for missing target device
            self._log_warning(f"Target BlinkStick {self.target_serial} not found")
            # Fallback to first available if target not found
            for stick in all_sticks:
                try:
                    self.bs = stick
                    if self.bs.get_description():  # Validate device is responsive
                        self.debug_print(f"Using fallback BlinkStick: {self.bs.get_serial()}")
                        # Test LED functionality
                        self.set_color(0, 0, 0, 255, 0)
                        time.sleep(0.1)
                        self.turn_off()
                        return True
                except Exception as e:
                    self.debug_print(f"Error with fallback device: {str(e)}")
                    continue
            
            self._log_warning("No responsive BlinkStick devices found")
            return False
            
        except Exception as e:
            self._log_warning(f"Error initializing BlinkStick: {str(e)}")
            return False
            
    def set_color(self, channel: int, index: int, red: int, green: int, blue: int) -> bool:
        """Set LED color with error handling and retries"""
        max_retries = 3
        for attempt in range(max_retries):
            try:
                if self.bs is None or not self.bs.get_description():  # Check if BlinkStick is responsive
                    self.initialize()
                if self.bs:
                    self.bs.set_color(channel=channel, index=index, red=red, green=green, blue=blue)
                    self.debug_print(f"LED set: channel={channel}, index={index}, RGB=({red},{green},{blue})")
                    return True
            except Exception as e:
                self.debug_print(f"Attempt {attempt + 1}: Error setting LED color: {e}")
                self.initialize()  # Try to reinitialize
        return False
        
    def turn_off(self, channel: int = 0, index: Optional[int] = None) -> None:
        """Turn off LED(s)"""
        try:
            if index is not None:
                # Turn off specific LED
                self.set_color(channel, index, 0, 0, 0)
            else:
                # Turn off all LEDs
                for i in range(8):
                    self.set_color(channel, i, 0, 0, 0)
        except Exception as e:
            self.debug_print(f"Error turning off LED(s): {e}")
            
    async def cleanup(self) -> None:
        """Clean up resources"""
        try:
            if self.bs:
                # Turn off all LEDs
                self.turn_off()
                # Clear the reference
                self.bs = None
        except Exception as e:
            self.debug_print(f"Error during LED cleanup: {e}")
        finally:
            # Ensure bs is None even if there was an error
            self.bs = None

    def test_sequence(self, config: Dict[str, Any]):
        """Run a test sequence showing all configured colors"""
        if not config.get('led_enabled', False):
            self.debug_print("LED is disabled in config")
            return

        try:
            # Get LED colors from config
            led_colors = config.get('led_colors', {})
            
            # Test each color for 0.5 seconds
            for key, color in led_colors.items():
                self.debug_print(f"Testing {key} color")
                self.set_color(0, 0, 
                             color.get('red', 0),
                             color.get('green', 0),
                             color.get('blue', 0))
                time.sleep(0.5)
                
            # Turn off at the end
            self.turn_off()
            
        except Exception as e:
            self.debug_print(f"Error running LED test sequence: {e}")
            self.turn_off() 