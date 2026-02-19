import asyncio
import logging
import os
import re
import glob
import hashlib
from dotenv import load_dotenv
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import CommandStart
from aiogram.types import FSInputFile, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
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

# Store for music search results (video_id -> url mapping)
music_cache = {}

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
    
    await asyncio.sleep(60)
    async with ClientSession() as session:
        while True:
            try:
                async with session.get(f"{url}/health") as resp:
                    logging.info(f"Keep-alive ping: {resp.status}")
            except Exception as e:
                logging.warning(f"Keep-alive ping failed: {e}")
            await asyncio.sleep(300)

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
        
        if '/stories/' in url:
            logging.info("Story links require login, skipping instaloader")
            return None
        
        post = instaloader.Post.from_shortcode(L.context, shortcode)
        L.download_post(post, target="")
        
        for ext in ['mp4', 'jpg', 'jpeg', 'png', 'webp']:
            pattern = os.path.join(DOWNLOAD_DIR, f"{shortcode}*.{ext}")
            files = glob.glob(pattern)
            if files:
                return files[0]
        
        pattern = os.path.join(DOWNLOAD_DIR, f"{shortcode}*")
        files = glob.glob(pattern)
        if files:
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
    """Downloads Instagram content. Tries instaloader first, falls back to yt-dlp."""
    logging.info(f"Attempting download: {url}")
    
    filename = download_with_instaloader(url)
    if filename and os.path.exists(filename):
        logging.info(f"Downloaded with instaloader: {filename}")
        return filename
    
    logging.info("Trying yt-dlp fallback...")
    filename = download_with_ytdlp(url)
    if filename and os.path.exists(filename):
        logging.info(f"Downloaded with yt-dlp: {filename}")
        return filename
    
    logging.error(f"All download methods failed for: {url}")
    return None


def extract_reels_audio(video_path: str) -> str:
    """
    Extracts audio from a video file and saves it as MP3 using yt-dlp or ffmpeg.
    Returns the path to the MP3 file.
    """
    if not video_path or not os.path.exists(video_path):
        logging.error(f"Cannot extract audio: file not found at {video_path}")
        return None
    
    # Generate MP3 path
    base_path = os.path.splitext(video_path)[0]
    mp3_path = f"{base_path}_audio.mp3"
    
    # We'll try to use yt-dlp to extract audio from the local file
    ydl_opts = {
        'quiet': True,
        'no_warnings': True,
        'format': 'bestaudio/best',
        'postprocessors': [{
            'key': 'FFmpegExtractAudio',
            'preferredcodec': 'mp3',
            'preferredquality': '128',
        }],
        'outtmpl': base_path + '_audio.%(ext)s',
    }

    try:
        logging.info(f"Extracting audio from {video_path}...")
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            # yt-dlp can process local files if prefixed with file://
            # But on windows it's tricky, so we just pass the path
            ydl.extract_info(video_path, download=True)
            
            if os.path.exists(mp3_path):
                logging.info(f"Successfully extracted audio: {mp3_path}")
                return mp3_path
    except Exception as e:
        logging.error(f"Error extracting audio from video: {e}")
        if "ffmpeg" in str(e).lower():
            logging.error("FFmpeg not found! Audio extraction failed.")
            
    return None


# ========================
# Music Search & Download Functions
# ========================

def search_music(query: str) -> list:
    """
    Search for music on YouTube using yt-dlp.
    Returns list of dicts with: title, url, duration, video_id, artist
    """
    ydl_opts = {
        'quiet': True,
        'no_warnings': True,
        'extract_flat': True,
        'default_search': 'ytsearch10',
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            result = ydl.extract_info(f"ytsearch10:{query}", download=False)
            
            if not result or 'entries' not in result:
                return []
            
            songs = []
            for entry in result['entries']:
                if not entry:
                    continue
                
                video_id = entry.get('id', '')
                title = entry.get('title', 'Noma\'lum')
                url = entry.get('url', f"https://www.youtube.com/watch?v={video_id}")
                duration = entry.get('duration') or 0
                artist = entry.get('uploader', '') or entry.get('channel', '')
                
                # Format duration
                if duration:
                    mins = int(duration) // 60
                    secs = int(duration) % 60
                    dur_str = f"{mins}:{secs:02d}"
                else:
                    dur_str = "—"
                
                # Create short key for callback data (max 64 bytes)
                short_key = hashlib.md5(video_id.encode()).hexdigest()[:10]
                
                # Cache the URL
                music_cache[short_key] = {
                    'url': f"https://www.youtube.com/watch?v={video_id}",
                    'title': title,
                    'artist': artist,
                }
                
                songs.append({
                    'title': title,
                    'url': url,
                    'duration': dur_str,
                    'video_id': video_id,
                    'artist': artist,
                    'key': short_key,
                })
            
            return songs
    except Exception as e:
        logging.error(f"Music search error: {type(e).__name__}: {e}")
        return []


def download_music(url: str) -> tuple:
    """
    Download music from YouTube as MP3.
    Returns (filepath, title, artist) or (None, None, None)
    """
    outtmpl = os.path.join(DOWNLOAD_DIR, '%(id)s.%(ext)s')
    
    ydl_opts = {
        'outtmpl': outtmpl,
        'quiet': True,
        'no_warnings': True,
        'format': 'bestaudio/best',
        'postprocessors': [{
            'key': 'FFmpegExtractAudio',
            'preferredcodec': 'mp3',
            'preferredquality': '128',
        }],
        'socket_timeout': 30,
        'retries': 3,
    }

    try:
        logging.info(f"Downloading music from {url}...")
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            video_id = info.get('id', 'unknown')
            mp3_path = os.path.join(DOWNLOAD_DIR, f"{video_id}.mp3")
            title = info.get('title', 'Noma\'lum')
            artist = info.get('uploader', '') or info.get('channel', '')
            
            if os.path.exists(mp3_path):
                return mp3_path, title, artist
            
            # If MP3 doesn't exist, check if another format exists (maybe ffmpeg failed)
            files = glob.glob(os.path.join(DOWNLOAD_DIR, f"{video_id}.*"))
            if files:
                # Prefer audio formats
                for f in files:
                    if f.endswith(('.mp3', '.m4a', '.opus', '.ogg', '.wav')):
                        return f, title, artist
                # Fallback to whatever was downloaded
                return files[0], title, artist
            
            return None, None, None
    except Exception as e:
        logging.error(f"Music download error: {type(e).__name__}: {e}")
        if "ffmpeg" in str(e).lower():
            logging.error("FFmpeg not found! YouTube music download (as MP3) failed.")
        return None, None, None


# ========================
# Bot Handlers
# ========================

@dp.message(CommandStart())
async def cmd_start(message: types.Message):
    await message.answer(
        "🎵 Salom! Men ko'p funksiyali botman:\n\n"
        "🎶 **Musiqa qidirish** — qo'shiq yoki qo'shiqchi nomini yozing\n"
        "📸 **Instagram yuklab olish** — Instagram linkini yuboring\n\n"
        "Boshlash uchun qo'shiq nomini yoki Instagram linkini yuboring! 🚀",
        parse_mode="Markdown"
    )

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
            is_video = filename.lower().endswith(('.mp4', '.mkv', '.mov'))
            
            if is_video:
                await message.answer_video(video=media_file, caption=caption)
                
                # If it's a Reels/Video, extract and send audio too
                if "/reel/" in url or "/reels/" in url or is_video:
                    audio_path = await loop.run_in_executor(None, extract_reels_audio, filename)
                    if audio_path and os.path.exists(audio_path):
                        audio_file = FSInputFile(audio_path)
                        await message.answer_audio(
                            audio=audio_file, 
                            caption="🎵 Videodagi musiqa"
                        )
                        # Clean up audio file
                        try:
                            os.remove(audio_path)
                        except:
                            pass
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


@dp.message(F.text)
async def handle_music_search(message: types.Message):
    """Handle music search - any text that is not an Instagram link."""
    query = message.text.strip()
    
    # Skip if too short
    if len(query) < 2:
        return
    
    # Skip commands
    if query.startswith('/'):
        return
    
    status_msg = await message.reply("🔍 Qidirilmoqda...")

    loop = asyncio.get_event_loop()
    songs = await loop.run_in_executor(None, search_music, query)

    if not songs:
        await status_msg.edit_text("😕 Hech narsa topilmadi. Boshqa so'z bilan qidirib ko'ring.")
        return

    # Build inline keyboard with results
    buttons = []
    for i, song in enumerate(songs, 1):
        btn_text = f"🎵 {song['title'][:45]} [{song['duration']}]"
        buttons.append([InlineKeyboardButton(
            text=btn_text,
            callback_data=f"music:{song['key']}"
        )])
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
    
    await status_msg.edit_text(
        f"🎶 **\"{query}\"** bo'yicha natijalar:\n\nYuklash uchun qo'shiqni tanlang 👇",
        reply_markup=keyboard,
        parse_mode="Markdown"
    )


@dp.callback_query(F.data.startswith("music:"))
async def handle_music_callback(callback: CallbackQuery):
    """Handle music download when user clicks a song button."""
    short_key = callback.data.replace("music:", "")
    
    # Get song info from cache
    song_info = music_cache.get(short_key)
    if not song_info:
        await callback.answer("⏰ Qidiruv eskirgan. Qaytadan qidiring.", show_alert=True)
        return
    
    await callback.answer("⏳ Yuklanmoqda...")
    
    # Edit the message to show downloading status
    await callback.message.edit_text(
        f"⏳ Yuklanmoqda: **{song_info['title'][:50]}**...",
        parse_mode="Markdown"
    )
    
    # Download the music
    loop = asyncio.get_event_loop()
    filepath, title, artist = await loop.run_in_executor(
        None, download_music, song_info['url']
    )
    
    if filepath and os.path.exists(filepath):
        try:
            audio_file = FSInputFile(filepath)
            await callback.message.answer_audio(
                audio=audio_file,
                title=title or song_info['title'],
                performer=artist or song_info.get('artist', ''),
                caption="🎵 Mana qo'shigingiz!"
            )
            await callback.message.delete()
        except Exception as e:
            logging.error(f"Error sending audio: {e}")
            await callback.message.edit_text("😕 Audio yuborishda xatolik bo'ldi.")
        finally:
            try:
                if os.path.exists(filepath):
                    os.remove(filepath)
            except Exception as e:
                logging.error(f"Error deleting file: {e}")
    else:
        await callback.message.edit_text(
            "😕 Bu qo'shiqni yuklab bo'lmadi. Boshqasini tanlang."
        )


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
