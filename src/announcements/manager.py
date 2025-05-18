import logging
from ..audio.playback import AudioPlayer

class AnnouncementManager:
    def __init__(self, bot, tts_manager, config, logger=None):
        self.bot = bot
        self.tts_manager = tts_manager
        self.config = config
        self.logger = logger or logging.getLogger(__name__)
        
    async def play_announcement(self, text: str, source: str = "unknown"):
        """Play an announcement in the voice channel"""
        self.logger.info("Starting announcement playback")
        self.logger.info(f"Called from {source} - looking for voice channel")
        
        try:
            # Find voice client
            voice_client = await self._get_voice_client()
            if not voice_client:
                raise Exception("Bot is not in a voice channel and couldn't connect to one")
                
            # Create audio player
            player = AudioPlayer(voice_client, self.tts_manager, self.logger.info)
            
            # Play the announcement with notification sound
            success = await player.play_text(text, play_notification=True)
            
            if success:
                self.logger.info("Announcement completed successfully")
            else:
                self.logger.error("Failed to play announcement")
                
            return success
            
        except Exception as e:
            self.logger.error(f"Error in announcement playback: {e}")
            raise 