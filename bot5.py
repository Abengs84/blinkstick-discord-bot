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
import openai  # Add OpenAI import

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

# Default LED colors if not found in config
DEFAULT_LED_COLORS = {
    "target_voice": {"red": 255, "green": 0, "blue": 0},
    "other_voice": {"red": 0, "green": 0, "blue": 255},
    "hotkey": {"red": 60, "green": 0, "blue": 0},
    "notification": {"red": 255, "green": 204, "blue": 0},
    "gpt_activity": {"red": 128, "green": 0, "blue": 128},
    "power_on": {"red": 0, "green": 100, "blue": 0}
}

# Load config before using debug_print
config = load_config()
if config:
    DEBUG_MODE = config.get('debug_mode', True)
    TARGET_USER = config.get('target_user', 'USER')
    led_on = config.get('led_enabled', False)
    HOTKEY = config.get('hotkey', 'ctrl+shift+alt+o')
    OPENAI_API_KEY = config.get('openai_api_key', '')
    GPT_MODEL = config.get('gpt_model', 'gpt-3.5-turbo')
    LED_COLORS = config.get('led_colors', DEFAULT_LED_COLORS)  # Use default colors if not in config
    
    # Initialize OpenAI
    if OPENAI_API_KEY:
        openai.api_key = OPENAI_API_KEY
        debug_print("OpenAI API key loaded successfully")
    else:
        debug_print("Warning: OpenAI API key not found in config")
else:
    # Fallback defaults if config fails to load
    DEBUG_MODE = True
    TARGET_USER = "USER"
    led_on = False
    HOTKEY = 'ctrl+shift+alt+o'
    OPENAI_API_KEY = ''
    GPT_MODEL = 'gpt-3.5-turbo'
    LED_COLORS = DEFAULT_LED_COLORS  # Use default colors
    debug_print("Warning: Config file not found, using default values")

# Now handle Opus loading
if not discord.opus.is_loaded():
    try:
        if platform.system() == 'Windows':
            # Try to load libopus-0.dll
            try:
                discord.opus.load_opus('libopus-0.dll')
                debug_print("Successfully loaded libopus-0.dll")
            except Exception as e:
                debug_print(f"Failed to load libopus-0.dll: {e}")
    except Exception as e:
        debug_print(f"Failed to load Opus: {e}")
        debug_print("Voice functionality may be limited")

# Initialize the BlinkStick
def initialize_blinkstick():
    global bs
    try:
        # Find all connected BlinkSticks
        all_sticks = blinkstick.find_all()
        if not all_sticks:
            debug_print("No BlinkStick devices found. Please check USB connection.")
            return False

        # Look for the specific BlinkStick
        target_serial = "BS061825-3.0"
        for stick in all_sticks:
            try:
                serial = stick.get_serial()
                if serial == target_serial:
                    bs = stick
                    # Validate device is responsive
                    if not bs.get_description():
                        debug_print(f"Found target BlinkStick {target_serial} but device is not responding")
                        continue
                    debug_print(f"Found and validated target BlinkStick: {target_serial}")
                    # Test LED functionality
                    bs.turn_off()
                    bs.set_color(channel=0, index=0, red=0, green=255, blue=0)
                    time.sleep(0.1)
                    bs.turn_off()
                    return True
            except Exception as e:
                debug_print(f"Error checking BlinkStick {serial}: {str(e)}")
                continue
        
        debug_print(f"Target BlinkStick {target_serial} not found, found devices: {[stick.get_serial() for stick in all_sticks]}")
        # Fallback to first available if target not found
        for stick in all_sticks:
            try:
                bs = stick
                if bs.get_description():  # Validate device is responsive
                    debug_print(f"Using fallback BlinkStick: {bs.get_serial()}")
                    # Test LED functionality
                    bs.turn_off()
                    bs.set_color(channel=0, index=0, red=0, green=255, blue=0)
                    time.sleep(0.1)
                    bs.turn_off()
                    return True
            except Exception as e:
                debug_print(f"Error with fallback device: {str(e)}")
                continue
        
        debug_print("No responsive BlinkStick devices found")
        return False
        
    except Exception as e:
        debug_print(f"Error initializing BlinkStick: {str(e)}")
        return False

# Replace the existing BlinkStick initialization with the new function
if not initialize_blinkstick():
    print("ERROR: No BlinkStick found or device not responding! Please check USB connection.")
    debug_print("Bot will continue without LED functionality")
else:
    print(f"BlinkStick initialized successfully: {bs.get_description()} (Serial: {bs.get_serial()})")

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
        """Generate speech using OpenAI's TTS API"""
        try:
            client = openai.OpenAI(api_key=OPENAI_API_KEY)
            with client.audio.speech.with_streaming_response.create(
                model="tts-1",
                voice="alloy",
                input=text
            ) as response:
                response.stream_to_file(output_file)
            debug_print(f"Generated speech saved to {output_file}")
        except Exception as e:
            debug_print(f"Error generating speech: {e}")
            raise

    def __init__(self):
        self.speaking_states = {}
        self.audio_queue = Queue()
        self.recognizer = sr.Recognizer()
        self.wake_phrases = {
            "hey gpt": "chatgpt"
        }
        self.additional_commands = {
            "play sound": "sound"
        }
        self.sound_file = resource_path('sounds/Glenn.mp3')
        debug_print(f"Sound file path: {self.sound_file}")
        debug_print(f"Sound file exists: {os.path.exists(self.sound_file)}")
        
        # Check if sound file exists
        if not os.path.exists(self.sound_file):
            debug_print(f"Warning: Sound file not found at {self.sound_file}")
            sounds_dir = resource_path('sounds')
            if os.path.exists(sounds_dir):
                debug_print(f"Contents of sounds directory: {os.listdir(sounds_dir)}")
        
        self.is_speaking = False
        self.is_chatgpt_mode = False
        self.is_asleep = False  # New state for sleep mode
        self.conversation_history = []
        self.is_processing = False
        self.processing_lock = asyncio.Lock()
        self.last_response_time = 0
        self.last_processed_text = None
        self.debounce_timer = None
        self.debounce_delay = 0.5
        self.min_audio_length = 16000
        
        # Audio settings
        self.input_sample_rate = 48000
        self.channels = 2
        self.buffer_duration = 2.0
        self.samples_per_buffer = int(self.input_sample_rate * self.buffer_duration)
        
        # Buffers
        self.original_buffer = []
        self.processed_buffer = np.array([], dtype=np.int16)
        self.accumulated_audio = np.array([], dtype=np.int16)
        
        # Debug settings
        self.debug_counter = 0
        self.debug_recording = False
        self.total_chunks = 0
        self.recognition_attempts = 0
        self.successful_recognitions = 0
        
        # Recognition settings
        self.recognizer.energy_threshold = 100
        self.recognizer.dynamic_energy_threshold = True
        self.recognizer.pause_threshold = 0.3
        self.recognizer.phrase_threshold = 0.1
        self.recognizer.non_speaking_duration = 0.1
        
        debug_print("MySink initialized with Discord voice format")
        
        self.recognition_task = asyncio.create_task(self.process_audio())

    def cleanup(self):
        """Required method: Clean up resources"""
        debug_print("Cleaning up MySink resources")
        set_led_color(channel=0, index=0, red=0, green=0, blue=0)
        set_led_color(channel=0, index=2, red=0, green=0, blue=0)
        set_led_color(channel=0, index=4, red=0, green=0, blue=0)  # Clean up GPT LED

    def wants_opus(self) -> bool:
        """Required method: Indicate if we want Opus encoded data"""
        return False  # We want PCM data instead of Opus

    def write(self, user, data):
        try:
            if (user.name.strip().lower() == TARGET_USER.lower()):
                # Store original PCM data if debug recording is enabled
                if self.debug_recording:
                    self.original_buffer.append(data.pcm)
                    self.debug_counter += 1
                
                # Process audio for recognition
                audio_array = np.frombuffer(data.pcm, dtype=np.int16)
                audio_array = audio_array.reshape(-1, 2)
                audio_mono = np.mean(audio_array, axis=1, dtype=np.int16)
                audio_resampled = audio_mono[::3]
                
                # Check for voice activity
                is_voice_active = np.max(np.abs(audio_mono)) > 500
                
                # Get the voice client and check speaking state
                voice_client = None
                for guild in bot.guilds:
                    if guild.voice_client:
                        voice_client = guild.voice_client
                        break
                
                is_speaking = voice_client.get_speaking(user) if voice_client else False
                
                if is_voice_active or is_speaking:
                    if not self.is_speaking:
                        debug_print(f"Voice activity detected from {user.name} (PTT: {is_speaking}, Audio: {is_voice_active})")
                        self.is_speaking = True
                        if not self.is_asleep:  # Only set LED if not asleep
                            set_led_color(channel=0, index=0, 
                                       red=LED_COLORS['target_voice']['red'],
                                       green=LED_COLORS['target_voice']['green'],
                                       blue=LED_COLORS['target_voice']['blue'])
                        # Clear accumulated audio when starting to speak
                        self.accumulated_audio = np.array([], dtype=np.int16)
                    
                    # Accumulate audio while speaking
                    self.accumulated_audio = np.concatenate([self.accumulated_audio, audio_resampled])
                    debug_print(f"Accumulated audio length: {len(self.accumulated_audio)} samples")
                else:
                    if self.is_speaking:
                        debug_print(f"Voice activity stopped from {user.name}")
                        self.is_speaking = False
                        if not self.is_asleep:  # Only set LED if not asleep
                            set_led_color(channel=0, index=0, red=0, green=0, blue=0)
                        
                        # Process accumulated audio when speaking stops
                        if len(self.accumulated_audio) > 0:
                            debug_print(f"Queueing audio for processing: {len(self.accumulated_audio)} samples")
                            if self.debug_recording:
                                # Save debug files
                                debug_file = f'debug_processed_{self.debug_counter}.wav'
                                with wave.open(debug_file, 'wb') as wf:
                                    wf.setnchannels(1)
                                    wf.setsampwidth(2)
                                    wf.setframerate(16000)
                                    wf.writeframes(self.accumulated_audio.tobytes())
                                debug_print(f"Saved processed audio to {debug_file}")
                            
                            # Queue the audio for processing
                            self.audio_queue.put(self.accumulated_audio.copy())
                            debug_print(f"Audio queue size: {self.audio_queue.qsize()}")
                            self.accumulated_audio = np.array([], dtype=np.int16)
                
                # Add to processed buffer for other processing
                self.processed_buffer = np.concatenate([self.processed_buffer, audio_resampled])
        except Exception as e:
            debug_print(f"Error in write: {e}")
            # Turn off LED if there's an error
            set_led_color(channel=0, index=0, red=0, green=0, blue=0)

    @voice_recv.AudioSink.listener()
    def on_voice_member_speaking_start(self, member: discord.Member):
        debug_print(f"Voice start detected for {member.name}")
        self.speaking_states[member.name] = True
        if member.name.strip().lower() == TARGET_USER.lower():
            debug_print("Setting red LED for target user")
            self.is_speaking = True
            set_led_color(channel=0, index=0, 
                         red=LED_COLORS['target_voice']['red'],
                         green=LED_COLORS['target_voice']['green'],
                         blue=LED_COLORS['target_voice']['blue'])
        else:
            debug_print("Setting blue LED for other user")
            set_led_color(channel=0, index=2, 
                         red=LED_COLORS['other_voice']['red'],
                         green=LED_COLORS['other_voice']['green'],
                         blue=LED_COLORS['other_voice']['blue'])

    @voice_recv.AudioSink.listener()
    def on_voice_member_speaking_stop(self, member: discord.Member):
        debug_print(f"Voice stop detected for {member.name}")
        self.speaking_states[member.name] = False
        if member.name.strip().lower() == TARGET_USER.lower():
            debug_print("Turning off red LED")
            self.is_speaking = False
            # Clear the audio queue when stopping speaking
            while not self.audio_queue.empty():
                self.audio_queue.get()
            set_led_color(channel=0, index=0, red=0, green=0, blue=0)
        else:
            debug_print("Turning off blue LED")
            set_led_color(channel=0, index=2, red=0, green=0, blue=0)

    async def get_chatgpt_response(self, text):
        """Get response from ChatGPT"""
        try:
            # Turn on purple LED for GPT activity
            set_led_color(channel=0, index=4, 
                         red=LED_COLORS['gpt_activity']['red'],
                         green=LED_COLORS['gpt_activity']['green'],
                         blue=LED_COLORS['gpt_activity']['blue'])
            
            # Add user message to conversation history
            self.conversation_history.append({"role": "user", "content": text})
            
            # Get response from ChatGPT using the new API format
            client = openai.OpenAI(api_key=OPENAI_API_KEY)
            response = client.chat.completions.create(
                model=GPT_MODEL,
                messages=self.conversation_history,
                max_tokens=150
            )
            
            # Extract and store the response
            chatgpt_response = response.choices[0].message.content
            self.conversation_history.append({"role": "assistant", "content": chatgpt_response})
            
            return chatgpt_response
        except Exception as e:
            debug_print(f"Error getting ChatGPT response: {e}")
            return "I'm sorry, I encountered an error processing your request."
        finally:
            # Turn off purple LED when done
            set_led_color(channel=0, index=4, red=0, green=0, blue=0)

    async def process_audio(self):
        while True:
            try:
                if not self.audio_queue.empty():
                    debug_print(f"Processing audio from queue (size: {self.audio_queue.qsize()})")
                    async with self.processing_lock:
                        if self.is_processing:
                            debug_print("Skipping processing - already in progress")
                            continue
                            
                        current_time = time.time()
                        if current_time - self.last_response_time < 2.0:  # 2 second cooldown
                            debug_print("Skipping response due to cooldown")
                            continue
                            
                        # Cancel any existing debounce timer
                        if self.debounce_timer is not None:
                            debug_print("Canceling existing debounce timer")
                            self.debounce_timer.cancel()
                            
                        self.is_processing = True
                        
                        # Get and clear the queue in one operation
                        audio_data = None
                        while not self.audio_queue.empty():
                            audio_data = self.audio_queue.get()
                            debug_print(f"Retrieved audio data: {len(audio_data) if audio_data is not None else 0} samples")
                        
                        if audio_data is None or len(audio_data) < self.min_audio_length:
                            debug_print(f"No valid audio data retrieved from queue (length: {len(audio_data) if audio_data is not None else 0})")
                            self.is_processing = False
                            continue
                        
                        # Create a new debounce timer
                        debug_print("Creating new debounce timer")
                        self.debounce_timer = asyncio.create_task(self._debounce_processing(audio_data))
                        
            except Exception as e:
                debug_print(f"Error in process_audio: {e}")
                self.is_processing = False
            await asyncio.sleep(0.1)

    async def _debounce_processing(self, audio_data):
        """Process audio with debounce delay"""
        try:
            await asyncio.sleep(self.debounce_delay)
            
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as temp_wav:
                with wave.open(temp_wav.name, 'wb') as wf:
                    wf.setnchannels(1)
                    wf.setsampwidth(2)
                    wf.setframerate(16000)
                    wf.writeframes(audio_data.tobytes())
                
                try:
                    with sr.AudioFile(temp_wav.name) as source:
                        audio = self.recognizer.record(source)
                        text = self.recognizer.recognize_google(audio, language='en-US').lower()
                        debug_print(f"Recognized text: '{text}'")
                        
                        # Skip if this is the same text we just processed
                        if text == self.last_processed_text:
                            debug_print("Skipping duplicate text")
                            return
                        
                        self.last_processed_text = text
                        self.last_response_time = time.time()
                        
                        # Handle sleep mode commands first
                        if "go to sleep" in text or "good night" in text:
                            if not self.is_asleep:
                                self.is_asleep = True
                                self.is_chatgpt_mode = False
                                response = "Good night! I'll be here when you need me."
                                await self.generate_and_play_speech(response)
                                set_led_color(channel=0, index=0, red=0, green=0, blue=0)  # Turn off LED
                                return
                        
                        if "wake up" in text or "good morning" in text:
                            if self.is_asleep:
                                self.is_asleep = False
                                response = "Good morning! I'm awake and ready to help."
                                await self.generate_and_play_speech(response)
                                return
                        
                        if "goodbye" in text or "see you later" in text:
                            self.is_asleep = True
                            self.is_chatgpt_mode = False
                            response = "Goodbye! Have a great day!"
                            await self.generate_and_play_speech(response)
                            set_led_color(channel=0, index=0, red=0, green=0, blue=0)  # Turn off LED
                            return
                        
                        # If bot is asleep, only respond to wake commands
                        if self.is_asleep:
                            debug_print("Bot is asleep, ignoring command")
                            return
                        
                        # Check for wake phrase
                        for phrase, command_type in self.wake_phrases.items():
                            if phrase in text:
                                if command_type == "chatgpt":
                                    self.is_chatgpt_mode = True
                                    response = "Hello! I'm now in ChatGPT mode. How can I help you?"
                                    await self.generate_and_play_speech(response)
                                    return
                        
                        # If in ChatGPT mode, process the text
                        if self.is_chatgpt_mode:
                            response = await self.get_chatgpt_response(text)
                            await self.generate_and_play_speech(response)
                            return
                        
                        # Handle other commands
                        elif "play sound" in text:
                            await self.play_sound()
                            return
                        
                except sr.UnknownValueError:
                    debug_print("Speech was unclear")
                except sr.RequestError as e:
                    debug_print(f"Recognition error: {e}")
                finally:
                    try:
                        os.unlink(temp_wav.name)
                    except:
                        pass
        finally:
            self.is_processing = False
            self.debounce_timer = None

    async def generate_and_play_speech(self, text):
        """Generate speech from text and play it"""
        if not text:
            debug_print("Skipping speech generation - no text")
            return

        temp_mp3 = None
        try:
            # Turn on purple LED for speech generation
            set_led_color(channel=0, index=4, 
                         red=LED_COLORS['gpt_activity']['red'],
                         green=LED_COLORS['gpt_activity']['green'],
                         blue=LED_COLORS['gpt_activity']['blue'])
            
            temp_mp3 = tempfile.NamedTemporaryFile(suffix=".mp3", delete=False)
            temp_mp3.close()  # Close the file so we can write to it
            
            debug_print(f"Generating speech for text: '{text}'")
            await self.generate_speech(text, temp_mp3.name)
            
            for guild in bot.guilds:
                if guild.voice_client:
                    # Stop any currently playing audio
                    if guild.voice_client.is_playing():
                        guild.voice_client.stop()
                        await asyncio.sleep(0.1)  # Small delay to ensure stop is processed
                    
                    # Create an event to track when playback is complete
                    play_complete = asyncio.Event()
                    
                    def after_playing(error):
                        if error:
                            debug_print(f"Error playing speech: {error}")
                        play_complete.set()
                    
                    # Play the new audio
                    guild.voice_client.play(
                        discord.FFmpegPCMAudio(temp_mp3.name),
                        after=after_playing
                    )
                    
                    # Wait for playback to complete
                    await play_complete.wait()
                    break  # Only play in one guild
            
        except Exception as e:
            debug_print(f"Error generating/playing speech: {e}")
        finally:
            # Turn off purple LED when done
            set_led_color(channel=0, index=4, red=0, green=0, blue=0)
            
            # Clean up the file after playback is complete
            if temp_mp3 and os.path.exists(temp_mp3.name):
                try:
                    await asyncio.sleep(0.5)  # Wait for FFmpeg to release the file
                    os.unlink(temp_mp3.name)
                    debug_print(f"Successfully cleaned up temp file: {temp_mp3.name}")
                except Exception as e:
                    debug_print(f"Error cleaning up temp file: {e}")

    async def play_sound(self):
        """Play the sound file"""
        for guild in bot.guilds:
            if guild.voice_client:
                try:
                    if os.path.exists(self.sound_file):
                        guild.voice_client.play(
                            discord.FFmpegPCMAudio(self.sound_file),
                            after=lambda e: debug_print(f"Finished playing sound" if not e else f"Error playing sound: {e}")
                        )
                except Exception as e:
                    debug_print(f"Error playing sound file: {e}")
                break

# Custom bot class
class MyBot(commands.Bot):
    async def setup_hook(self):
        debug_print("Running setup_hook...")
        
        if not hasattr(self, '_scheduled_announcement_task_started'):
            self.scheduled_announcement.start()
            self._scheduled_announcement_task_started = True
        
        for guild in self.guilds:
            debug_print(f"\nChecking guild: {guild.name} (ID: {guild.id})")
            
            # Only print members in voice channels
            for vc in guild.voice_channels:
                if vc.members:  # Only print if there are members
                    members_in_vc = [f"{m.name}#{m.discriminator}" for m in vc.members]
                    debug_print(f"Voice channel '{vc.name}' members: {members_in_vc}")
            
            for member in guild.members:
                # Check both name and name#discriminator
                target_matches = [
                    member.name == TARGET_USER,  # Exact name match
                    f"{member.name}#{member.discriminator}" == TARGET_USER,  # Full discriminator match
                    member.name.lower() == TARGET_USER.lower(),  # Case-insensitive name match
                    f"{member.name}#{member.discriminator}".lower() == TARGET_USER.lower()  # Case-insensitive full match
                ]
                
                if any(target_matches):
                    debug_print(f"Found target user: {member.name}#{member.discriminator}")
                    if member.voice:
                        channel = member.voice.channel
                        debug_print(f"{TARGET_USER} is in channel: {channel.name}")
                        try:
                            # Try connecting with voice_recv directly
                            debug_print("Attempting to connect with voice_recv client...")
                            vc = await channel.connect(cls=voice_recv.VoiceRecvClient)
                            debug_print("Successfully connected with voice_recv client")
                            
                            # Initialize and attach the sink
                            sink = MySink()
                            vc.listen(sink)
                            debug_print("Successfully attached audio sink")
                            
                            # Set up error handling
                            @vc.error
                            async def on_error(error):
                                debug_print(f"Voice client error: {error}")
                                if isinstance(error, Exception):
                                    debug_print(f"Error details: {str(error)}")
                            
                            return
                            
                        except Exception as e:
                            debug_print(f"Error connecting to voice channel: {e}")
                            if hasattr(e, '__cause__') and e.__cause__:
                                debug_print(f"Cause: {e.__cause__}")
                            if hasattr(e, '__context__') and e.__context__:
                                debug_print(f"Context: {e.__context__}")
                            
                            # Try fallback method
                            try:
                                debug_print("Attempting fallback connection method...")
                                vc = await channel.connect()
                                debug_print("Successfully connected with regular voice client")
                                
                                # Try to upgrade to voice_recv
                                try:
                                    voice_recv_client = voice_recv.VoiceRecvClient.from_client(vc)
                                    sink = MySink()
                                    voice_recv_client.listen(sink)
                                    debug_print("Successfully upgraded to voice_recv client")
                                    return
                                except Exception as upgrade_error:
                                    debug_print(f"Failed to upgrade to voice_recv: {upgrade_error}")
                                    # Keep using regular voice client
                                    return
                                    
                            except Exception as fallback_error:
                                debug_print(f"Fallback connection failed: {fallback_error}")
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
                        # First generate and play the speech
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
                            await asyncio.sleep(1)  # Small delay between sounds
                        
                        # Then play the MP3 file
                        sound_file = resource_path('sounds/1900.mp3')
                        if os.path.exists(sound_file):
                            debug_print("Playing 1900.mp3")
                            voice_client.play(
                                discord.FFmpegPCMAudio(sound_file),
                                after=lambda e: debug_print(f"Finished playing sound" if not e else f"Error playing sound: {e}")
                            )
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
            set_led_color(channel=0, index=1, 
                         red=LED_COLORS['hotkey']['red'],
                         green=LED_COLORS['hotkey']['green'],
                         blue=LED_COLORS['hotkey']['blue'])
            led_on = True
        else:
            debug_print("Key combination pressed! Turning LED off.")
            set_led_color(channel=0, index=1, red=0, green=0, blue=0)
            led_on = False
    except Exception as e:
        debug_print(f"Error changing LED color: {e}")

# Function to handle the power-on sequence
async def power_on_sequence():
    # Cycle through all 8 indexes with a short 50ms delay between each
    for i in range(8):
        set_led_color(channel=0, index=i, 
                     red=LED_COLORS['power_on']['red'],
                     green=LED_COLORS['power_on']['green'],
                     blue=LED_COLORS['power_on']['blue'])
        await asyncio.sleep(0.05)  # 50ms delay between each LED
        set_led_color(channel=0, index=i, red=0, green=0, blue=0)  # Turn off LED
    
    # Flash all indexes with a longer duration
    for i in range(8):
        set_led_color(channel=0, index=i, 
                     red=LED_COLORS['power_on']['red'],
                     green=LED_COLORS['power_on']['green'],
                     blue=LED_COLORS['power_on']['blue'])
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
        # Turn off the LED when user stops speaking
        set_led_color(channel=0, index=0, red=0, green=0, blue=0)
        # Also update the speaking state in the sink if it exists
        for guild in bot.guilds:
            if guild.voice_client and isinstance(guild.voice_client, voice_recv.VoiceRecvClient):
                sink = guild.voice_client._reader.sink
                if sink and isinstance(sink, MySink):
                    sink.is_speaking = False
                    while not sink.audio_queue.empty():
                        sink.audio_queue.get()

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
            # Only check voice channels with members
            for vc in guild.voice_channels:
                if vc.members:
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
                            red=int(brightness * (LED_COLORS['notification']['red'] / 255)),
                            green=int(brightness * (LED_COLORS['notification']['green'] / 255)),
                            blue=int(brightness * (LED_COLORS['notification']['blue'] / 255)))
                await asyncio.sleep(0.05)
            
            # Pulse down
            for brightness in range(max_brightness, 0, -5):
                set_led_color(channel=0, index=3, 
                            red=int(brightness * (LED_COLORS['notification']['red'] / 255)),
                            green=int(brightness * (LED_COLORS['notification']['green'] / 255)),
                            blue=int(brightness * (LED_COLORS['notification']['blue'] / 255)))
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
async def testfriday(ctx):
    """Test the Friday announcement"""
    try:
        debug_print("Testing Friday announcement")
        
        # Check if bot is connected to any voice channel
        if ctx.author.voice and ctx.author.voice.channel:
            try:
                # Connect to the user's voice channel if not already connected
                if not ctx.voice_client:
                    await ctx.author.voice.channel.connect()
                
                # Create an event to track when playback is complete
                play_complete = asyncio.Event()
                
                # First generate and play the speech
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
                        play_complete.set()
                    
                    ctx.voice_client.play(discord.FFmpegPCMAudio(temp_path), after=after_play)
                    debug_print("Started playing announcement")
                
                # Wait for the first sound to finish
                await play_complete.wait()
                await asyncio.sleep(0.5)  # Small delay between sounds
                
                # Reset the event for the second sound
                play_complete.clear()
                
                # Then play the MP3 file
                sound_file = resource_path('sounds/1900.mp3')
                if os.path.exists(sound_file):
                    debug_print("Playing 1900.mp3")
                    ctx.voice_client.play(
                        discord.FFmpegPCMAudio(sound_file),
                        after=lambda e: play_complete.set()
                    )
                    await play_complete.wait()  # Wait for the second sound to finish
                
                await ctx.send("Testing Friday announcement!")
            except Exception as e:
                debug_print(f"Error during test announcement: {e}")
                await ctx.send(f"Error: {str(e)}")
        else:
            await ctx.send("You need to be in a voice channel to test the announcement!")
    except Exception as e:
        debug_print(f"Error in testfriday command: {e}")
        await ctx.send(f"Error: {str(e)}")

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