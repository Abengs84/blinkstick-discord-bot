import os
import sys
import json
import asyncio
import logging
import time
import threading
from typing import Dict, Any, Optional
from pathlib import Path
import queue

# Add the src directory to Python path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Global variables
bot = None
systray = None

from src.bot.discord_bot import DiscordBot
from src.ui.systray import SystrayManager

# Custom log filter to suppress noisy messages
class SuppressFilter(logging.Filter):
    def __init__(self):
        super().__init__()
        self.suppressed_messages = [
            "Retrying channel update:",
            "Received channel update:",
            "Queuing channel update for after window initialization",
            "Applying pending channel update:",
            "Found active connection in channel"
        ]
    
    def filter(self, record):
        msg = record.getMessage()
        for pattern in self.suppressed_messages:
            if pattern in msg:
                return False  # Suppress this message
        return True  # Allow all other messages

def setup_logging() -> None:
    """Configure logging"""
    # Create our filter
    suppress_filter = SuppressFilter()
    
    # Configure handlers with the filter
    stdout_handler = logging.StreamHandler(sys.stdout)
    stdout_handler.addFilter(suppress_filter)
    
    file_handler = logging.FileHandler('bot.log')
    file_handler.addFilter(suppress_filter)
    
    # Configure logging with our filtered handlers
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        handlers=[stdout_handler, file_handler]
    )
    
    # Also add the filter to the root logger for good measure
    root_logger = logging.getLogger()
    root_logger.addFilter(suppress_filter)

def load_config(config_path: str = 'config.json') -> Dict[str, Any]:
    """Load configuration from JSON file"""
    try:
        with open(config_path, 'r') as f:
            config = json.load(f)
            
        # Validate required fields
        required_fields = ['discord_token', 'openai_api_key', 'target_user']
        missing_fields = [field for field in required_fields if field not in config]
        
        if missing_fields:
            raise ValueError(f"Missing required config fields: {', '.join(missing_fields)}")
            
        return config
        
    except FileNotFoundError:
        print(f"Config file not found at {config_path}")
        # Create example config
        example_config = {
            "discord_token": "YOUR_DISCORD_BOT_TOKEN",
            "openai_api_key": "YOUR_OPENAI_API_KEY",
            "target_user": "username#1234",
            "announcement_enabled": True,
            "announcement_day": 4,  # Friday
            "announcement_hour": 19,
            "announcement_minute": 0,
            "listen_all_users": False,
            "debug_mode": True,
            "led_enabled": False,
            "hotkey": "ctrl+shift+alt+o",
            "window_position": {
                "x": 100,
                "y": 100
            },
            "led_colors": {
                "target_voice": {"red": 255, "green": 0, "blue": 0},
                "other_voice": {"red": 0, "green": 0, "blue": 255},
                "hotkey": {"red": 60, "green": 0, "blue": 0},
                "notification": {"red": 255, "green": 204, "blue": 0},
                "gpt_activity": {"red": 128, "green": 0, "blue": 128},
                "power_on": {"red": 0, "green": 100, "blue": 0}
            }
        }
        
        with open(config_path, 'w') as f:
            json.dump(example_config, f, indent=4)
            
        print(f"Created example config at {config_path}")
        print("Please fill in your Discord token and OpenAI API key")
        sys.exit(1)
        
    except json.JSONDecodeError:
        print(f"Error parsing config file: {config_path}")
        print("Please ensure it is valid JSON")
        sys.exit(1)
        
    except Exception as e:
        print(f"Error loading config: {str(e)}")
        sys.exit(1)

def toggle_mute_callback(is_muted):
    """Callback for mute toggle"""
    global bot
    if bot is None:
        logging.error("Bot not initialized when PTT toggle triggered")
        return
        
    debug_print(f"PTT toggle triggered")
    try:
        bot.on_ptt_hotkey()  # Use the new PTT hotkey handler
    except Exception as e:
        debug_print(f"Error in PTT toggle: {e}")

async def cleanup():
    """Clean up resources"""
    global bot, systray
    
    try:
        logging.info("Starting main cleanup sequence")
        
        # First, cleanup the systray manager
        if systray:
            try:
                # Make a local copy of the status window reference
                status_window = getattr(systray, 'status_window', None)
                
                # Stop the systray first to prevent new UI events
                systray.stop()
                logging.info("System tray icon stopped")
                
                # Then cleanup the status window if it exists
                if status_window:
                    try:
                        logging.info("Starting UI cleanup...")
                        # Call cleanup directly instead of incremental actions
                        status_window.cleanup()
                        logging.info("UI cleanup completed")
                    except Exception as e:
                        logging.error(f"Error during status window cleanup: {e}")
            except Exception as e:
                logging.error(f"Error during systray cleanup: {e}")
        
        # Start bot cleanup but don't wait for completion
        logging.info("Starting bot cleanup...")
        if bot:
            try:
                # Clear voice state handlers first to prevent callbacks during disconnect
                if hasattr(bot, '_voice_clients'):
                    for guild_id, voice_client in list(bot._voice_clients.items()):
                        try:
                            if hasattr(voice_client, '_voice_state_update_handler'):
                                voice_client._voice_state_update_handler = None
                        except Exception as e:
                            logging.error(f"Error clearing voice handler: {e}")
                
                # Start cleanup but don't wait for completion
                if hasattr(bot, 'cleanup'):
                    asyncio.create_task(bot.cleanup())
            except Exception as e:
                logging.error(f"Error initiating bot cleanup: {e}")
        
        # Brief delay to allow cleanup to start
        logging.info("Waiting briefly for cleanup tasks to start...")
        await asyncio.sleep(0.2)
        
    except Exception as e:
        logging.error(f"Error during cleanup: {e}")
    finally:
        # Ensure all references are cleared
        bot = None
        systray = None
        logging.info("Forcing exit...")
        # Force exit as a last resort
        os._exit(0)

async def main():
    """Main entry point"""
    global bot, status_window, systray
    
    try:
        # Setup logging
        setup_logging()
        logger = logging.getLogger(__name__)
        
        # Load config
        config = load_config()
        
        # Create systray first
        systray = None
        bot = None  # Define bot at outer scope so quit_callback can access it
        
        def quit_callback():
            """Handle quit from systray"""
            global bot  # Changed from nonlocal to global
            logger.info("Quit requested from systray")
            try:
                # First cleanup the systray and its UI components
                if systray:
                    try:
                        # Stop the systray first to prevent new UI events
                        systray.stop()
                        logger.info("System tray icon stopped")
                        
                        # Store status window reference before cleanup
                        status_window = getattr(systray, 'status_window', None)
                        
                        # Cleanup status window if it exists
                        if status_window:
                            try:
                                logger.info("Starting UI window cleanup...")
                                # Force terminate UI thread 
                                status_window.cleanup()
                                logger.info("UI window cleanup completed")
                            except Exception as e:
                                logger.error(f"Error cleaning up status window: {e}")
                            finally:
                                # Remove reference to prevent further access
                                if hasattr(systray, 'status_window'):
                                    delattr(systray, 'status_window')
                    except Exception as e:
                        logger.error(f"Error stopping systray: {e}")
                
                # Then cleanup the bot - do this in a non-blocking way
                logger.info("Starting bot cleanup...")
                if bot:
                    # Schedule cleanup in event loop but don't wait for it
                    if hasattr(bot, 'loop') and bot.loop and not bot.loop.is_closed():
                        # First, clear voice handlers to prevent callbacks during disconnect
                        if hasattr(bot, '_voice_clients'):
                            for guild_id, voice_client in list(bot._voice_clients.items()):
                                try:
                                    if hasattr(voice_client, '_voice_state_update_handler'):
                                        voice_client._voice_state_update_handler = None
                                except Exception as e:
                                    logger.error(f"Error clearing voice state handler: {e}")
                        
                        # Schedule but don't wait for cleanup
                        bot.loop.call_soon_threadsafe(
                            lambda: asyncio.create_task(bot.cleanup())
                        )
                
                # Give cleanup a moment to start but don't wait for completion
                logger.info("Waiting briefly for cleanup to start...")
                time.sleep(0.2)
                
                # Force exit to ensure all threads terminate
                logger.info("Forcing exit...")
                os._exit(0)
                
            except Exception as e:
                logger.error(f"Error during quit: {e}")
                # Force exit on error
                os._exit(1)
            
        def debug_print(msg: str):
            """Debug print function that logs to both logger and status window"""
            # Filter patterns for console logging are already handled by SuppressFilter
            # We only log to the console, then the UI handles its own filtering
            logger.info(msg)
            if systray:
                status_window = getattr(systray, 'status_window', None)
                if status_window:
                    try:
                        status_window.add_log(msg)
                    except Exception as e:
                        logger.error(f"Error adding log to status window: {e}")
            
        systray = SystrayManager(config, quit_callback, toggle_mute_callback, debug_print)
        
        # Create bot with status callbacks
        bot = DiscordBot(config, debug_print,
                        status_callback=systray.update_status,
                        channel_callback=systray.update_channel)
        
        # Set UI callbacks on the bot
        bot.ui_callbacks = systray.status_window
        
        # Set up status window callbacks
        systray.status_window.set_callbacks(
            mute_toggle=toggle_mute_callback,
            join_channel=None,  # TODO: Implement
            leave_channel=None,  # TODO: Implement
            led_test=lambda key=None, color=None: (
                bot.led_controller.test_sequence(config) if key is None
                else (
                    bot.led_controller.set_color(0, 0, 
                                               color.get('red', 0),
                                               color.get('green', 0),
                                               color.get('blue', 0)),
                    time.sleep(0.5),
                    bot.led_controller.turn_off()
                )[-1]  # Return value of last expression
            ),
            debug_toggle=None,  # TODO: Implement
            test_announcement=lambda: asyncio.run_coroutine_threadsafe(
                bot.process_command('test_announcement'), bot.loop),
            disconnect=lambda: asyncio.run_coroutine_threadsafe(
                bot.disconnect_voice(), bot.loop),
            reconnect=lambda: asyncio.run_coroutine_threadsafe(
                bot.reconnect_voice(), bot.loop),
            chat_callback=lambda cmd, message: asyncio.run_coroutine_threadsafe(
                bot.process_command(cmd, message), bot.loop)
        )
        
        # Set additional systray callbacks
        systray.set_callbacks(
            on_disconnect=lambda: asyncio.run_coroutine_threadsafe(
                bot.disconnect_voice(), bot.loop),
            on_reconnect=lambda: asyncio.run_coroutine_threadsafe(
                bot.reconnect_voice(), bot.loop)
        )
        
        # Start the systray after setting up callbacks
        systray.run()
        
        try:
            # Start the bot
            await bot.start(config['discord_token'])
        except KeyboardInterrupt:
            logger.info("Received keyboard interrupt")
        finally:
            # Cleanup
            logger.info("Cleaning up...")
            if systray:
                systray.stop()
            if bot:
                try:
                    await bot.cleanup()
                    await bot.close()
                except Exception as e:
                    logger.error(f"Error during final cleanup: {e}")
            
    except Exception as e:
        logger.error(f"Fatal error: {str(e)}", exc_info=True)
        sys.exit(1)

if __name__ == "__main__":
    # Set up asyncio event loop with debug mode
    if sys.platform == 'win32':
        # Set up policy for Windows
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    
    try:
        loop.run_until_complete(main())
    except KeyboardInterrupt:
        pass
    finally:
        loop.close() 