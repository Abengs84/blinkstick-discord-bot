import discord
from discord.ext import commands, voice_recv, tasks
import asyncio
import datetime
from typing import Optional, Dict, Any, Callable
from src.audio.tts import TTSManager
from src.audio.playback import AudioPlayer
from src.utils.led_control import LEDController
import numpy as np
import threading
import time
import functools
import logging
import math

# Monkey patch the voice_recv library to handle None channel_id
original_on_voice_state_update = voice_recv.VoiceRecvClient.on_voice_state_update

@functools.wraps(original_on_voice_state_update)
async def safe_on_voice_state_update(self, data):
    try:
        # Handle None channel_id gracefully
        if 'channel_id' in data and data['channel_id'] is None:
            # This means we've disconnected, so we should clean up
            if hasattr(self, 'sink') and self.sink:
                self.sink.cleanup()
            return
        
        # Call the original method
        await original_on_voice_state_update(self, data)
    except Exception as e:
        print(f"Error in voice state update handler (monkey patched): {e}")

# Apply the monkey patch
voice_recv.VoiceRecvClient.on_voice_state_update = safe_on_voice_state_update

class DiscordBot(commands.Bot):
    def __init__(self, config: Dict[str, Any], debug_print_func: Callable = print,
                 status_callback: Optional[Callable[[str], None]] = None,
                 channel_callback: Optional[Callable[[str], None]] = None):
        intents = discord.Intents.default()
        intents.message_content = True
        intents.voice_states = True
        intents.members = True
        
        # Get or create event loop
        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
        
        super().__init__(command_prefix='!', intents=intents, loop=loop)
        
        self.config = config
        self.debug_print = debug_print_func
        self.status_callback = status_callback
        self.channel_callback = channel_callback
        self.led_controller = LEDController(debug_print_func)
        self.tts_manager = TTSManager(config.get('openai_api_key', ''), debug_print_func)
        self._scheduled_announcement_task_started = False
        
        # Voice client management
        self._voice_clients = {}  # guild_id -> voice_client
        self._connection_queue = asyncio.Queue()
        self._connection_lock = asyncio.Lock()
        self._connection_tasks = {}
        self._current_channel_id = None
        self._connection_manager_task = None
        
        # Initialize task tracking
        self.running_tasks = []
        self.scheduled_announcements = []
        self._loop = loop
        
        # Register event handlers
        self.setup_events()
        
    def setup_events(self):
        @self.event
        async def on_ready():
            self.debug_print(f"Bot is ready! Logged in as {self.user.name} ({self.user.id})")
            # Update status immediately on connection to Discord
            if self.status_callback:
                self.status_callback("Connected to Discord")
            
            # Small delay to ensure UI can update
            await asyncio.sleep(0.5)
            
            # Now check for guilds where the target user is present
            await self._check_all_guilds()
            
        @self.event
        async def on_voice_state_update(member, before, after):
            """Handle voice state updates"""
            try:
                # Process updates for our bot
                if member.id == self.user.id:
                    # Handle channel changes
                    if before.channel != after.channel:
                        # Handle leaving a channel
                        if before.channel and hasattr(before.channel, 'id') and before.channel.id:
                            try:
                                guild_id = before.channel.guild.id
                                if guild_id in self._voice_clients:
                                    voice_client = self._voice_clients[guild_id]
                                    if voice_client and voice_client.is_connected():
                                        try:
                                            # Clear voice state handlers first
                                            if hasattr(voice_client, '_voice_state_update_handler'):
                                                voice_client._voice_state_update_handler = None
                                            await voice_client.disconnect()
                                        except Exception as e:
                                            self.debug_print(f"Error disconnecting from channel {before.channel.id}: {e}")
                                    del self._voice_clients[guild_id]
                            except (ValueError, TypeError) as e:
                                self.debug_print(f"Error handling voice state update (before): {e}")
                        
                        # Handle joining a channel
                        if after.channel and hasattr(after.channel, 'id') and after.channel.id:
                            try:
                                guild_id = after.channel.guild.id
                                if guild_id not in self._voice_clients:
                                    # Add connection request to queue
                                    await self._connection_queue.put((guild_id, after.channel.id, None))
                            except (ValueError, TypeError) as e:
                                self.debug_print(f"Error handling voice state update (after): {e}")
                    return
                
                # Check if the member is the target user
                target_user = self.config.get('target_user', '').lower()
                is_target_user = member.name.lower() == target_user or str(member).lower() == target_user
                
                if is_target_user:
                    # Target user has left a voice channel
                    if before.channel and (after.channel is None or before.channel.id != after.channel.id):
                        self.debug_print(f"Target user {member.name} left voice channel {before.channel.name}")
                        
                        # Check if bot is in the same channel that the user left
                        guild_id = before.channel.guild.id
                        if guild_id in self._voice_clients:
                            voice_client = self._voice_clients[guild_id]
                            if voice_client and voice_client.is_connected() and voice_client.channel.id == before.channel.id:
                                self.debug_print(f"Target user left the channel, disconnecting bot from {before.channel.name}")
                                await self.disconnect_voice()
                                
                    # Target user has joined a voice channel
                    elif after.channel and (before.channel is None or before.channel.id != after.channel.id):
                        self.debug_print(f"Target user {member.name} joined voice channel {after.channel.name}")
                        
                        try:
                            # Connect to the channel the target user joined
                            guild_id = after.channel.guild.id
                            channel_id = after.channel.id
                            guild = self.get_guild(guild_id)
                            
                            if not guild:
                                self.debug_print(f"Could not find guild with ID {guild_id}")
                                return
                            
                            channel = guild.get_channel(channel_id)
                            if not channel:
                                self.debug_print(f"Could not find channel with ID {channel_id}")
                                return
                            
                            # Cancel any pending connection requests to avoid conflicts
                            while not self._connection_queue.empty():
                                try:
                                    self._connection_queue.get_nowait()
                                    self.debug_print("Cleared a pending connection request from queue")
                                except asyncio.QueueEmpty:
                                    break
                            
                            # Check if guild still has a voice client with lingering connection
                            if guild.voice_client:
                                self.debug_print(f"Found existing voice client in guild, disconnecting first")
                                try:
                                    await guild.voice_client.disconnect(force=True)
                                    # Clear any references to this voice client
                                    if guild_id in self._voice_clients:
                                        del self._voice_clients[guild_id]
                                    await asyncio.sleep(1.0)  # Longer delay to ensure disconnect completes
                                except Exception as e:
                                    self.debug_print(f"Error disconnecting existing voice client: {e}")
                            
                            # Connect directly to the channel
                            self.debug_print(f"Directly connecting to channel {channel.name}")
                            try:
                                # Connect with a timeout
                                voice_client = await asyncio.wait_for(
                                    channel.connect(cls=voice_recv.VoiceRecvClient),
                                    timeout=10.0
                                )
                                
                                self.debug_print(f"Successfully connected to voice channel {channel.name}")
                                self._voice_clients[guild_id] = voice_client
                                self._current_channel_id = channel_id
                                
                                # Setup audio sink
                                if not hasattr(voice_client, 'sink'):
                                    voice_client.sink = MySink(
                                        self.tts_manager,
                                        self.led_controller,
                                        self.config, 
                                        self.debug_print
                                    )
                                    voice_client.listen(voice_client.sink)
                                    
                                # Update UI with connection status
                                if self.status_callback:
                                    self.status_callback("Connected to Discord")
                                if self.channel_callback:
                                    channel_name = channel.name if hasattr(channel, 'name') else f"Channel {channel_id}"
                                    self.channel_callback(f"{channel_name}")
                                    
                                # Set connected flag in UI if available
                                if hasattr(self, 'ui_callbacks') and self.ui_callbacks:
                                    if hasattr(self.ui_callbacks, 'update_connection_status'):
                                        self.ui_callbacks.update_connection_status(True)
                                    elif hasattr(self.ui_callbacks, 'on_connection_status'):
                                        self.ui_callbacks.on_connection_status(True, f"Connected to {channel.name}")
                                    
                                # Schedule a status update after 1 second to ensure UI reflects correct state
                                self.loop.create_task(self._delayed_status_update(guild_id, channel_id))
                                
                            except asyncio.TimeoutError:
                                self.debug_print(f"Connection to channel {channel.name} timed out")
                            except Exception as e:
                                self.debug_print(f"Error connecting to channel: {e}")
                        except Exception as e:
                            self.debug_print(f"Error handling target user channel join: {e}")
                
            except Exception as e:
                self.debug_print(f"Error in voice state update handler: {e}")
                
        @self.event
        async def on_disconnect():
            """Handle bot disconnection"""
            try:
                # Clear all voice clients
                for guild_id, voice_client in list(self._voice_clients.items()):
                    try:
                        if voice_client and voice_client.is_connected():
                            # Clear voice state handlers first
                            if hasattr(voice_client, '_voice_state_update_handler'):
                                voice_client._voice_state_update_handler = None
                            await voice_client.disconnect()
                    except Exception as e:
                        self.debug_print(f"Error disconnecting voice client {guild_id}: {e}")
                    finally:
                        del self._voice_clients[guild_id]
                
                # Clear connection queue
                while not self._connection_queue.empty():
                    try:
                        self._connection_queue.get_nowait()
                    except asyncio.QueueEmpty:
                        break
                        
                # Clear current channel
                self._current_channel_id = None
                
            except Exception as e:
                self.debug_print(f"Error in disconnect handler: {e}")
        
    async def _check_all_guilds(self):
        """Check all guilds for the target user"""
        self.debug_print("Checking all guilds for target user...")
        connected = False
        for guild in self.guilds:
            self.debug_print(f"Checking guild: {guild.name} (ID: {guild.id})")
            if await self._try_connect_to_target_user(guild):
                connected = True
                
        # Force channel status update for any existing connections
        await self._refresh_connection_status()
        
        return connected
            
    async def _refresh_connection_status(self):
        """Refresh the UI status based on current connections"""
        try:
            # Check for any active connections
            for guild_id, voice_client in list(self._voice_clients.items()):
                if voice_client and voice_client.is_connected() and voice_client.channel:
                    # Found an active connection, update the UI
                    # Use a more detailed debug level check to avoid cluttering logs
                    if self.config.get('debug_mode', False):
                        self.debug_print(f"Found active connection in channel {voice_client.channel.id}")
                    
                    # Get connection latency if available
                    latency_ms = 0
                    if hasattr(voice_client, 'latency'):
                        try:
                            if isinstance(voice_client.latency, float):
                                # Check for infinity or NaN before converting to int
                                if math.isinf(voice_client.latency) or math.isnan(voice_client.latency):
                                    self.debug_print("Voice latency is infinity or NaN, setting to 0")
                                    latency_ms = 0
                                else:
                                    latency_ms = int(voice_client.latency * 1000)
                            elif callable(voice_client.latency):
                                latency_value = voice_client.latency()
                                # Check for infinity or NaN before converting to int
                                if isinstance(latency_value, float) and (math.isinf(latency_value) or math.isnan(latency_value)):
                                    self.debug_print("Voice latency is infinity or NaN, setting to 0")
                                    latency_ms = 0
                                else:
                                    latency_ms = int(latency_value * 1000)
                        except Exception as e:
                            self.debug_print(f"Error getting latency: {e}")
                    
                    # Update the UI
                    if self.status_callback:
                        self.status_callback("Connected to Discord")
                    if self.channel_callback:
                        # Use the channel name rather than ID for better user experience
                        channel_name = voice_client.channel.name if hasattr(voice_client.channel, 'name') else f"Channel {voice_client.channel.id}"
                        self.channel_callback(f"{channel_name}")
                    
                    # Update latency if we have a UI callback for it
                    if hasattr(self, 'ui_callbacks') and self.ui_callbacks and hasattr(self.ui_callbacks, 'update_latency'):
                        self.ui_callbacks.update_latency(latency_ms)
                    
                    return True
        except Exception as e:
            self.debug_print(f"Error refreshing connection status: {e}")
            
        return False
        
    async def setup_hook(self):
        """Run setup after bot is ready"""
        try:
            self.debug_print("Running setup_hook...")
            
            # Start connection manager
            self._connection_manager_task = self.loop.create_task(self._connection_manager())
            
            if not hasattr(self, '_scheduled_announcement_task_started'):
                self.scheduled_announcement.start()
                self._scheduled_announcement_task_started = True
                
            # Start status refresh task
            self._status_refresh_task = self.loop.create_task(self._periodic_status_refresh())
                
            # Power on sequence for LED
            if self.config.get('led_enabled', False):
                power_on = self.config.get('led_colors', {}).get('power_on', {'red': 0, 'green': 100, 'blue': 0})
                self.led_controller.set_color(0, 0, 
                                           power_on['red'], 
                                           power_on['green'], 
                                           power_on['blue'])
                await asyncio.sleep(1)
                self.led_controller.turn_off()
            
            for guild in self.guilds:
                self.debug_print(f"Checking guild: {guild.name} (ID: {guild.id})")
                
                # Only print members in voice channels
                for vc in guild.voice_channels:
                    if vc.members:  # Only print if there are members
                        members_in_vc = [f"{m.name}#{m.discriminator}" for m in vc.members]
                        self.debug_print(f"Voice channel '{vc.name}' members: {members_in_vc}")
                
                await self._try_connect_to_target_user(guild)
                
        except Exception as e:
            self.debug_print(f"Error in setup_hook: {e}")
        
    async def _connection_manager(self):
        """Manage voice connections"""
        while True:
            try:
                # Get next connection request
                guild_id, channel_id, target_user = await self._connection_queue.get()
                
                async with self._connection_lock:
                    # Check if we already have a connection for this guild
                    if guild_id in self._voice_clients:
                        current_client = self._voice_clients[guild_id]
                        if current_client and current_client.is_connected():
                            if current_client.channel and current_client.channel.id == channel_id:
                                # Already in the right channel, skip connection
                                self.debug_print(f"Already connected to channel {channel_id} in guild {guild_id}")
                                continue
                            else:
                                # Disconnect from current channel
                                try:
                                    self.debug_print(f"Disconnecting from current channel {current_client.channel.id} to connect to new channel {channel_id}")
                                    await current_client.disconnect()
                                    # Add a small delay after disconnecting
                                    await asyncio.sleep(1)
                                except Exception as e:
                                    self.debug_print(f"Error disconnecting from current channel: {e}")
                    
                    # Ensure the guild still exists
                    guild = self.get_guild(guild_id)
                    if not guild:
                        self.debug_print(f"Guild {guild_id} not found")
                        continue
                        
                    # Ensure the channel still exists
                    channel = guild.get_channel(channel_id)
                    if not channel:
                        self.debug_print(f"Channel {channel_id} not found in guild {guild.name}")
                        continue
                    
                    # Double-check if we're already connected to this channel
                    if (guild.voice_client and guild.voice_client.is_connected() and 
                        guild.voice_client.channel and guild.voice_client.channel.id == channel_id):
                        self.debug_print(f"Already connected to channel {channel_id} in guild {guild.name}")
                        self._voice_clients[guild_id] = guild.voice_client
                        self._current_channel_id = channel_id
                        continue
                        
                    # Connect to new channel
                    try:
                        self.debug_print(f"Attempting to connect to channel {channel.name} ({channel_id}) in guild {guild.name}")
                        
                        # Try to connect with a timeout
                        try:
                            # Use a timeout to prevent hanging indefinitely if connection fails
                            voice_client = await asyncio.wait_for(
                                channel.connect(cls=voice_recv.VoiceRecvClient), 
                                timeout=10.0
                            )
                            
                            self.debug_print(f"Successfully connected to voice channel {channel.name}")
                            self._voice_clients[guild_id] = voice_client
                            self._current_channel_id = channel_id
                            
                            # Setup audio sink
                            if not hasattr(voice_client, 'sink'):
                                voice_client.sink = MySink(
                                    self.tts_manager,
                                    self.led_controller,
                                    self.config,
                                    self.debug_print
                                )
                                voice_client.listen(voice_client.sink)
                            
                            # Update UI with connection status
                            if self.status_callback:
                                self.status_callback("Connected to Discord")
                            if self.channel_callback:
                                # Use the channel name rather than ID for better user experience
                                channel_name = channel.name if hasattr(channel, 'name') else f"Channel {channel_id}"
                                self.channel_callback(f"{channel_name}")
                                
                            # Set connected flag in UI if available
                            if hasattr(self, 'ui_callbacks') and self.ui_callbacks:
                                if hasattr(self.ui_callbacks, 'update_connection_status'):
                                    self.ui_callbacks.update_connection_status(True)
                                elif hasattr(self.ui_callbacks, 'on_connection_status'):
                                    self.ui_callbacks.on_connection_status(True, f"Connected to {channel.name}")
                                    
                            # Schedule a status update after 1 second to ensure UI reflects correct state
                            self.loop.create_task(self._delayed_status_update(guild_id, channel_id))
                            
                        except asyncio.TimeoutError:
                            self.debug_print(f"Connection to channel {channel_id} timed out")
                            if guild_id in self._voice_clients:
                                del self._voice_clients[guild_id]
                            raise Exception("Connection timeout")
                            
                    except Exception as e:
                        self.debug_print(f"Error connecting to channel {channel_id}: {e}")
                        if guild_id in self._voice_clients:
                            del self._voice_clients[guild_id]
                        self._current_channel_id = None
                        
            except asyncio.CancelledError:
                break
            except Exception as e:
                self.debug_print(f"Error in connection manager: {e}")
                await asyncio.sleep(1)  # Prevent tight loop on errors

    async def _try_connect_to_channel(self, channel_id: int) -> bool:
        """Try to connect to a specific voice channel"""
        try:
            channel = self.get_channel(channel_id)
            if not channel:
                self.debug_print(f"Could not find channel with ID {channel_id}")
                return False
                
            # Add connection request to queue
            await self._connection_queue.put((channel.guild.id, channel_id, None))
            return True
            
        except Exception as e:
            self.debug_print(f"Error in _try_connect_to_channel: {e}")
            return False

    async def _try_connect_to_target_user(self, guild: discord.Guild) -> bool:
        """Try to connect to the target user's voice channel"""
        target_user = self.config.get('target_user', '')
        
        self.debug_print(f"Looking for target user '{target_user}' in guild '{guild.name}'")
        self.debug_print(f"Guild has {len(guild.members)} cached members")
        
        found_user = False
        
        for member in guild.members:
            # Check both name and name#discriminator, case insensitive
            member_full = str(member).lower()
            target_lower = target_user.lower()
            
            if member_full == target_lower or member.name.lower() == target_lower:
                found_user = True
                self.debug_print(f"Found target user: {member}")
                if member.voice and member.voice.channel:
                    # Add connection request to queue
                    channel_id = member.voice.channel.id
                    channel_name = member.voice.channel.name
                    self.debug_print(f"Target user is in voice channel: {channel_name} ({channel_id})")
                    
                    # Clear any existing connection to this guild first
                    if guild.id in self._voice_clients:
                        try:
                            existing_client = self._voice_clients[guild.id]
                            if existing_client and existing_client.is_connected():
                                self.debug_print(f"Disconnecting from existing voice connection in {guild.name}")
                                await existing_client.disconnect()
                        except Exception as e:
                            self.debug_print(f"Error disconnecting from existing connection: {e}")
                    
                    await self._connection_queue.put((guild.id, channel_id, member))
                    return True
                else:
                    self.debug_print(f"{target_user} was found in guild {guild.name} but is not in a voice channel")
        
        if not found_user:
            self.debug_print(f"Could not find {target_user} in guild {guild.name}")
        
        return False

    async def disconnect_voice(self):
        """Disconnect from all voice channels"""
        self.debug_print("Disconnecting from all voice channels")
        
        # Update UI status immediately
        if self.status_callback:
            self.status_callback("Disconnecting...")
        if self.channel_callback:
            self.channel_callback("Not connected")
            
        # Reset latency and connection status in UI if available
        if hasattr(self, 'ui_callbacks') and self.ui_callbacks:
            if hasattr(self.ui_callbacks, 'update_latency'):
                self.ui_callbacks.update_latency(0)
                self.debug_print("Reset latency display to 0")
            if hasattr(self.ui_callbacks, 'update_connection_status'):
                self.ui_callbacks.update_connection_status(False)
                self.debug_print("Set connection status to disconnected")
            if hasattr(self.ui_callbacks, 'update_voice_connection'):
                self.ui_callbacks.update_voice_connection("Disconnected", False)
                self.debug_print("Set voice connection to disconnected")
            if hasattr(self.ui_callbacks, 'on_connection_status'):
                self.ui_callbacks.on_connection_status(False, "Disconnected")
                self.debug_print("Updated connection status to disconnected")
            
        try:
            # First, check for any voice clients in guilds
            for guild in self.guilds:
                if guild.voice_client:
                    try:
                        self.debug_print(f"Disconnecting from voice in guild {guild.name}")
                        
                        # Force disconnect and cleanup
                        voice_client = guild.voice_client
                        if voice_client:  # Double-check to avoid NoneType errors
                            if hasattr(voice_client, '_voice_state_update_handler'):
                                voice_client._voice_state_update_handler = None
                                
                            if hasattr(voice_client, 'cleanup'):
                                voice_client.cleanup()
                                
                            # Use force disconnect as a last resort
                            if hasattr(voice_client, 'disconnect'):
                                try:
                                    await voice_client.disconnect(force=True)
                                except Exception as e:
                                    self.debug_print(f"Error during voice_client.disconnect: {e}")
                    except Exception as e:
                        self.debug_print(f"Error disconnecting from guild {guild.name}: {e}")
            
            # Add a delay to let Discord process disconnects
            await asyncio.sleep(1.0)
                    
            # Now check our tracking dictionary and clean up any remaining clients
            for guild_id, voice_client in list(self._voice_clients.items()):
                try:
                    if voice_client and voice_client.is_connected():
                        self.debug_print(f"Cleaning up tracked voice client for guild {guild_id}")
                        try:
                            if hasattr(voice_client, '_voice_state_update_handler'):
                                voice_client._voice_state_update_handler = None
                                
                            await voice_client.disconnect(force=True)
                        except Exception as e:
                            self.debug_print(f"Error during voice client disconnect: {str(e)}")
                except Exception as e:
                    self.debug_print(f"Error cleaning up tracked voice client {guild_id}: {e}")
                
            # Clear tracking data
            self._voice_clients.clear()
            self._current_channel_id = None
            
            # Update UI status again
            if self.status_callback:
                self.status_callback("Disconnected")
                
            return True
        except Exception as e:
            self.debug_print(f"Error in disconnect_voice: {e}")
            return False
    
    async def reconnect_voice(self):
        """Disconnect and reconnect to voice channels to fix audio issues"""
        self.debug_print("Reconnect requested - disconnecting from all voice channels")
        
        # Store current connected channels before disconnecting
        target_channels = []
        for guild in self.guilds:
            if guild.voice_client and guild.voice_client.is_connected():
                channel = guild.voice_client.channel
                target_user = self._find_target_user_in_guild(guild)
                target_channels.append((guild, channel.id, channel.name, target_user))
                self.debug_print(f"Saving current connection: {channel.name} in {guild.name}")
        
        # Disconnect from all channels
        for guild in self.guilds:
            if guild.voice_client and guild.voice_client.is_connected():
                try:
                    await guild.voice_client.disconnect(force=True)
                    self.debug_print(f"Disconnected from voice in {guild.name}")
                except Exception as e:
                    self.debug_print(f"Error disconnecting from {guild.name}: {e}")
        
        # Delay to let Discord register the disconnect
        await asyncio.sleep(2.0)
        
        # Manually clear any lingering connections at the Discord.py level
        for guild in self.guilds:
            if guild.voice_client:
                try:
                    guild.voice_client.cleanup()
                    self.debug_print(f"Cleaned up voice client for {guild.name}")
                except Exception as e:
                    self.debug_print(f"Error cleaning up voice client: {e}")
        
        # Wait a bit more
        await asyncio.sleep(1.0)
        
        # Try to reconnect to the saved channels first
        if target_channels:
            self.debug_print(f"Attempting to reconnect to {len(target_channels)} previous channel(s)")
            reconnected = False
            
            for guild, channel_id, channel_name, member in target_channels:
                # Try direct connection using our new method
                direct_connected = await self._force_direct_connect(guild.id, channel_id)
                if direct_connected:
                    self.debug_print(f"Successfully reconnected directly to {channel_name}")
                    reconnected = True
                    break
            
            if not reconnected:
                self.debug_print("Direct reconnection failed, trying to find target user again")
                # If direct reconnection to previous channels failed, try to find target user
                await self._find_and_connect_to_target_user()
        else:
            # No previous connections, try to find target user
            self.debug_print("No previous connections, trying to find target user")
            await self._find_and_connect_to_target_user()
        
        self.debug_print("Reconnect process completed")
        
        # Update UI
        if self.ui_callbacks and hasattr(self.ui_callbacks, 'on_reconnect_completed'):
            self.ui_callbacks.on_reconnect_completed()
        
    async def _find_and_connect_to_target_user(self):
        """Find and connect to target user across all guilds"""
        target_user = self.config.get('target_user', '')
        if not target_user:
            self.debug_print("No target user configured")
            return False
        
        self.debug_print(f"Searching for {target_user} across all guilds")
        connected = False
        
        for guild in self.guilds:
            try:
                self.debug_print(f"Searching in guild {guild.name}")
                if await self._try_connect_to_target_user(guild):
                    connected = True
                    self.debug_print(f"Successfully connected in guild {guild.name}")
                    break
            except Exception as e:
                self.debug_print(f"Error connecting in guild {guild.name}: {e}")
            
        return connected
    
    def _find_target_user_in_guild(self, guild):
        """Find the target user in a specific guild"""
        target_user = self.config.get('target_user', '')
        if not target_user:
            return None
        
        for member in guild.members:
            member_full = str(member).lower()
            target_lower = target_user.lower()
            
            if member_full == target_lower or member.name.lower() == target_lower:
                return member
            
        return None

    @tasks.loop(minutes=1)  # Check every minute
    async def scheduled_announcement(self):
        """Handle scheduled announcements"""
        current_time = datetime.datetime.now()
        
        # Get announcement settings from config
        enabled = self.config.get('announcement_enabled', True)
        target_day = self.config.get('announcement_day', 4)  # Default to Friday (4)
        target_hour = self.config.get('announcement_hour', 19)
        target_minute = self.config.get('announcement_minute', 0)
        
        self.debug_print(f"Checking scheduled announcement at {current_time}")
        self.debug_print(f"Current day: {current_time.weekday()}, hour: {current_time.hour}, minute: {current_time.minute}")
        self.debug_print(f"Target day: {target_day}, hour: {target_hour}, minute: {target_minute}")
        
        # Check if announcement is enabled and it's the right time
        if (enabled and 
            current_time.weekday() == target_day and 
            current_time.hour == target_hour and 
            current_time.minute == target_minute):
            
            await self._do_announcement()
            
    async def _do_announcement(self, ctx=None):
        """Internal method to handle the announcement playback"""
        try:
            self.debug_print("Starting announcement playback")
            voice_client = None
            
            # If ctx is None (called from scheduler), find a suitable voice channel
            if ctx is None:
                self.debug_print("Called from scheduler/test - looking for voice channel")
                for guild in self.guilds:
                    voice_client = guild.voice_client
                    if voice_client and voice_client.is_connected():
                        self.debug_print(f"Found connected voice client in guild {guild.name}")
                        break
                    
                    # If not connected, try to connect to a channel where the bot is present
                    for vc in guild.voice_channels:
                        if self.user in vc.members:
                            self.debug_print(f"Found bot in channel {vc.name}, attempting to connect")
                            try:
                                voice_client = await vc.connect()
                                self.debug_print("Successfully connected to voice channel")
                                break
                            except Exception as e:
                                self.debug_print(f"Failed to connect to voice channel: {e}")
                    if voice_client and voice_client.is_connected():
                        break
            else:
                # Normal command invocation
                if not ctx.author.voice or not ctx.author.voice.channel:
                    if ctx:
                        await ctx.send("You need to be in a voice channel to test the announcement!")
                    return
                
                # Connect to the voice channel if not already connected
                if not ctx.voice_client:
                    voice_client = await ctx.author.voice.channel.connect()
                else:
                    voice_client = ctx.voice_client
            
            if voice_client and voice_client.is_connected():
                # Create audio player for announcement
                audio_player = AudioPlayer(voice_client, self.tts_manager, self.debug_print)
                
                # Play announcement
                announcement_text = "Happy Friday everyone! It's time for the weekend!"
                await audio_player.play_text(announcement_text, play_notification=True)
                
                if ctx:
                    await ctx.send("Testing Friday announcement!")
                self.debug_print("Announcement test completed successfully")
            else:
                error_msg = "Bot is not in a voice channel and couldn't connect to one"
                self.debug_print(error_msg)
                if ctx:
                    await ctx.send(error_msg)
                raise Exception(error_msg)
                
        except Exception as e:
            self.debug_print(f"Error in announcement playback: {e}")
            if ctx:
                await ctx.send(f"Error: {str(e)}")
            raise
            
    @commands.command()
    async def testfriday(self, ctx):
        """Test the Friday announcement"""
        await self._do_announcement(ctx)
        
    async def cleanup(self):
        """Clean up resources"""
        try:
            # Cancel connection manager
            if hasattr(self, '_connection_manager_task') and self._connection_manager_task:
                self._connection_manager_task.cancel()
                try:
                    await self._connection_manager_task
                except asyncio.CancelledError:
                    pass
                    
            # Cancel status refresh task
            if hasattr(self, '_status_refresh_task') and self._status_refresh_task:
                self._status_refresh_task.cancel()
                try:
                    await self._status_refresh_task
                except asyncio.CancelledError:
                    pass
                    
            # Clean up voice clients
            for guild_id, voice_client in list(self._voice_clients.items()):
                try:
                    if voice_client and voice_client.is_connected():
                        # Clear voice state handlers first to prevent callbacks during disconnect
                        if hasattr(voice_client, '_voice_state_update_handler'):
                            voice_client._voice_state_update_handler = None
                        await voice_client.disconnect()
                except Exception as e:
                    self.debug_print(f"Error disconnecting voice client {guild_id}: {e}")
                finally:
                    del self._voice_clients[guild_id]
                    
            # Clear connection queue
            while not self._connection_queue.empty():
                try:
                    self._connection_queue.get_nowait()
                except asyncio.QueueEmpty:
                    break
                    
            # Cancel any running tasks
            for task in self.running_tasks:
                try:
                    if not task.done():
                        task.cancel()
                        try:
                            await task
                        except asyncio.CancelledError:
                            pass
                except Exception as e:
                    self.debug_print(f"Error canceling task: {e}")
                    
            # Clean up LED controller
            if self.led_controller:
                try:
                    await self.led_controller.cleanup()
                except Exception as e:
                    self.debug_print(f"Error cleaning up LED controller: {e}")
                    
            # Close Discord client
            try:
                # Safely check if client is closed without accessing attributes of _MissingSentinel
                # Use getattr with default to avoid attribute errors
                is_closed = getattr(self, 'is_closed', None)
                if callable(is_closed) and not is_closed():
                    await self.close()
            except Exception as e:
                self.debug_print(f"Error closing Discord client: {e}")
                    
        except Exception as e:
            self.debug_print(f"Error during cleanup: {e}")
        finally:
            # Clear all references
            self._voice_clients.clear()
            self._current_channel_id = None
            self._connection_tasks.clear()
            self.running_tasks.clear()
            self.scheduled_announcements.clear()
            
            # Clear event loop reference
            if hasattr(self, '_loop'):
                self._loop = None
                
            # Clear connection manager task
            self._connection_manager_task = None

    async def _periodic_status_refresh(self):
        """Periodically refresh UI status to ensure it stays in sync with actual connection state"""
        try:
            # Wait a bit to let the bot initialize
            await asyncio.sleep(5)
            
            # Keep refreshing until the bot is stopped
            while True:
                try:
                    # Skip full status refresh if debug mode is disabled
                    if self.config.get('debug_mode', False):
                        # Full refresh with logging
                        await self._refresh_connection_status()
                    else:
                        # Only update status UI without logging
                        for guild_id, voice_client in list(self._voice_clients.items()):
                            if voice_client and voice_client.is_connected() and voice_client.channel:
                                # Update the UI with minimal logging
                                if self.status_callback:
                                    self.status_callback("Connected to Discord")
                                if self.channel_callback:
                                    channel_name = voice_client.channel.name if hasattr(voice_client.channel, 'name') else f"Channel {voice_client.channel.id}"
                                    self.channel_callback(f"{channel_name}")
                                    
                                # Update latency without logging
                                latency_ms = 0
                                try:
                                    if hasattr(voice_client, 'latency'):
                                        if isinstance(voice_client.latency, float) and not math.isinf(voice_client.latency) and not math.isnan(voice_client.latency):
                                            latency_ms = int(voice_client.latency * 1000)
                                except:
                                    pass
                                    
                                if hasattr(self, 'ui_callbacks') and self.ui_callbacks and hasattr(self.ui_callbacks, 'update_latency'):
                                    self.ui_callbacks.update_latency(latency_ms)
                                break
                    
                    # Wait before next refresh (10 seconds)
                    await asyncio.sleep(10)
                except asyncio.CancelledError:
                    # Task was cancelled, exit the loop
                    break
                except Exception as e:
                    self.debug_print(f"Error in periodic status refresh: {e}")
                    # Wait before retrying
                    await asyncio.sleep(10)
        except asyncio.CancelledError:
            # Task was cancelled during initial sleep
            pass
        except Exception as e:
            self.debug_print(f"Error starting periodic status refresh: {e}")

    async def _check_voice_connections(self):
        """Check all voice connections and their status"""
        try:
            # Force refresh guild members first
            await self._refresh_guild_state()
            
            connected = False
            for guild in self.guilds:
                # Check if the guild has an active voice client
                if guild.voice_client and guild.voice_client.is_connected():
                    self.debug_print(f"Found active voice client in guild {guild.name}, channel {guild.voice_client.channel.name}")
                    connected = True
                    
                    # Update our internal tracking
                    self._voice_clients[guild.id] = guild.voice_client
                    self._current_channel_id = guild.voice_client.channel.id
                    
                    # Update UI status
                    if self.status_callback:
                        self.status_callback("Connected to Discord")
                    if self.channel_callback:
                        channel_name = guild.voice_client.channel.name
                        self.channel_callback(f"{channel_name}")
                
                # Check which members are in voice channels
                for vc in guild.voice_channels:
                    if vc.members:
                        member_names = [f"{m.name}" for m in vc.members]
                        self.debug_print(f"Voice channel '{vc.name}' in guild '{guild.name}' has members: {', '.join(member_names)}")
                        
                        # Check if target user is in this channel
                        target_user = self.config.get('target_user', '').lower()
                        for member in vc.members:
                            if member.name.lower() == target_user or str(member).lower() == target_user:
                                self.debug_print(f"Found target user {member.name} in voice channel {vc.name}")
            
            return connected
        except Exception as e:
            self.debug_print(f"Error checking voice connections: {e}")
            return False

    async def _force_direct_connect(self, guild_id: int, channel_id: int) -> bool:
        """Force a direct connection to a voice channel, bypassing connection queue"""
        try:
            guild = self.get_guild(guild_id)
            if not guild:
                self.debug_print(f"Cannot find guild with ID {guild_id}")
                return False
            
            channel = guild.get_channel(channel_id)
            if not channel:
                self.debug_print(f"Cannot find channel with ID {channel_id} in guild {guild.name}")
                return False
            
            self.debug_print(f"Attempting direct connection to {channel.name} in {guild.name}")
            
            # Disconnect first if already connected
            if guild.voice_client and guild.voice_client.is_connected():
                try:
                    await guild.voice_client.disconnect(force=True)
                    self.debug_print(f"Disconnected from previous channel in {guild.name}")
                except Exception as e:
                    self.debug_print(f"Error disconnecting from previous channel: {e}")
            
            # Try to connect with timeout
            try:
                voice_client = await channel.connect(timeout=15.0, self_deaf=True)
                self.debug_print(f"Successfully connected to {channel.name}")
                
                # Set up audio sink if connection successful
                if voice_client and voice_client.is_connected():
                    voice_client.play(discord.PCMAudio(source=silence_source()))
                    if self.status_callback and hasattr(self.status_callback, 'on_connection_status'):
                        self.status_callback.on_connection_status(True, f"Connected to {channel.name} in {guild.name}")
                    return True
                else:
                    self.debug_print(f"Connection failed - no voice client after connect")
                    return False
                
            except asyncio.TimeoutError:
                self.debug_print(f"Connection timeout when connecting to {channel.name}")
                return False
            except Exception as e:
                self.debug_print(f"Error connecting to {channel.name}: {e}")
                return False
            
        except Exception as e:
            self.debug_print(f"Unexpected error in force_direct_connect: {e}")
            return False

    def get_voice_latency(self):
        """Get voice connection latency in milliseconds"""
        try:
            for guild_id, voice_client in list(self._voice_clients.items()):
                if voice_client and voice_client.is_connected():
                    try:
                        # Try to get latency
                        if hasattr(voice_client, 'latency'):
                            if isinstance(voice_client.latency, float):
                                # Check for infinity or NaN before converting to int
                                if math.isinf(voice_client.latency) or math.isnan(voice_client.latency):
                                    self.debug_print("Voice latency is infinity or NaN, setting to 0")
                                    return 0
                                else:
                                    return int(voice_client.latency * 1000)
                            elif callable(voice_client.latency):
                                latency_value = voice_client.latency()
                                # Check for infinity or NaN before converting to int
                                if isinstance(latency_value, float) and (math.isinf(latency_value) or math.isnan(latency_value)):
                                    self.debug_print("Voice latency is infinity or NaN, setting to 0")
                                    return 0
                                else:
                                    return int(latency_value * 1000)
                    except Exception as e:
                        self.debug_print(f"Error getting voice latency: {e}")
        except Exception as e:
            self.debug_print(f"Error in get_voice_latency: {e}")
        
        # Default value if no latency info available
        return 0

    async def process_command(self, command: str, *args):
        """Process a command from the UI"""
        self.debug_print(f"Processing command: {command} with args: {args}")
        
        if command == 'test_announcement':
            try:
                await self._do_announcement()
                return "Test announcement completed successfully"
            except Exception as e:
                self.debug_print(f"Error processing test announcement: {e}")
                return f"Error: {str(e)}"
                
        elif command == 'chat_message':
            # Process chat message with GPT
            if not args or not args[0]:
                return "Error: Empty message"
                
            message = args[0]
            try:
                # Initialize OpenAI client
                import openai
                
                # Extract model name from config (default to GPT-3.5-turbo if not specified)
                model_name = self.config.get('gpt_model', 'gpt-3.5-turbo')
                # Remove pricing info if present in the model name
                if "(" in model_name:
                    model_name = model_name.split("(")[0].strip()
                
                self.debug_print(f"Using model: {model_name} for chat")
                
                # Set up OpenAI client
                client = openai.OpenAI(api_key=self.config.get('openai_api_key', ''))
                
                # Send request to OpenAI
                response = client.chat.completions.create(
                    model=model_name,
                    messages=[
                        {"role": "system", "content": "You are a helpful assistant."},
                        {"role": "user", "content": message}
                    ],
                    max_tokens=1024
                )
                
                # Extract response text - openai==1.3.5 response format
                self.debug_print(f"Got response from OpenAI, extracting content")
                if response and hasattr(response, 'choices') and response.choices:
                    # Access using proper API for openai 1.3.5+
                    reply = response.choices[0].message.content
                    
                    self.debug_print(f"Extracted reply: {reply[:50]}...")
                    
                    # Update status with reply
                    if self.status_callback:
                        self.status_callback(f"chat_response:{reply}")
                    
                    # Play response through TTS in voice channel
                    try:
                        # Find an active voice client
                        for guild_id, voice_client in list(self._voice_clients.items()):
                            if voice_client and voice_client.is_connected():
                                self.debug_print(f"Playing GPT response through voice in guild {guild_id}")
                                
                                # Create audio player
                                audio_player = AudioPlayer(voice_client, self.tts_manager, self.debug_print)
                                
                                # Play the response as TTS
                                asyncio.create_task(audio_player.play_text(reply))
                                break
                        else:
                            self.debug_print("No active voice client found for TTS playback")
                    except Exception as e:
                        self.debug_print(f"Error playing TTS response: {e}")
                    
                    return reply
                else:
                    error = "Error: No response from GPT"
                    if self.status_callback:
                        self.status_callback(f"chat_response:{error}")
                    return error
                    
            except Exception as e:
                error_msg = f"Error: {str(e)}"
                self.debug_print(f"Error processing chat message: {error_msg}")
                
                # Send error back to UI
                if self.status_callback:
                    self.status_callback(f"chat_response:{error_msg}")
                
                return error_msg
        
        return f"Unknown command: {command}"

    async def _delayed_status_update(self, guild_id, channel_id):
        """Schedule a delayed status update to ensure UI reflects correct state"""
        try:
            # Wait a second to let the connection stabilize
            await asyncio.sleep(1.0)
            
            # Get the guild and channel
            guild = self.get_guild(guild_id)
            if not guild:
                self.debug_print("Guild not found in delayed status update")
                return
                
            channel = guild.get_channel(channel_id)
            if not channel:
                self.debug_print("Channel not found in delayed status update")
                return
                
            # Check if we're still connected
            if guild.voice_client and guild.voice_client.is_connected():
                self.debug_print("Delayed status update: Still connected, updating UI")
                
                # Update UI status
                if self.status_callback:
                    self.status_callback("Connected to Discord")
                if self.channel_callback:
                    channel_name = channel.name if hasattr(channel, 'name') else f"Channel {channel_id}"
                    self.channel_callback(f"{channel_name}")
                    
                # Make sure this connection is in our tracking dictionary
                self._voice_clients[guild_id] = guild.voice_client
                self._current_channel_id = channel_id
            else:
                self.debug_print("Delayed status update: No longer connected")
                
        except Exception as e:
            self.debug_print(f"Error in delayed status update: {e}")

class MySink(voice_recv.AudioSink):
    def __init__(self, tts_manager: TTSManager, led_controller: LEDController, 
                 config: Dict[str, Any], debug_print_func: Callable = print):
        self.tts_manager = tts_manager
        self.led_controller = led_controller
        self.config = config
        self.debug_print = debug_print_func
        self.is_speaking = False
        self._lock = threading.Lock()
        self._last_audio_time = time.time()
        
    def write(self, data, user=None) -> bool:
        """Process incoming audio data"""
        try:
            with self._lock:
                # Process audio data
                if isinstance(data, (bytes, bytearray)) and len(data) > 0:
                    self.is_speaking = True
                    self._last_audio_time = time.time()
                    
                    # Update LED if enabled
                    if self.config.get('led_enabled', False):
                        # Use different colors for target user vs others
                        if user and str(user).lower() == self.config.get('target_user', '').lower():
                            color = self.config.get('led_colors', {}).get('target_voice', 
                                                                        {'red': 255, 'green': 0, 'blue': 0})
                            self.led_controller.set_color(0, 0, 
                                                        color.get('red', 0),
                                                        color.get('green', 0),
                                                        color.get('blue', 0))
                        else:
                            color = self.config.get('led_colors', {}).get('other_voice',
                                                                        {'red': 0, 'green': 0, 'blue': 255})
                            self.led_controller.set_color(0, 0, 
                                                        color.get('red', 0),
                                                        color.get('green', 0),
                                                        color.get('blue', 0))
                    return True
                else:
                    # No audio data, check if we should stop speaking
                    if self.is_speaking and time.time() - self._last_audio_time > 0.1:
                        self.is_speaking = False
                        self.led_controller.turn_off()
                    return True
                
        except Exception as e:
            self.debug_print(f"Error processing audio data: {e}")
            return False

    def cleanup(self):
        """Clean up resources"""
        with self._lock:
            try:
                # Turn off LED
                self.led_controller.turn_off()
                
                # Reset state
                self.is_speaking = False
                self._last_audio_time = time.time()
                
            except Exception as e:
                self.debug_print(f"Error in audio sink cleanup: {e}")
                # Even if there's an error, try to turn off the LED
                try:
                    self.led_controller.turn_off()
                except:
                    pass

    def wants_opus(self) -> bool:
        """Required method: Indicate if we want Opus encoded data"""
        return False  # We want PCM data instead of Opus
        
    def listen(self):
        """Start listening for audio"""
        with self._lock:
            self.debug_print("Audio sink listening enabled")
            
    def stop_listening(self):
        """Stop listening for audio"""
        with self._lock:
            self.debug_print("Audio sink listening disabled")
            self.led_controller.turn_off() 

    async def toggle_mute(self):
        """Toggle mute state"""
        if not self.voice_sink:
            self.debug_print("Cannot toggle mute - not connected to voice")
            return
            
        new_state = not self.voice_sink.is_speaking
        self.voice_sink.is_speaking = new_state
        
        if self.status_callback:
            self.status_callback("Muted" if new_state else "Active") 