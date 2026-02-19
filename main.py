import asyncio
import logging
import os
import re
import glob
import sys
from dotenv import load_dotenv
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import CommandStart
from aiogram.types import FSInputFile
from aiohttp import web, ClientSession
import instaloader
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

# Initialize Instaloader
L = instaloader.Instaloader(
    dirname_pattern=DOWNLOAD_DIR,
    filename_pattern="{shortcode}",
    download_video_thumbnails=False,
    download_geotags=False,
    download_comments=False,
    save_metadata=False,
    compress_json=False,
    post_metadata_txt_pattern="",
)

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
# Instagram Download Functions
# ========================

def extract_shortcode(url: str) -> str:
    """Extract shortcode from Instagram URL."""
    patterns = [
        r'instagram\.com/p/([A-Za-z0-9_-]+)',
        r'instagram\.com/reel/([A-Za-z0-9_-]+)',
        r'instagram\.com/reels/([A-Za-z0-9_-]+)',
        r'instagram\.com/tv/([A-Za-z0-9_-]+)',
        r'instagram\.com/stories/[^/]+/(\d+)',
    ]
    for pattern in patterns:
        match = re.search(pattern, url)
        if match:
            return match.group(1)
    return None


def download_with_instaloader(url: str) -> str:
    """Download Instagram content using instaloader."""
    try:
        shortcode = extract_shortcode(url)
        if not shortcode:
            logging.error(f"Could not extract shortcode from URL: {url}")
            return None
        
        # Check if it's a story link
        if '/stories/' in url:
            logging.info("Story links require login, skipping instaloader")
            return None
        
        post = instaloader.Post.from_shortcode(L.context, shortcode)
        L.download_post(post, target="")
        
        # Find the downloaded file
        for ext in ['mp4', 'jpg', 'jpeg', 'png', 'webp']:
            pattern = os.path.join(DOWNLOAD_DIR, f"{shortcode}*.{ext}")
            files = glob.glob(pattern)
            if files:
                return files[0]
        
        # Check for any file with the shortcode
        pattern = os.path.join(DOWNLOAD_DIR, f"{shortcode}*")
        files = glob.glob(pattern)
        if files:
            # Filter out txt/json files
            media_files = [f for f in files if not f.endswith(('.txt', '.json', '.xz'))]
            if media_files:
                return media_files[0]
        
        logging.error(f"File not found after download for shortcode: {shortcode}")
        return None
    except instaloader.exceptions.LoginRequiredException:
        logging.warning("Instaloader: Login required, trying yt-dlp fallback")
        return None
    except instaloader.exceptions.QueryReturnedNotFoundException:
        logging.error(f"Instaloader: Post not found: {url}")
        return None
    except instaloader.exceptions.PrivateProfileNotFollowedException:
        logging.error(f"Instaloader: Private profile: {url}")
        return None
    except Exception as e:
        logging.error(f"Instaloader error: {type(e).__name__}: {e}")
        return None


def download_with_ytdlp(url: str) -> str:
    """Download Instagram content using yt-dlp as fallback."""
    outtmpl = os.path.join(DOWNLOAD_DIR, '%(id)s.%(ext)s')
    
    ydl_opts = {
        'outtmpl': outtmpl,
        'quiet': True,
        'no_warnings': True,
        'format': 'best',
        'socket_timeout': 30,
        'retries': 3,
        'http_headers': {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        },
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            filename = ydl.prepare_filename(info)
            return filename
    except Exception as e:
        logging.error(f"yt-dlp error: {type(e).__name__}: {e}")
        return None


def download_instagram_content(url: str) -> str:
    """
    Downloads Instagram content.
    Tries instaloader first, falls back to yt-dlp.
    Returns the path to the downloaded file.
    """
    logging.info(f"Attempting download: {url}")
    
    # Try instaloader first
    filename = download_with_instaloader(url)
    if filename and os.path.exists(filename):
        logging.info(f"Downloaded with instaloader: {filename}")
        return filename
    
    # Fallback to yt-dlp
    logging.info("Trying yt-dlp fallback...")
    filename = download_with_ytdlp(url)
    if filename and os.path.exists(filename):
        logging.info(f"Downloaded with yt-dlp: {filename}")
        return filename
    
    logging.error(f"All download methods failed for: {url}")
    return None


# ========================
# Bot Handlers
# ========================

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
