# GSwarm Multi-User Telegram Bot Service

A Telegram bot wrapper for the [GSwarm](https://github.com/Deep-Commit/gswarm) monitoring service that manages multiple users through a queue-based system. Each user can link their EVM address with Discord for monitoring and the Swarm discord role, with only one active session running at a time.

## 🎯 Features

- **Single Active Session**: Only one user's GSwarm instance runs at a time, preventing resource conflicts
- **Automatic Queue Management**: Users are automatically queued when another session is active
- **Auto-Verification**: Automatically detects and sends verification codes from GSwarm output
- **Auto-Account Linking**: Detects successful Discord account linking and gracefully closes sessions
- **Session Timeout**: Automatically stops inactive sessions after 10 minutes
- **Docker-Ready**: Fully containerized for easy deployment on Render, Railway, or any Docker-compatible platform

## 🏗️ Architecture

The bot operates as a supervisor that:

1. Accepts EVM addresses from users via Telegram
2. Manages a queue system for multiple concurrent requests
3. Spawns GSwarm subprocesses for the active user
4. Monitors GSwarm output for verification codes and success messages
5. Automatically handles the verification flow
6. Manages session lifecycle (start, timeout, stop)

### Flow Diagram

```
User sends EVM address
        ↓
   Is session active?
    ↙        ↘
  Yes         No
   ↓           ↓
  Queue    Start GSwarm
   ↓           ↓
  Wait    Monitor Output
   ↓           ↓
Notify     Auto-Verify
When Ready     ↓
           Link Success
                ↓
          Close Session
                ↓
         Start Next User
```

## 📋 Prerequisites

- **Telegram Bot Token**: Get one from [@BotFather](https://t.me/BotFather) on Telegram
- **Docker**: For containerized deployment (optional for local development)
- **Python 3.11+**: If running locally without Docker
- **Go**: Already handled in Dockerfile for building GSwarm

## 🚀 Deployment

### Deploy on Render

1. **Push to GitHub**: Upload your repository to GitHub

2. **Create Web Service**:
   - Go to [Render Dashboard](https://dashboard.render.com)
   - Click "New +" → "Web Service"
   - Connect your GitHub repository

3. **Configure Environment**:
   - Add environment variable:
     ```
     TELEGRAM_BOT_TOKEN=your_bot_token_here
     ```
   - Render will automatically detect the Dockerfile and build using it

4. **Deploy**: Render will build and deploy automatically


### Local Docker Deployment

```bash
# Build the Docker image
docker build -t gswarm-bot .

# Run the container
docker run -e TELEGRAM_BOT_TOKEN=your_bot_token_here gswarm-bot
```

### Local Python Development

```bash
# Install dependencies
pip install -r requirements.txt

# Install Go and build GSwarm (or use pre-built binary)
# Then set environment variable and run:
export TELEGRAM_BOT_TOKEN=your_bot_token_here
python main.py
```

## 💬 Usage

### Starting a Session

1. Start a chat with your bot on Telegram
2. Send `/start` to begin
3. Send your EVM address (must start with `0x` and be 42 characters long)
   ```
   0x1234567890123456789012345678901234567890
   ```
4. Wait for verification code (automatically handled)
5. Receive confirmation when account is successfully linked

### Bot Commands

- `/start` - Display welcome message and instructions
- `/stop` - Stop your active session or remove yourself from the queue

### Session Management

- **Active User**: Any message you send refreshes your session timeout
- **Queued Users**: You'll receive a notification when it's your turn
- **Timeout**: Sessions automatically stop after 10 minutes of inactivity
- **Auto-Close**: Sessions close automatically when Discord account linking succeeds

## ⚙️ Configuration

### Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `TELEGRAM_BOT_TOKEN` | Yes | Your Telegram bot token from @BotFather |

### Configuration Constants (in `main.py`)

```python
USER_CONFIG_PATH = "/app/telegram-config.json"  # Path for GSwarm config
GSWARM_CMD = "gswarm"                            # GSwarm binary command
SESSION_TIMEOUT = timedelta(minutes=10)          # Inactivity timeout
```

## 🔧 Technical Details

### Project Structure

```
gswarm-multibot/
├── main.py                 # Main bot supervisor logic
├── Dockerfile              # Container build instructions
├── gswarm_builder.sh       # Script to build GSwarm from source
├── requirements.txt        # Python dependencies
└── README.md              # This file
```

### Dependencies

- **aiogram 3.4.1**: Telegram Bot API framework
- **gswarm**: Go-based monitoring service (built from source)

### How It Works

1. **Queue System**: In-memory queue stores `(chat_id, evm_address)` tuples
2. **Active Session**: Global state tracks the current active session
3. **Process Management**: Uses `asyncio.create_subprocess_exec()` for non-blocking subprocess handling
4. **Output Monitoring**: Regex patterns detect verification codes and success messages
5. **Auto-Verification**: Bot automatically sends `/verify <code>` when detected

## 🐛 Troubleshooting

### GSwarm Binary Not Found

If you see `GSwarm binary not found in PATH`, ensure:
- Dockerfile correctly copies the binary to `/usr/local/bin/gswarm`
- The build completed successfully without errors

### Session Not Starting

- Verify your EVM address format (must be 42 characters, starting with `0x`)
- Check that the bot token is correctly set in environment variables
- Review Docker logs for error messages

### Verification Not Working

- Ensure GSwarm is producing verification codes in its output
- Check that the regex pattern matches your GSwarm version's output format
- Monitor Docker logs: `docker logs <container_id>`

### Timeout Issues

- Default timeout is 10 minutes of inactivity
- Send any message to your bot to refresh the timeout
- Adjust `SESSION_TIMEOUT` in `main.py` if needed

## 📝 Notes

- **Single Active Session**: This design prevents resource conflicts by ensuring only one GSwarm process runs at a time
- **In-Memory State**: Queue and session state are not persisted; restarting the bot resets the queue
- **Error Handling**: The bot continues running even if individual message sends fail
- **Auto-Cleanup**: Sessions are automatically cleaned up when processes exit or timeout

## 🔐 Security Considerations

- **Never commit your bot token** to version control
- Use environment variables for sensitive configuration
- The Dockerfile includes a token in ENV (should be removed in production - use secrets instead)

## 📄 License

This project wraps the GSwarm service. Please refer to:
- GSwarm: [github.com/Deep-Commit/gswarm](https://github.com/Deep-Commit/gswarm)

## 🤝 Contributing

Feel free to submit issues and enhancement requests!

## 🙏 Acknowledgments

- [GSwarm](https://github.com/Deep-Commit/gswarm) - The underlying monitoring service
- [aiogram](https://github.com/aiogram/aiogram) - Telegram Bot framework
