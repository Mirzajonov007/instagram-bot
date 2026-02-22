import os
import logging
import yt_dlp
import imageio_ffmpeg
import sys

logging.basicConfig(level=logging.INFO)

def check_env():
    print("--- Environment Check ---")
    print(f"Python version: {sys.version}")
    
    # Check FFmpeg
    print("\n--- FFmpeg Check ---")
    try:
        ffmpeg_exe = imageio_ffmpeg.get_ffmpeg_exe()
        if ffmpeg_exe and os.path.exists(ffmpeg_exe):
            print(f"SUCCESS: FFmpeg found at {ffmpeg_exe}")
        else:
            print("FAILURE: FFmpeg NOT found via imageio-ffmpeg")
    except Exception as e:
        print(f"ERROR: Exception while checking FFmpeg: {e}")

    # Check yt-dlp
    print("\n--- yt-dlp Check ---")
    try:
        version = yt_dlp.version.__version__
        print(f"SUCCESS: yt-dlp version: {version}")
        
        # Simple search test
        print("Testing yt-dlp search...")
        ydl_opts = {
            'quiet': True,
            'extract_flat': True,
            'default_search': 'ytsearch1',
        }
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            result = ydl.extract_info("ytsearch1:Python programming", download=False)
            if result and 'entries' in result and len(result['entries']) > 0:
                print(f"SUCCESS: yt-dlp search working. Found: {result['entries'][0].get('title')}")
            else:
                print("FAILURE: yt-dlp search returned no results")
    except Exception as e:
        print(f"ERROR: yt-dlp check failed: {e}")

if __name__ == "__main__":
    check_env()
