import discord
from discord.ext import commands, voice_recv, tasks
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
import datetime
import speech_recognition as sr
from queue import Queue
import numpy as np
import wave
import random
import edge_tts

print(f"Python {platform.architecture()}")

def debug_print(message):
    """Print debug messages if DEBUG_MODE is enabled"""
    if DEBUG_MODE:
        print(f"[DEBUG] {message}")

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

# Load config before using debug_print
config = load_config()
if config:
    DEBUG_MODE = config.get('debug_mode', True)
    TARGET_USER = config.get('target_user', 'USER')
    led_on = config.get('led_enabled', False)
    HOTKEY = config.get('hotkey', 'ctrl+shift+alt+o')
else:
    # Fallback defaults if config fails to load
    DEBUG_MODE = True
    TARGET_USER = "USER"
    led_on = False
    HOTKEY = 'ctrl+shift+alt+o'

# Now handle Opus loading
if not discord.opus.is_loaded():
    try:
        if platform.system() == 'Windows':
            # Try multiple possible Opus DLL locations
            possible_opus_paths = [
                './libopus.dll',
                './opus.dll',
                './libopus-0.dll',
                resource_path('libopus.dll'),
                resource_path('opus.dll'),
                resource_path('libopus-0.dll')
            ]
            
            for opus_path in possible_opus_paths:
                try:
                    discord.opus.load_opus(opus_path)
                    debug_print(f"Successfully loaded Opus from: {opus_path}")
                    break
                except Exception as e:
                    debug_print(f"Failed to load Opus from {opus_path}: {e}")
            
            if not discord.opus.is_loaded():
                debug_print("Could not load Opus from any location")
        else:
            opus_path = resource_path('libopus.so')
            discord.opus.load_opus(opus_path)
    except Exception as e:
        debug_print(f"Failed to load Opus: {e}")
        debug_print("Voice functionality may be limited")

# Initialize the BlinkStick
def initialize_blinkstick():
    global bs
    # Find all connected BlinkSticks
    all_sticks = blinkstick.find_all()
    
    # Look for the specific BlinkStick
    target_serial = "BS061825-3.0"
    for stick in all_sticks:
        if stick.get_serial() == target_serial:
            bs = stick
            debug_print(f"Found target BlinkStick: {target_serial}")
            return True
    
    debug_print(f"Target BlinkStick {target_serial} not found!")
    # Fallback to first available if target not found
    bs = blinkstick.find_first()
    if bs:
        debug_print(f"Using fallback BlinkStick: {bs.get_serial()}")
        return True
    return False

# Replace the existing BlinkStick initialization with the new function
if not initialize_blinkstick():
    print("ERROR: No BlinkStick found! Please check USB connection.")
else:
    print(f"BlinkStick found: {bs.get_description()} (Serial: {bs.get_serial()})")

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

# Define MySink class to handle audio data
class MySink(voice_recv.AudioSink):
    async def generate_speech(self, text, output_file):
        # Voice options: rate=speed of speech, pitch=voice pitch
        voice = 'de-AT-JonasNeural'
        communicate = edge_tts.Communicate(
            text,
            voice,
            rate="+15%",     # Use percentage for rate
            volume="+0%"      # Use volume instead of pitch
        )
        await communicate.save(output_file)

    def __init__(self):
        self.speaking_states = {}
        self.audio_queue = Queue()
        self.recognizer = sr.Recognizer()
        self.jokes = [
            "Two old ladies were sitting on a park bench when a man in a trench coat came up and flashed them. One old lady immediately had a stroke. The other couldn't quite reach.",
            "Why did the programmer quit his job? Because he didn't get arrays!",
            "What do you call a programmer from Finland? Nerdic!",
            "Why do programmers always mix up Halloween and Christmas? Because Oct 31 equals Dec 25!",
            "What's a programmer's favorite place? The Cookie Store!"
        ]
        self.wake_phrases = {
            "hey dick": "greeting"  # Start with only the greeting command
        }
        self.additional_commands = {
            "tell me a joke": "joke",
            "play sound": "sound"
        }
        self.sound_file = resource_path('sounds/Glenn.mp3')
        debug_print(f"Sound file path: {self.sound_file}")
        debug_print(f"Sound file exists: {os.path.exists(self.sound_file)}")
        
        # Check if sound file exists
        if not os.path.exists(self.sound_file):
            debug_print(f"Warning: Sound file not found at {self.sound_file}")
            # Try to list contents of sounds directory
            sounds_dir = resource_path('sounds')
            if os.path.exists(sounds_dir):
                debug_print(f"Contents of sounds directory: {os.listdir(sounds_dir)}")
        
        self.is_speaking = False
        
        # Audio settings for Discord's voice format
        self.input_sample_rate = 48000  # Discord sends 48kHz
        self.channels = 2  # Discord sends stereo
        self.buffer_duration = 2.0  # Increased to 2 seconds for better recognition
        self.samples_per_buffer = int(self.input_sample_rate * self.buffer_duration)
        
        # Separate buffers for original and processed audio
        self.original_buffer = []  # Store original PCM chunks
        self.processed_buffer = np.array([], dtype=np.int16)  # Store processed audio
        
        self.debug_counter = 0
        debug_print("MySink initialized with Discord voice format")
        
        # Recognition settings
        self.recognizer.energy_threshold = 100
        self.recognizer.dynamic_energy_threshold = True
        self.recognizer.pause_threshold = 0.3
        self.recognizer.phrase_threshold = 0.1
        self.recognizer.non_speaking_duration = 0.1
        
        # Debug settings
        self.total_chunks = 0
        self.recognition_attempts = 0
        self.successful_recognitions = 0
        self.debug_recording = False
        
        # Track if bot has been greeted
        self.has_been_greeted = False

        # Start the recognition thread using asyncio
        self.recognition_task = asyncio.create_task(self.process_audio())

    def cleanup(self):
        """Required method: Clean up resources"""
        debug_print("Cleaning up MySink resources")
        set_led_color(channel=0, index=0, red=0, green=0, blue=0)
        set_led_color(channel=0, index=2, red=0, green=0, blue=0)

    def wants_opus(self) -> bool:
        """Required method: Indicate if we want Opus encoded data"""
        return False  # We want PCM data instead of Opus

    def write(self, user, data):
        try:
            if (user.name.strip().lower() == TARGET_USER.lower() and 
                self.is_speaking):
                # Store original PCM data
                if self.debug_recording:
                    self.original_buffer.append(data.pcm)
                
                # Process audio for recognition
                audio_array = np.frombuffer(data.pcm, dtype=np.int16)
                audio_array = audio_array.reshape(-1, 2)
                audio_mono = np.mean(audio_array, axis=1, dtype=np.int16)
                audio_resampled = audio_mono[::3]
                
                # Add to processed buffer
                self.processed_buffer = np.concatenate([self.processed_buffer, audio_resampled])
                
                # Check if we have enough data
                if len(self.processed_buffer) >= self.samples_per_buffer // 3:
                    if self.debug_recording:
                        self.debug_counter += 1
                        
                        # Save original audio by combining chunks
                        if self.original_buffer:
                            debug_file_orig = f'debug_original_{self.debug_counter}.wav'
                            with wave.open(debug_file_orig, 'wb') as wf:
                                wf.setnchannels(2)  # Stereo
                                wf.setsampwidth(2)  # 16-bit
                                wf.setframerate(48000)  # Original sample rate
                                wf.writeframes(b''.join(self.original_buffer))
                            debug_print(f"Saved combined original audio to {debug_file_orig}")
                            self.original_buffer = []  # Clear original buffer
                        
                        # Save processed audio
                        debug_file = f'debug_processed_{self.debug_counter}.wav'
                        with wave.open(debug_file, 'wb') as wf:
                            wf.setnchannels(1)  # Mono
                            wf.setsampwidth(2)  # 16-bit
                            wf.setframerate(16000)  # Speech recognition rate
                            wf.writeframes(self.processed_buffer.tobytes())
                        debug_print(f"Saved processed audio to {debug_file}")
                    
                    # Queue the processed buffer for recognition
                    self.audio_queue.put(self.processed_buffer.copy())
                    self.processed_buffer = np.array([], dtype=np.int16)
        except Exception as e:
            debug_print(f"Error in write: {e}")

    @voice_recv.AudioSink.listener()
    def on_voice_member_speaking_start(self, member: discord.Member):
        debug_print(f"Voice start detected for {member.name}")
        self.speaking_states[member.name] = True
        if member.name.strip().lower() == TARGET_USER.lower():
            debug_print("Setting red LED for target user")
            self.is_speaking = True  # Set speaking state
            set_led_color(channel=0, index=0, red=255, green=0, blue=0)
        else:
            debug_print("Setting blue LED for other user")
            set_led_color(channel=0, index=2, red=0, green=0, blue=255)

    @voice_recv.AudioSink.listener()
    def on_voice_member_speaking_stop(self, member: discord.Member):
        debug_print(f"Voice stop detected for {member.name}")
        self.speaking_states[member.name] = False
        if member.name.strip().lower() == TARGET_USER.lower():
            debug_print("Turning off red LED")
            self.is_speaking = False  # Reset speaking state
            # Clear the audio queue when stopping speaking
            while not self.audio_queue.empty():
                self.audio_queue.get()
            set_led_color(channel=0, index=0, red=0, green=0, blue=0)
        else:
            debug_print("Turning off blue LED")
            set_led_color(channel=0, index=2, red=0, green=0, blue=0)

    async def process_audio(self):
        greetings = [
            "Hello!",
            "Jaah Was ist los",
            "Heil arsehole",
            "I'm listening! You can now ask me for jokes or to play sounds.",
            "Ready to clap those balls!"
        ]

        while True:
            try:
                if not self.audio_queue.empty() and self.is_speaking:
                    audio_data = self.audio_queue.get()
                    
                    # Convert to WAV for recognition
                    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as temp_wav:
                        with wave.open(temp_wav.name, 'wb') as wf:
                            wf.setnchannels(1)  # Mono
                            wf.setsampwidth(2)  # 16-bit
                            wf.setframerate(16000)  # Use 16kHz for speech recognition
                            wf.writeframes(audio_data.tobytes())
                        
                        try:
                            with sr.AudioFile(temp_wav.name) as source:
                                debug_print("Starting recognition...")
                                self.recognizer.energy_threshold = 300
                                self.recognizer.dynamic_energy_threshold = True
                                self.recognizer.pause_threshold = 0.8
                                audio = self.recognizer.record(source)
                                
                                try:
                                    text = self.recognizer.recognize_google(
                                        audio, 
                                        language='en-US'
                                    ).lower()
                                    debug_print(f"Google recognized: '{text}'")
                                    self.successful_recognitions += 1
                                    
                                    # Check for commands in the text
                                    for phrase, command_type in self.wake_phrases.items():
                                        if phrase in text:
                                            debug_print(f"Command detected: {command_type}")
                                            
                                            # Handle initial greeting
                                            if command_type == "greeting":
                                                if not self.has_been_greeted:
                                                    # Enable additional commands after first greeting
                                                    self.wake_phrases.update(self.additional_commands)
                                                    self.has_been_greeted = True
                                                    debug_print("Additional commands enabled!")
                                                
                                                response = random.choice(greetings)
                                            elif command_type == "joke":
                                                response = random.choice(self.jokes)
                                            elif command_type == "sound":
                                                debug_print("Sound command detected!")
                                                # Play custom sound file
                                                for guild in bot.guilds:
                                                    if guild.voice_client:
                                                        try:
                                                            debug_print(f"Attempting to play sound file: {self.sound_file}")
                                                            if not os.path.exists(self.sound_file):
                                                                debug_print("Sound file does not exist!")
                                                                continue
                                                                
                                                            guild.voice_client.play(
                                                                discord.FFmpegPCMAudio(
                                                                    self.sound_file,
                                                                    options='-loglevel debug'  # Add FFmpeg debug output
                                                                ),
                                                                after=lambda e: debug_print(f"Finished playing sound" if not e else f"Error playing sound: {e}")
                                                            )
                                                            debug_print("Started playing sound file")
                                                        except Exception as e:
                                                            debug_print(f"Error playing sound file: {e}")
                                                        break
                                
                                except sr.UnknownValueError:
                                    debug_print("Speech was unclear")
                                except sr.RequestError as e:
                                    debug_print(f"Recognition error: {e}")
                        finally:
                            try:
                                os.unlink(temp_wav.name)  # Clean up temp file
                            except:
                                pass
            except Exception as e:
                debug_print(f"Error in process_audio: {e}")
            await asyncio.sleep(0.1)  # Use asyncio.sleep instead of time.sleep

# Custom bot class
class MyBot(commands.Bot):
    async def setup_hook(self):
        debug_print("Running setup_hook...")
        
        if not hasattr(self, '_scheduled_announcement_task_started'):
            self.scheduled_announcement.start()
            self._scheduled_announcement_task_started = True
        
        debug_print(f"Total guilds: {len(self.guilds)}")
        if len(self.guilds) == 0:
            debug_print("Bot is not in any guilds! Please invite the bot to your server.")
            return
        
        for guild in self.guilds:
            debug_print(f"\nChecking guild: {guild.name} (ID: {guild.id})")
            
            # Print all members
            members_list = [f"{member.name}#{member.discriminator}" for member in guild.members]
            debug_print(f"All guild members: {members_list}")
            
            # Print all voice channels and their members
            for vc in guild.voice_channels:
                members_in_vc = [f"{m.name}#{m.discriminator}" for m in vc.members]
                debug_print(f"Voice channel '{vc.name}' members: {members_in_vc}")
            
            for member in guild.members:
                debug_print(f"\nChecking member: {member.name}#{member.discriminator}")
                debug_print(f"Member ID: {member.id}")
                debug_print(f"Member display name: {member.display_name}")
                debug_print(f"Voice state: {member.voice}")
                debug_print(f"Is bot: {member.bot}")
                
                # Check both name and name#discriminator
                target_matches = [
                    member.name == TARGET_USER,  # Exact name match
                    f"{member.name}#{member.discriminator}" == TARGET_USER,  # Full discriminator match
                    member.name.lower() == TARGET_USER.lower(),  # Case-insensitive name match
                    f"{member.name}#{member.discriminator}".lower() == TARGET_USER.lower()  # Case-insensitive full match
                ]
                
                debug_print(f"Checking if {member.name}#{member.discriminator} matches {TARGET_USER}")
                if any(target_matches):
                    debug_print(f"Found target user: {member.name}#{member.discriminator}")
                    if member.voice:
                        channel = member.voice.channel
                        debug_print(f"{TARGET_USER} is in channel: {channel.name}")
                        try:
                            vc = await channel.connect(cls=voice_recv.VoiceRecvClient)
                            sink = MySink()
                            vc.listen(sink)
                            debug_print(f"Bot has joined {channel.name} because {TARGET_USER} is in it.")
                            return
                        except Exception as e:
                            debug_print(f"Error connecting to channel: {e}")
                            debug_print(f"Error details: {str(e)}")
                    else:
                        debug_print(f"{TARGET_USER} was found but is not in a voice channel")

        debug_print(f"\nCould not find {TARGET_USER} in any voice channel")

    @tasks.loop(minutes=1)  # Check every minute
    async def scheduled_announcement(self):
        current_time = datetime.datetime.now()
        target_hour = 19
        target_minute = 0
        
        debug_print(f"Checking scheduled announcement at {current_time}")
        debug_print(f"Current hour: {current_time.hour}, minute: {current_time.minute}")
        debug_print(f"Target hour: {target_hour}, minute: {target_minute}")
        
        # Check if it's Friday and the right time
        if current_time.weekday() == 4 and current_time.hour == target_hour and current_time.minute == target_minute:
            debug_print("It's Friday 19:00 - time for announcement!")
            
            # Check if bot is connected to any voice channel
            for guild in self.guilds:
                voice_client = guild.voice_client
                if voice_client and voice_client.is_connected():
                    debug_print("Bot is connected to voice - making announcement")
                    
                    try:
                        # Generate speech from text
                        announcement_text = "Happy Friday everyone! It's time for the weekend!"
                        debug_print(f"Generating announcement: {announcement_text}")
                        tts = gTTS(text=announcement_text, lang='en')
                        
                        # Save and play the audio
                        with tempfile.NamedTemporaryFile(delete=False, suffix='.mp3') as fp:
                            temp_path = fp.name
                            tts.save(temp_path)
                            debug_print("Saved announcement audio file")
                            
                            def after_play(error):
                                if error:
                                    debug_print(f"Error playing audio: {error}")
                                try:
                                    os.unlink(temp_path)  # Delete the temporary file
                                    debug_print("Cleaned up temporary audio file")
                                except Exception as e:
                                    debug_print(f"Error deleting temp file: {e}")
                            
                            voice_client.play(discord.FFmpegPCMAudio(temp_path), after=after_play)
                            debug_print("Started playing announcement")
                        return  # Exit after playing in first connected channel
                    except Exception as e:
                        debug_print(f"Error during announcement: {e}")
                else:
                    debug_print("Bot is not connected to any voice channel")
        else:
            if current_time.weekday() != 4:
                debug_print("Not Friday - no announcement needed")
            elif current_time.hour != target_hour or current_time.minute != target_minute:
                debug_print("Not announcement time yet")

    @scheduled_announcement.before_loop
    async def before_announcement(self):
        await self.wait_until_ready()
        debug_print("Scheduled announcement task is ready")

# Initialize the bot
intents = discord.Intents.default()
intents.message_content = True
intents.voice_states = True
intents.members = True

# Create bot instance with command prefix and intents
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


# Function to check if the bot is listening to user
def is_listening(voice_client, user) -> bool:
    return voice_client.get_speaking(user)

# Callback function to handle incoming voice packets
async def callback(voice_client, user, data: voice_recv.VoiceData):
    user_name = user.name
    debug_print(f"Got packet from {user_name}")  # Debugging output

    if user_name.strip() == TARGET_USER:
        speaking = is_listening(voice_client, user)  # Check if the user is speaking

# Function to start key listener with error handling
def start_key_listener():
    try:
        debug_print(f"Starting key listener with hotkey: {HOTKEY}")
        keyboard.add_hotkey(HOTKEY, change_led_color)
        debug_print("Hotkey registered successfully")
        keyboard.wait()  # This will block until the program is terminated
    except Exception as e:
        debug_print(f"Error in key listener: {e}")
        try:
            fallback_hotkey = 'ctrl+shift+o'  # Simpler fallback hotkey
            debug_print(f"Trying fallback hotkey: {fallback_hotkey}")
            keyboard.add_hotkey(fallback_hotkey, change_led_color)
            debug_print("Fallback hotkey registered")
            keyboard.wait()
        except Exception as e:
            debug_print(f"Fallback hotkey also failed: {e}")

# New event listener for when user stops speaking
@bot.event
async def on_voice_member_speaking_stop(member: discord.Member):
    if member.name.strip().lower() == TARGET_USER.lower():
        debug_print(f"{member.name} has stopped speaking.")
        # Add your LED off logic here

# Asynchronous wrapper for the callback
async def async_callback(voice_client, user, data):
    await callback(voice_client, user, data)

@bot.event
async def on_ready():
    debug_print(f'Logged in as {bot.user.id}/{bot.user}')
    
    # Check guilds after connection
    if len(bot.guilds) == 0:
        debug_print("Bot is not in any guilds! Please reinvite the bot.")
        debug_print("Use this link: https://discord.com/api/oauth2/authorize?client_id=REDACTED&permissions=3214336&scope=bot%20applications.commands")
    else:
        for guild in bot.guilds:
            debug_print(f"Connected to guild: {guild.name} (ID: {guild.id})")
            debug_print(f"Bot permissions: {guild.me.guild_permissions}")
            
            # Check voice channels
            for vc in guild.voice_channels:
                permissions = vc.permissions_for(guild.me)
                debug_print(f"Can connect to {vc.name}: {permissions.connect}")
                debug_print(f"Can speak in {vc.name}: {permissions.speak}")
                debug_print(f"Can use voice activity in {vc.name}: {permissions.use_voice_activation}")
                members = [f"{m.name}#{m.discriminator}" for m in vc.members]
                debug_print(f"Members in {vc.name}: {members}")
    
    await power_on_sequence()

@bot.event
async def on_voice_state_update(member, before, after):
    # Skip if it's the target user or the bot itself
    if member.name.strip().lower() == TARGET_USER.lower() or member == bot.user:
        if after.channel:  # Target user joined a channel
            # Turn off notification LED since target user is now present
            set_led_color(channel=0, index=3, red=0, green=0, blue=0)
            try:
                if not member.guild.voice_client:
                    vc = await after.channel.connect(cls=voice_recv.VoiceRecvClient)
                    sink = MySink()
                    vc.listen(sink)
                    debug_print(f"Bot has joined {after.channel.name}")
            except Exception as e:
                debug_print(f"Error joining channel: {e}")
    else:  # Someone else changed voice state
        # Check if target user is in any voice channel
        target_in_voice = False
        total_voice_users = 0
        
        for guild in bot.guilds:
            for vc in guild.voice_channels:
                # Count users in voice (excluding bots)
                total_voice_users += sum(1 for m in vc.members if not m.bot)
                if any(m.name.strip().lower() == TARGET_USER.lower() for m in vc.members):
                    target_in_voice = True
                    break

        if not target_in_voice and total_voice_users > 0:  # People in voice but target user isn't
            debug_print(f"Voice activity detected: {total_voice_users} users in voice")
            
            # Start pulsing in a separate task to avoid blocking
            if not hasattr(bot, 'pulse_task') or bot.pulse_task.done():
                bot.pulse_task = asyncio.create_task(pulse_notification(total_voice_users))

async def pulse_notification(user_count):
    """Creates a pulsing yellow light with intensity based on user count"""
    max_brightness = min(50 * user_count, 255)  # 50 brightness per user, max 255
    
    try:
        while True:  # Continue pulsing until interrupted
            # Pulse up
            for brightness in range(0, max_brightness, 5):
                set_led_color(channel=0, index=3, 
                            red=brightness, 
                            green=int(brightness * 0.8),  # Slightly less green for warmer yellow
                            blue=0)
                await asyncio.sleep(0.05)
            
            # Pulse down
            for brightness in range(max_brightness, 0, -5):
                set_led_color(channel=0, index=3, 
                            red=brightness, 
                            green=int(brightness * 0.8),  # Slightly less green for warmer yellow
                            blue=0)
                await asyncio.sleep(0.05)
            
            await asyncio.sleep(0.1)  # Small pause between pulses
            
    except asyncio.CancelledError:
        # Clean up when cancelled
        set_led_color(channel=0, index=3, red=0, green=0, blue=0)

@bot.command()
async def test(ctx):
    """Test if the bot is working"""
    try:
        await ctx.send("Bot is working!")
        debug_print(f"Test command used in {ctx.guild.name} by {ctx.author}")
    except Exception as e:
        debug_print(f"Error in test command: {e}")

@bot.command()
async def debugrec(ctx):
    """Toggle debug recording of audio"""
    for guild in bot.guilds:
        if guild.voice_client and isinstance(guild.voice_client, voice_recv.VoiceRecvClient):
            sink = guild.voice_client._reader.sink  # Changed from _sink to sink
            if sink and isinstance(sink, MySink):
                sink.debug_recording = not sink.debug_recording
                status = "ON" if sink.debug_recording else "OFF"
                await ctx.send(f"Debug recording turned {status}")
                debug_print(f"Debug recording turned {status}")
                return
    await ctx.send("Bot is not in a voice channel or sink not initialized")

@bot.command()
async def playback(ctx, file_num: int):
    """Play back a debug audio file"""
    try:
        debug_file = f'debug_combined_{file_num}.wav'
        if os.path.exists(debug_file):
            if ctx.voice_client:
                # Stop any currently playing audio
                if ctx.voice_client.is_playing():
                    ctx.voice_client.stop()
                
                ctx.voice_client.play(
                    discord.FFmpegPCMAudio(debug_file),
                    after=lambda e: debug_print(f'Done playing debug file {file_num}' if not e else f'Error playing file: {e}')
                )
                await ctx.send(f"Playing debug file {file_num}")
            else:
                await ctx.send("Bot is not in a voice channel")
        else:
            await ctx.send(f"Debug file {file_num} not found")
    except Exception as e:
        debug_print(f"Error in playback: {e}")
        await ctx.send(f"Error playing file: {e}")

def cleanup():
    if DEBUG_MODE:
        debug_print("Cleaning up...")
    
    # Silently turn off all LEDs
    for index in range(8):
        try:
            bs.set_color(channel=0, index=index, red=0, green=0, blue=0)
        except:
            pass

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
        icon_image = Image.new('RGB', (64, 64), color = 'red')
    
    def exit_action(icon):
        try:
            # Stop keyboard listener
            keyboard.unhook_all()
            
            # Cleanup resources silently
            cleanup()
            
            # Stop the icon
            icon.stop()
            
            # Exit without using sys.exit
            os._exit(0)
        except:
            os._exit(0)

    def show_status(icon):
        status_window.show()

    menu = (
        item('Status', show_status),
        item('Exit', exit_action)
    )

    return pystray.Icon("DiscordBot", icon_image, "Discord Bot", menu)

# Modify your main code to run in a separate thread
def run_bot():
    config = load_config()
    if config and 'token' in config:
        bot.run(config['token'])
    else:
        print("Error: Could not load bot token from config file")

# Main execution
if __name__ == "__main__":
    # Start key listener in a separate thread
    key_thread = threading.Thread(target=start_key_listener, daemon=True)
    key_thread.start()
    debug_print(f"Started key listener thread with hotkey: {HOTKEY}")

    # Create and start the bot thread
    bot_thread = threading.Thread(target=run_bot, daemon=True)
    bot_thread.start()
    debug_print("Started bot thread")

    # Create and run the system tray icon (this blocks)
    icon = create_tray_icon()
    icon.run()