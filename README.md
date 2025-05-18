# Discord Voice Assistant Bot

A Discord bot that can listen to voice channels, control LED indicators, and make scheduled announcements. Perfect for creating an interactive voice presence in your Discord server.

## Features

- Voice activity detection with LED indicators
- Text-to-speech capabilities using OpenAI's API
- Scheduled announcements (e.g., Friday evening reminders)
- Configurable target user tracking
- LED status indicators for different users and states

## Requirements

- Python 3.8 or higher
- Discord Bot Token
- OpenAI API Key
- Raspberry Pi (optional, for LED control)

## Installation

1. Clone this repository:
```bash
git clone <repository-url>
cd discord-voice-assistant
```

2. Install required packages:
```bash
pip install -r requirements.txt
```

3. Create and configure `config.json`:
```json
{
    "discord_token": "YOUR_DISCORD_BOT_TOKEN",
    "openai_api_key": "YOUR_OPENAI_API_KEY",
    "target_user": "username#1234",
    "announcement_enabled": true,
    "announcement_day": 4,
    "announcement_hour": 19,
    "announcement_minute": 0,
    "listen_all_users": false
}
```

## Usage

1. Start the bot:
```bash
python src/main.py
```

2. The bot will automatically:
   - Connect to your Discord server
   - Join the voice channel where the target user is present
   - Monitor voice activity and control LEDs accordingly
   - Make scheduled announcements if enabled

## Commands

- `!testfriday` - Test the Friday announcement feature

## LED Control

If running on a Raspberry Pi, the bot will control LEDs based on voice activity:
- Red LED: Target user is speaking
- Blue LED: Other users are speaking

## Configuration Options

- `discord_token`: Your Discord bot token
- `openai_api_key`: Your OpenAI API key
- `target_user`: Discord username to track (format: "username#1234")
- `announcement_enabled`: Enable/disable scheduled announcements
- `announcement_day`: Day of the week for announcement (0=Monday, 6=Sunday)
- `announcement_hour`: Hour for announcement (24-hour format)
- `announcement_minute`: Minute for announcement
- `listen_all_users`: Whether to monitor all users or just the target user

## Troubleshooting

1. If the bot fails to connect to voice channels:
   - Ensure the bot has proper permissions in your Discord server
   - Check if the target user is in a voice channel
   - Verify your Discord token is correct

2. If LEDs don't work:
   - Ensure you're running on a Raspberry Pi
   - Check GPIO pin connections
   - Verify you have the required permissions to access GPIO

## Contributing

Feel free to submit issues and pull requests for new features or bug fixes.

## License

This project is licensed under the MIT License - see the LICENSE file for details.
