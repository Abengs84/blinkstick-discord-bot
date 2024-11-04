import discord
from discord.ext import commands, voice_recv
import threading
import keyboard  # Import the keyboard library
import time
from blinkstick import blinkstick
import asyncio  # Import asyncio for adding delays
import signal
import sys
from gtts import gTTS
import os
import tempfile
import discord.opus
import platform
import pystray
from PIL import Image
from pystray import MenuItem as item
import winreg as reg
import json
import tkinter as tk
from tkinter import ttk

print(f"Python {platform.architecture()}")

def resource_path(relative_path):
    """ Get absolute path to resource, works for dev and for PyInstaller """
    try:
        # PyInstaller creates a temp folder and stores path in _MEIPASS
        base_path = sys._MEIPASS
    except Exception:
        base_path = os.path.abspath(".")
    return os.path.join(base_path, relative_path)

def load_config():
    try:
        config_path = resource_path('config.json')
        with open(config_path, 'r') as f:
            return json.load(f)
    except Exception as e:
        print(f"Error loading config: {e}")
        return None

dll_path = os.path.join(os.getcwd(), './libopus-0.dll')
print(f"Attempting to load Opus from: {dll_path}")
print(f"File exists: {os.path.exists(dll_path)}")

if not discord.opus.is_loaded():
    try:
        opus_path = resource_path('libopus-0.dll')
        discord.opus.load_opus(opus_path)
        print(f"Opus loaded from: {opus_path}")
    except Exception as e:
        print(f"Failed to load Opus: {e}")


# Global variable to control debug output
DEBUG_MODE = True

# Function to print debug messages
def debug_print(message):
    if DEBUG_MODE:
        print(message)

# Variable to track LED state
led_on = False

# Initialize the BlinkStick
bs = blinkstick.find_first()
if bs is None:
    print("ERROR: No BlinkStick found! Please check USB connection.")
else:
    print(f"BlinkStick found: {bs.get_description()}")

# Example function to set the LED color
def set_led_color(channel, index, red, green, blue):
    global bs
    max_retries = 3
    for attempt in range(max_retries):
        try:
            if bs is None or not bs.get_description():  # Check if BlinkStick is responsive
                initialize_blinkstick()
            if bs:
                bs.set_color(channel, index, red, green, blue)
                debug_print(f"LED set: channel={channel}, index={index}, RGB=({red},{green},{blue})")
                return True
        except Exception as e:
            print(f"Attempt {attempt + 1}: Error setting LED color: {e}")
            initialize_blinkstick()  # Try to reinitialize
    return False

def initialize_blinkstick():
    global bs
    bs = blinkstick.find_first()
    if bs is None:
        print("ERROR: No BlinkStick found! Please check USB connection.")
        return False
    print(f"BlinkStick found: {bs.get_description()}")
    return True

# Define MySink class to handle audio data
class MySink(voice_recv.AudioSink):
    def __init__(self):
        super().__init__()
        self.speaking_states = {}  # Track speaking state for each member
        debug_print("MySink initialized")
        # Test LED at initialization
        set_led_color(channel=0, index=0, red=255, green=0, blue=0)  # Brief red flash
        time.sleep(0.5)
        set_led_color(channel=0, index=0, red=0, green=0, blue=0)  # Turn off
    
    @voice_recv.AudioSink.listener()
    def on_voice_member_speaking_start(self, member: discord.Member):
        print(f"Voice start detected for {member.name}")  # Debug print
        self.speaking_states[member.name] = True
        if member.name.strip().lower() == "***REMOVED***":
            print("Attempting to set red LED")  # Debug print
            set_led_color(channel=0, index=0, red=255, green=0, blue=0)
        else:
            print("Attempting to set blue LED")  # Debug print
            set_led_color(channel=0, index=2, red=0, green=0, blue=255)

    @voice_recv.AudioSink.listener()
    def on_voice_member_speaking_stop(self, member: discord.Member):
        print(f"Voice stop detected for {member.name}")  # Debug print
        self.speaking_states[member.name] = False
        if member.name.strip().lower() == "***REMOVED***":
            print("Attempting to turn off red LED")  # Debug print
            set_led_color(channel=0, index=0, red=0, green=0, blue=0)
        else:
            print("Attempting to turn off blue LED")  # Debug print
            set_led_color(channel=0, index=2, red=0, green=0, blue=0)

    def cleanup(self):
        debug_print("Cleaning up MySink resources.")
        # Force turn off all relevant LEDs during cleanup
        set_led_color(channel=0, index=0, red=0, green=0, blue=0)  # ***REMOVED***'s LED
        set_led_color(channel=0, index=2, red=0, green=0, blue=0)  # Others' LED

    def wants_opus(self) -> bool:
        return True

    def write(self, source, data: voice_recv.VoiceData):
        audio_length = len(data.pcm)  # Access the PCM audio data

# Custom bot class
class MyBot(commands.Bot):
    async def setup_hook(self):
        debug_print("Running setup_hook...")  # Debugging output
        for guild in self.guilds:
            debug_print(f"Checking guild: {guild.name}")  # Debugging output
            for member in guild.members:
                debug_print(f"Checking member: {member.name}, Voice: {member.voice}")  # Debugging output
                if member.name.strip().lower() == "***REMOVED***":  # Case insensitive check
                    if member.voice:  # Check if the user is in a voice channel
                        channel = member.voice.channel
                        debug_print(f"***REMOVED*** is in channel: {channel.name}")  # Debugging output
                        vc = await channel.connect(cls=voice_recv.VoiceRecvClient)
                        sink = MySink()  # Create an instance of MySink
                        vc.listen(sink)  # Register the sink to listen for audio data
                        debug_print(f"Bot has joined {channel.name} because ***REMOVED*** is in it.")
                        return  # Exit after joining the channel
                    else:
                        debug_print("***REMOVED*** is not in any voice channel.")  # Debugging output
        debug_print("***REMOVED*** is not in any voice channel.")  # Debugging output

# Initialize the bot
intents = discord.Intents.default()
intents.guilds = True  # Enable guilds intent
intents.voice_states = True  # Enable voice states intent
intents.message_content = True  # Enable message content intent
bot = MyBot(command_prefix='!', intents=intents)

# Function to change LED color when the key combination is pressed
def change_led_color():
    global led_on
    try:
        if not led_on:
            debug_print("Key combination pressed! Turning LED on.")
            set_led_color(channel=0, index=1, red=60, green=0, blue=0)  # Change to red
            led_on = True
        else:
            debug_print("Key combination pressed! Turning LED off.")
            set_led_color(channel=0, index=1, red=0, green=0, blue=0)  # Turn off the LED
            led_on = False
    except Exception as e:
        debug_print(f"Error changing LED color: {e}")

# Function to handle the power-on sequence
async def power_on_sequence():
    # Cycle through all 8 indexes with a short 50ms delay between each
    for i in range(8):
        set_led_color(channel=0, index=i, red=0, green=50, blue=0)  # Set LED to green
        await asyncio.sleep(0.05)  # 50ms delay between each LED
        set_led_color(channel=0, index=i, red=0, green=0, blue=0)  # Turn off LED
    
    # Flash all indexes with a longer duration
    for i in range(8):
        set_led_color(channel=0, index=i, red=0, green=100, blue=0)  # Set all LEDs to green
    await asyncio.sleep(0.5)  # 500ms duration
    
    # Turn off all LEDs after the flash
    for i in range(8):
        set_led_color(channel=0, index=i, red=0, green=0, blue=0)  # Turn off all LEDs


# Function to check if the bot is listening to "***REMOVED***"
def is_listening(voice_client, user) -> bool:
    return voice_client.get_speaking(user)

# Callback function to handle incoming voice packets
async def callback(voice_client, user, data: voice_recv.VoiceData):
    user_name = user.name
    debug_print(f"Got packet from {user_name}")  # Debugging output

    if user_name.strip() == "***REMOVED***":
        speaking = is_listening(voice_client, user)  # Check if the user is speaking

# Set up a listener for the key combination
def start_key_listener():
    keyboard.wait()  # This will block until the program is terminated

@bot.event
async def on_message(message):
    # Ignore messages sent by the bot itself
    if message.author == bot.user:
        return

    # Check if the message contains any specific content (optional)
    if "flash" in message.content.lower():  # Example condition, can be modified
        debug_print(f"Message received: {message.content} from {message.author}")
        
        # Fade in the LED (increase brightness gradually)
        for brightness in range(0, 256, 25):  # Step through brightness levels (0 to 255)
            set_led_color(channel=0, index=3, red=brightness, green=brightness, blue=0)  # Yellow = Red + Green
            await asyncio.sleep(0.05)  # Small delay for smooth transition (50ms)
        
        await asyncio.sleep(0.5)  # Hold the brightness for 500ms

        # Fade out the LED (decrease brightness gradually)
        for brightness in range(255, -1, -25):  # Step down through brightness levels
            set_led_color(channel=0, index=3, red=brightness, green=brightness, blue=0)  # Yellow = Red + Green
            await asyncio.sleep(0.05)  # Small delay for smooth transition (50ms)
        
        # Turn off the LED at the end
        set_led_color(channel=0, index=3, red=0, green=0, blue=0)

    await bot.process_commands(message)  # This ensures other commands still work


# New event listener for when ***REMOVED*** stops speaking
@bot.event
async def on_voice_member_speaking_stop(member: discord.Member):
    if member.name.strip().lower() == "***REMOVED***":
        debug_print(f"{member.name} has stopped speaking.")
        # Add your LED off logic here

# Asynchronous wrapper for the callback
async def async_callback(voice_client, user, data):
    await callback(voice_client, user, data)

@bot.event
async def on_ready():
    debug_print(f'Logged in as {bot.user.id}/{bot.user}')
    await power_on_sequence()  # Call the power-on sequence
    await bot.setup_hook()  # Call the setup_hook explicitly

@bot.event
async def on_voice_state_update(member, before, after):
    if member.name.strip().lower() == "***REMOVED***":
        if after.channel:  # When joining a channel
            debug_print(f"{member.name} has joined {after.channel.name}.")
            try:
                # Reinitialize BlinkStick
                initialize_blinkstick()
                
                if not member.guild.voice_client:
                    vc = await after.channel.connect(cls=voice_recv.VoiceRecvClient)
                    sink = MySink()  # Create new sink instance
                    vc.listen(sink)  # Start listening with new sink
                    debug_print(f"Bot has joined {after.channel.name}")
                elif member.guild.voice_client.channel != after.channel:
                    await member.guild.voice_client.move_to(after.channel)
                    # Reinitialize sink after moving
                    sink = MySink()
                    member.guild.voice_client.listen(sink)
                    debug_print(f"Bot has moved to {after.channel.name}")
            except Exception as e:
                debug_print(f"Error joining channel: {e}")
        else:  # When leaving a channel
            debug_print(f"{member.name} has left the voice channel.")
            if member.guild.voice_client:
                if hasattr(member.guild.voice_client, 'sink'):
                    member.guild.voice_client.sink.cleanup()
                await member.guild.voice_client.disconnect()
                debug_print("Bot has disconnected because ***REMOVED*** left.")

@bot.command()
async def say(ctx, *, text: str):
    """Makes the bot say the provided text in the voice channel."""
    if ctx.author.voice:  # Check if the user is in a voice channel
        channel = ctx.author.voice.channel
        if ctx.voice_client is None:  # If the bot is not connected to a voice channel
            await channel.connect()

        # Generate speech from text
        tts = gTTS(text=text, lang='en')
        
        # Save the audio to a temporary file
        with tempfile.NamedTemporaryFile(delete=True) as fp:
            tts.save(f"{fp.name}.mp3")
            fp.seek(0)  # Go to the start of the file
            ctx.voice_client.play(discord.FFmpegPCMAudio(f"{fp.name}.mp3"), after=lambda e: print('done', e))
    else:
        await ctx.send("You need to be in a voice channel to use this command.")

def start_key_listener():
    keyboard.add_hotkey('ctrl+shift+alt+ö', change_led_color)
    keyboard.wait()  # This will block until the program is terminated

# Start the key listener in a separate thread
listener_thread = threading.Thread(target=start_key_listener)
listener_thread.daemon = True  # Make the thread a daemon
listener_thread.start()

def cleanup():
    debug_print("Cleaning up before exit...")
    channel = 0  # Specify the channel you want to turn off
    num_indexes = 8  # Replace with the actual number of indexes for your LED strip

    # Turn off all indexes on the specified channel
    for index in range(num_indexes):
        set_led_color(channel=channel, index=index, red=0, green=0, blue=0)  # Turn off the LED at each index

    sys.exit(0)

# Register the cleanup function to be called on exit
signal.signal(signal.SIGINT, lambda s, f: cleanup())
signal.signal(signal.SIGTERM, lambda s, f: cleanup())

class StatusWindow:
    def __init__(self):
        self.window = None
        
    def toggle_debug(self):
        global DEBUG_MODE
        DEBUG_MODE = not DEBUG_MODE
        self.debug_var.set(f"Debug Mode: {'ON' if DEBUG_MODE else 'OFF'}")
        
    def show(self):
        if self.window is not None:
            self.window.destroy()
        
        # Create new window
        self.window = tk.Tk()
        self.window.title("Discord Bot Status")
        self.window.geometry("300x200")
        
        # Create a frame with padding
        frame = ttk.Frame(self.window, padding="10")
        frame.grid(row=0, column=0, sticky=(tk.W, tk.E, tk.N, tk.S))
        
        # Bot status
        status_text = "Connected" if bot.is_ready() else "Disconnected"
        ttk.Label(frame, text=f"Bot Status: {status_text}").grid(row=0, column=0, sticky=tk.W, pady=5)
        
        # Server and channel info
        server_name = "None"
        channel_name = "None"
        for guild in bot.guilds:
            server_name = guild.name
            for vc in guild.voice_channels:
                if bot.user in vc.members:
                    channel_name = vc.name
                    break
        
        ttk.Label(frame, text=f"Server: {server_name}").grid(row=1, column=0, sticky=tk.W, pady=5)
        ttk.Label(frame, text=f"Voice Channel: {channel_name}").grid(row=2, column=0, sticky=tk.W, pady=5)
        
        # Debug mode toggle
        self.debug_var = tk.StringVar(value=f"Debug Mode: {'ON' if DEBUG_MODE else 'OFF'}")
        ttk.Button(frame, textvariable=self.debug_var, command=self.toggle_debug).grid(row=3, column=0, sticky=tk.W, pady=10)
        
        # Close button
        ttk.Button(frame, text="Close", command=self.window.destroy).grid(row=4, column=0, sticky=tk.W, pady=10)
        
        # Center window on screen
        self.window.update_idletasks()
        width = self.window.winfo_width()
        height = self.window.winfo_height()
        x = (self.window.winfo_screenwidth() // 2) - (width // 2)
        y = (self.window.winfo_screenheight() // 2) - (height // 2)
        self.window.geometry(f'{width}x{height}+{x}+{y}')
        
        self.window.mainloop()

# Create a global status window instance
status_window = StatusWindow()

# Create the system tray icon and menu
def create_tray_icon():
    try:
        icon_path = resource_path('led.ico')
        icon_image = Image.open(icon_path)
    except Exception as e:
        print(f"Failed to load icon: {e}")
        # Fallback to generated icon
        icon_image = Image.new('RGB', (64, 64), color = 'red')
    
    def exit_action(icon):
        icon.stop()
        cleanup()
        os._exit(0)

    def show_status(icon):
        # Show status window instead of notification
        status_window.show()

    # Create the menu
    menu = (
        item('Status', show_status),
        item('Exit', exit_action)
    )

    # Create the icon
    icon = pystray.Icon("DiscordBot", icon_image, "Discord Bot", menu)
    return icon

# Modify your main code to run in a separate thread
def run_bot():
    config = load_config()
    if config and 'token' in config:
        bot.run(config['token'])
    else:
        print("Error: Could not load bot token from config file")

def add_to_startup():
    file_path = os.path.abspath(sys.argv[0])
    key_path = r"Software\Microsoft\Windows\CurrentVersion\Run"
    
    try:
        key = reg.HKEY_CURRENT_USER
        key_handle = reg.OpenKey(key, key_path, 0, reg.KEY_ALL_ACCESS)
        reg.SetValueEx(key_handle, "DiscordBotApp", 0, reg.REG_SZ, file_path)
        reg.CloseKey(key_handle)
        return True
    except WindowsError as e:
        print(f"Error adding to startup: {e}")
        return False

# Main execution
if __name__ == "__main__":
    add_to_startup()  # Optional: add to startup
    # Create and start the bot thread
    bot_thread = threading.Thread(target=run_bot, daemon=True)
    bot_thread.start()

    # Create and run the system tray icon
    icon = create_tray_icon()
    icon.run()