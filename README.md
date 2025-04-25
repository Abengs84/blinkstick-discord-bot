# Discord Voice Activity LED Controller

A Python-based Discord bot that controls LED indicators (via BlinkStick) based on voice activity in Discord channels. The bot provides visual feedback through LED indicators, integrates with ChatGPT for voice interactions, and includes a system tray interface.

## Features

- **Real-time Voice Activity Monitoring**: Detects when specific users are speaking in Discord voice channels
- **LED Integration**: Controls BlinkStick LED devices to provide visual feedback
  - Red LED indicates when the primary user is speaking
  - Blue LED indicates when other users are speaking
  - Purple LED indicates ChatGPT activity
  - Yellow LED for notifications
  - Green LED for power-on sequence
  - Custom LED patterns for various events
- **ChatGPT Integration**:
  - Voice-to-text and text-to-speech interactions
  - Configurable GPT model (default: gpt-3.5-turbo)
  - Conversation history management
  - Visual feedback during ChatGPT processing
- **System Tray Integration**: 
  - Runs quietly in the system tray
  - Easy access to status information and controls
  - Clean shutdown functionality
- **Status Window**:
  - Shows current connection status
  - Displays active server and channel information
  - Toggle for Debug Mode
- **Hotkey Support**: Global hotkey combination (Ctrl+Shift+Alt+Ö) for toggling between PTT and Voice Activity
- **Auto-startup**: Automatically starts with Windows
- **Error Recovery**: Automatically handles disconnections and hardware issues

## Technical Details

- Built with Discord.py and voice_recv extension for voice activity detection
- Uses BlinkStick Python API for LED control
- Implements multi-threading for concurrent operations
- Tkinter-based status window interface
- Pystray implementation for system tray functionality
- Proper resource management and cleanup
- OpenAI API integration for ChatGPT functionality

## Requirements

- Python 3.9+
- BlinkStick LED device
- Discord Bot Token
- OpenAI API Key
- Required Python packages (see requirements.txt)

## Setup

1. Install required packages:
```bash
pip install -r requirements.txt
```

2. Create a `config.json` file with your configuration:
```json
{
    "token": "YOUR_DISCORD_BOT_TOKEN",
    "target_user": "username",
    "debug_mode": true,
    "led_enabled": false,
    "hotkey": "ctrl+shift+alt+ö",
    "openai_api_key": "YOUR_OPENAI_API_KEY",
    "gpt_model": "gpt-3.5-turbo",
    "led_colors": {
        "target_voice": {"red": 255, "green": 0, "blue": 0},
        "other_voice": {"red": 0, "green": 0, "blue": 255},
        "hotkey": {"red": 60, "green": 0, "blue": 0},
        "notification": {"red": 255, "green": 204, "blue": 0},
        "gpt_activity": {"red": 128, "green": 0, "blue": 128},
        "power_on": {"red": 0, "green": 100, "blue": 0}
    }
}
```

Configuration options:
- `token`: Your Discord bot token
- `target_user`: The Discord username to track for voice activity
- `debug_mode`: Enable/disable debug messages
- `led_enabled`: Initial LED state
- `hotkey`: Global hotkey combination for PTT toggle (default: "ctrl+shift+alt+ö")
- `openai_api_key`: Your OpenAI API key for ChatGPT integration
- `gpt_model`: GPT model to use (default: "gpt-3.5-turbo")
- `led_colors`: RGB values for different LED states

3. Connect your BlinkStick device
   
4. Run the application:

```bash
python bot5.py
```

## Building

Use PyInstaller to create a standalone executable:

```bash
pyinstaller bot5.spec
```

## Usage

- The bot automatically joins voice channels when the specified user joins
- LED indicators respond to voice activity in real-time
- Access status and controls via the system tray icon
- Use the global hotkey combination for toggling between PTT and Voice Activity
- Clean shutdown available through system tray menu
- Interact with ChatGPT through voice commands
- LED colors can be customized through config.json

## Version Compatibility

- discord.py 2.5.0 and above is not compatible
- Current working version: discord.py 2.4.2

## Contributing

Contributions are welcome! Please feel free to submit a Pull Request.

## Acknowledgments

- Discord.py developers
- BlinkStick team
- OpenAI for ChatGPT API
- [icon-icons.com](https://icon-icons.com/) for the LED icon
