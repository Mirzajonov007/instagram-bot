import asyncio
import logging
import os
import sys
from dotenv import load_dotenv
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import CommandStart
from aiogram.types import FSInputFile
from aiohttp import web, ClientSession
import yt_dlp

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(level=logging.INFO)

# Get Bot Token
BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    logging.error("BOT_TOKEN is not set in .env file")
    print("FATAL: BOT_TOKEN is missing. Please check .env file.")
    

# Initialize Bot and Dispatcher
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# Output directory for downloads
DOWNLOAD_DIR = "downloads"
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

# ========================
# Keep-Alive Web Server
# ========================

async def health_handler(request):
    return web.Response(text="OK")

async def start_web_server():
    """Start a simple web server for Render health checks."""
    app = web.Application()
    app.router.add_get("/", health_handler)
    app.router.add_get("/health", health_handler)
    
    port = int(os.getenv("PORT", 10000))
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    logging.info(f"Web server started on port {port}")

async def keep_alive():
    """Ping own URL every 5 minutes to prevent Render from sleeping."""
    url = os.getenv("RENDER_EXTERNAL_URL")
    if not url:
        logging.warning("RENDER_EXTERNAL_URL not set, keep-alive disabled")
        return
    
    await asyncio.sleep(60)  # Wait 1 minute before first ping
    async with ClientSession() as session:
        while True:
            try:
                async with session.get(f"{url}/health") as resp:
                    logging.info(f"Keep-alive ping: {resp.status}")
            except Exception as e:
                logging.warning(f"Keep-alive ping failed: {e}")
            await asyncio.sleep(300)  # Every 5 minutes

# ========================
# Bot Handlers
# ========================

def download_instagram_content(url: str):
    """
    Downloads Instagram content using yt-dlp.
    Returns the path to the downloaded file.
    """
    outtmpl = os.path.join(DOWNLOAD_DIR, '%(id)s.%(ext)s')
    
    ydl_opts = {
        'outtmpl': outtmpl,
        'quiet': True,
        'no_warnings': True,
        'format': 'best',
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            filename = ydl.prepare_filename(info)
            return filename
    except Exception as e:
        logging.error(f"Error downloading content: {e}")
        return None

@dp.message(CommandStart())
async def cmd_start(message: types.Message):
    await message.answer("Salom! Menga Instagram reels, post yoki storiy linkini yuboring, men uni yuklab beraman. 🚀")

@dp.message(F.text.contains("instagram.com"))
async def handle_instagram_link(message: types.Message):
    url = message.text.strip()
    status_msg = await message.reply("Yuklanmoqda... ⏳")

    loop = asyncio.get_event_loop()
    filename = await loop.run_in_executor(None, download_instagram_content, url)

    if filename and os.path.exists(filename):
        try:
            media_file = FSInputFile(filename)
            caption = "Mana faylingiz! 📥"
            
            if filename.lower().endswith(('.mp4', '.mkv', '.mov')):
                await message.answer_video(video=media_file, caption=caption)
            elif filename.lower().endswith(('.jpg', '.jpeg', '.png', '.webp')):
                await message.answer_photo(photo=media_file, caption=caption)
            else:
                 await message.answer_document(document=media_file, caption=caption)
            
            await status_msg.delete()
        except Exception as e:
            logging.error(f"Error sending file: {e}")
            await status_msg.edit_text("Faylni yuborishda xatolik bo'ldi. 😕")
        finally:
            try:
                if os.path.exists(filename):
                    os.remove(filename)
            except Exception as e:
                logging.error(f"Error deleting file: {e}")
    else:
        await status_msg.edit_text("Kechirasiz, bu linkdan yuklab bo'lmadi.\nLink to'g'riligini yoki profil ochiqligini tekshiring. 🔒")

# ========================
# Main
# ========================

async def main():
    if not BOT_TOKEN:
        print("Error: BOT_TOKEN is missing in .env file.")
        return
    
    # Start web server and keep-alive in background
    await start_web_server()
    asyncio.create_task(keep_alive())
    
    # Start bot polling
    await dp.start_polling(bot)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Bot to'xtatildi")
