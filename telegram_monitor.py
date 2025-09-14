import asyncio
import logging
import os
from telethon import TelegramClient, events
from telethon.errors import SessionPasswordNeededError
from telegram import Bot
from telegram.error import TelegramError
from datetime import datetime

# Configure logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO,
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler('/app/logs/telegram_monitor.log') if os.path.exists('/app/logs') else logging.NullHandler()
    ]
)
logger = logging.getLogger(__name__)

class PublicChannelMonitor:
    def __init__(self):
        # Load configuration from environment variables
        self.api_id = int(os.getenv('API_ID', '0'))
        self.api_hash = os.getenv('API_HASH', '')
        self.phone_number = os.getenv('PHONE_NUMBER', '')
        self.bot_token = os.getenv('BOT_TOKEN', '')
        self.user_id = int(os.getenv('USER_ID', '0'))
        self.channel_username = os.getenv('CHANNEL_USERNAME', '')
        self.notification_interval = int(os.getenv('NOTIFICATION_INTERVAL', '30'))
        
        # Validate required environment variables
        self._validate_config()
        
        self.client = None
        self.bot = None
        self.notification_active = False
        self.stop_requested = False
        
        # Use sessions directory for persistence
        self.session_path = '/app/sessions/telegram_session'
        
    def _validate_config(self):
        """Validate that all required environment variables are set"""
        required_vars = {
            'API_ID': self.api_id,
            'API_HASH': self.api_hash,
            'PHONE_NUMBER': self.phone_number,
            'BOT_TOKEN': self.bot_token,
            'USER_ID': self.user_id,
            'CHANNEL_USERNAME': self.channel_username
        }
        
        missing_vars = [var for var, value in required_vars.items() 
                       if not value or (isinstance(value, int) and value == 0)]
        
        if missing_vars:
            logger.error(f"Missing required environment variables: {', '.join(missing_vars)}")
            raise ValueError(f"Missing required environment variables: {', '.join(missing_vars)}")
            
        logger.info("Configuration validated successfully")
        
    async def setup_client(self):
        """Setup Telegram client for monitoring"""
        logger.info("Setting up Telegram client...")
        
        # Ensure sessions directory exists
        os.makedirs('/app/sessions', exist_ok=True)
        
        self.client = TelegramClient(self.session_path, self.api_id, self.api_hash)
        
        # Check if we're running in Docker and handle authentication
        if not os.path.exists(f"{self.session_path}.session"):
            logger.warning("No existing session found. First-time setup required.")
            logger.warning("You may need to run this interactively first to authenticate.")
            
        await self.client.start(phone=self.phone_number)
        
        if not await self.client.is_user_authorized():
            logger.error("User not authorized. Session may be invalid or missing.")
            logger.error("Please run the script interactively first to authenticate.")
            raise Exception("Authentication required")
                
        # Setup notification bot
        self.bot = Bot(token=self.bot_token)
        
        logger.info("Client and bot setup complete")
        
    async def send_notification(self, message_text):
        """Send notification via bot"""
        try:
            await self.bot.send_message(
                chat_id=self.user_id,
                text=message_text,
                parse_mode='HTML'
            )
        except TelegramError as e:
            logger.error(f"Error sending notification: {e}")
            
    async def start_notification_loop(self, new_message):
        """Start the notification loop for a new message"""
        if self.notification_active:
            return  # Already notifying
            
        self.notification_active = True
        self.stop_requested = False
        
        # Get message details
        sender_name = getattr(new_message.sender, 'first_name', 'Unknown')
        if hasattr(new_message.sender, 'username') and new_message.sender.username:
            sender_name = f"@{new_message.sender.username}"
        
        message_preview = new_message.text[:150] if new_message.text else "Media message"
        timestamp = new_message.date.strftime("%H:%M:%S %d/%m/%Y")
        
        notification_count = 0
        max_notifications = 25  # Safety limit
        
        # Send initial detailed notification
        initial_text = (
            f"🔔 <b>NEW MESSAGE DETECTED!</b>\n\n"
            f"📢 Channel: <code>{self.channel_username}</code>\n"
            f"👤 Sender: {sender_name}\n"
            f"⏰ Time: {timestamp}\n"
            f"💬 Preview: {message_preview}\n\n"
            f"🛑 Send /stop to your bot to stop notifications"
        )
        await self.send_notification(initial_text)
        
        # Wait a bit before starting the loop
        await asyncio.sleep(5)
        
        while self.notification_active and not self.stop_requested and notification_count < max_notifications:
            notification_count += 1
            
            alert_text = (
                f"⚠️ <b>ALERT #{notification_count}</b> ⚠️\n\n"
                f"You have an unread message from:\n"
                f"📢 <code>{self.channel_username}</code>\n"
                f"⏰ {timestamp}\n\n"
                f"💬 <i>{message_preview[:100]}...</i>\n\n"
                f"🛑 Send /stop to your bot to stop these alerts"
            )
            
            await self.send_notification(alert_text)
            logger.info(f"Sent alert notification #{notification_count}")
            
            await asyncio.sleep(self.notification_interval)
            
        if notification_count >= max_notifications:
            await self.send_notification("⚠️ Maximum notifications reached. Alerts stopped automatically.")
            
        self.notification_active = False
        logger.info("Notification loop ended")
        
    async def check_stop_command(self):
        """Check for stop command from bot"""
        try:
            updates = await self.bot.get_updates(limit=10)
            for update in updates:
                if (update.message and 
                    update.message.from_user.id == self.user_id and 
                    update.message.text and 
                    update.message.text.strip().lower() == '/stop'):
                    
                    self.stop_requested = True
                    self.notification_active = False
                    
                    await self.bot.send_message(
                        chat_id=self.user_id,
                        text="✅ Notifications stopped!"
                    )
                    
                    # Mark update as processed
                    await self.bot.get_updates(offset=update.update_id + 1)
                    logger.info("Stop command received")
                    return True
        except Exception as e:
            logger.error(f"Error checking for stop command: {e}")
        return False
        
    async def start_monitoring(self):
        """Start monitoring the channel"""
        try:
            await self.setup_client()
        except Exception as e:
            logger.error(f"Failed to setup client: {e}")
            logger.error("Make sure you have run the initial authentication setup.")
            return
        
        # Send startup message
        await self.send_notification(
            f"🤖 <b>Channel Monitor Started!</b>\n\n"
            f"📢 Monitoring: <code>{self.channel_username}</code>\n"
            f"🔍 Waiting for new messages...\n"
            f"⏱️ Notification interval: {self.notification_interval}s\n\n"
            f"ℹ️ Send /stop to stop notifications when they start"
        )
        
        # Set up event handler for new messages
        @self.client.on(events.NewMessage(chats=self.channel_username))
        async def handler(event):
            logger.info(f"New message detected in {self.channel_username}")
            
            # Start notification loop in background
            asyncio.create_task(self.start_notification_loop(event.message))
            
        logger.info(f"Started monitoring {self.channel_username}")
        
        # Keep checking for stop commands
        while True:
            await self.check_stop_command()
            await asyncio.sleep(2)  # Check every 2 seconds
            
    async def stop_monitoring(self):
        """Stop monitoring"""
        self.notification_active = False
        self.stop_requested = True
        if self.client:
            await self.client.disconnect()

async def main():
    """Main function"""
    logger.info("Starting Telegram Channel Monitor")
    
    monitor = PublicChannelMonitor()
    
    try:
        await monitor.start_monitoring()
    except KeyboardInterrupt:
        logger.info("Monitoring stopped by user")
        await monitor.stop_monitoring()
    except Exception as e:
        logger.error(f"Monitor error: {e}")
        await monitor.stop_monitoring()
        raise

if __name__ == "__main__":
    asyncio.run(main())
