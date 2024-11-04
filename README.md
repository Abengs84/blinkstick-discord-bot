# Discord Voice Activity LED Controller

A Python-based Discord bot that controls LED indicators (via BlinkStick) based on voice activity in Discord channels. The bot specifically monitors voice activity and provides visual feedback through LED indicators, with a system tray interface.

## Features

- **Real-time Voice Activity Monitoring**: Detects when specific users are speaking in Discord voice channels
- **LED Integration**: Controls BlinkStick LED devices to provide visual feedback
  - Red LED indicates when the primary user (***REMOVED***) is speaking
  - Blue LED indicates when other users are speaking
  - Custom LED patterns for various events (startup, notifications, etc.)
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

## Requirements

- Python 3.9+
- BlinkStick LED device
- Discord Bot Token
- Required Python packages (see requirements.txt)

## Setup

1. Install required packages:
```bash
pip install -r requirements.txt
```

2. Create a `config.json` file with your Discord bot token:
```json
{
"token": "YOUR_DISCORD_BOT_TOKEN"
}
```
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

## Contributing

Contributions are welcome! Please feel free to submit a Pull Request.

## Acknowledgments

- Discord.py developers
- BlinkStick team
- [Any other acknowledgments]
