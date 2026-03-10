# You need to create a .desktop file in
# ~/.local/share/kxmlgui5/servicemenu/ (or /usr/share/kio/servicemenus/).

# You will need to install the Python Imaging Library:
# pip install Pillow
# sudo zypper install ffmpeg


import os
import struct
import hashlib
import argparse
import subprocess
import traceback  # Added for detailed error reporting
import urllib.parse
# from pathlib import Path
from PIL import Image, ImageDraw, ImageFont, PngImagePlugin


# --- CONFIGURATION ---
INI_BASE_PATH = os.path.expanduser("~/.config/smplayer/file_settings/")
THUMB_BASE = os.path.expanduser("~/.cache/thumbnails/")
MIN_THRESHOLD = 5.0   # % below which is "unwatched"
MAX_THRESHOLD = 90.0  # % above which is "watched" (green check)
VIDEO_EXTS = ('.mp4', '.mkv', '.avi', '.mov', '.webm', '.flv', '.wmv')


def get_duration(video_path):
    """Uses ffprobe to get video duration since SMPlayer doesn't save it."""
    try:
        cmd = [
            "ffprobe", "-v", "error", "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1", video_path
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        return float(result.stdout.strip())
    except Exception:
        traceback.print_exc()
        return None


def mark_metadata_watched(video_path):
    # Adding a tag is often more flexible than comments
    subprocess.run(["tagger", video_path, "+watched"], capture_output=True)
    # Or set a custom property via baloo
    # subprocess.run(["balooctl", "index", video_path], capture_output=True)


def get_smplayer_hash(filename):
    """Calculates the 64-bit MD5-like hash used by SMPlayer/OpenSubtitles."""
    try:
        size = os.path.getsize(filename)
        longlongformat = '<q'
        bytesize = struct.calcsize(longlongformat)

        with open(filename, "rb") as f:
            hash_val = size
            # Hash first 64KB
            for _ in range(65536 // bytesize):
                buffer = f.read(bytesize)
                (l_value,) = struct.unpack(longlongformat, buffer)
                hash_val = (hash_val + l_value) & 0xFFFFFFFFFFFFFFFF

            # Hash last 64KB
            f.seek(max(0, size - 65536), 0)
            for _ in range(65536 // bytesize):
                buffer = f.read(bytesize)
                (l_value,) = struct.unpack(longlongformat, buffer)
                hash_val = (hash_val + l_value) & 0xFFFFFFFFFFFFFFFF

        return "%016x" % hash_val
    except Exception:
        traceback.print_exc()
        return None


def get_progress_from_ini(filename):
    """Extracts current_sec and duration to calculate percentage."""
    try:
        h = get_smplayer_hash(filename)
        if not h: return None
        
        ini_path = os.path.join(INI_BASE_PATH, h[0], f"{h}.ini")
        print("SMPlayer INI path: " + ini_path)
        if not os.path.exists(ini_path):
            return None

        data = {}
        with open(ini_path, "r", errors='replace') as f:
            for line in f:
                if line.startswith("current_sec="):
                    val = line.strip().split("=")[1]
                    print("current_sec=" + val)
                    return val
                # if "=" in line:
                #     key, val = line.strip().split("=", 1)
                #     data[key] = val
        # curr = float(data.get("current_sec", 0))
                    
        return 0
    except Exception:
        traceback.print_exc()
        return None


def get_kde_thumbnail_path(video_path):
    """Finds the existing KDE thumbnail by matching KDE's URI encoding style."""
    try:
        abs_path = os.path.abspath(video_path)
        
        # We manually build the URI to control percent-encoding.
        # Freedesktop/KDE typically does NOT encode: / _ - . ~ [ ]
        # It DOES encode: # % and non-ascii (like the lightning bolt)
        
        path_encoded = urllib.parse.quote(abs_path, safe="!;:()&$/_-.,~*+=@")
        uri = f"file://{path_encoded}"
        
        # MD5 hash of the URI
        thumb_name = hashlib.md5(uri.encode('utf-8')).hexdigest() + ".png"

        print(f"DEBUG: Processing {os.path.basename(video_path)}")
        print(f"DEBUG: URI: {uri}")
        print(f"DEBUG: Calculated Hash: {thumb_name}")

        # Check all standard thumbnail locations
        for size in ['large', 'normal', 'x-large', 'xx-large']:
            full_path = os.path.join(THUMB_BASE, size, thumb_name)
            if os.path.exists(full_path):
                print(f"DEBUG: Found thumbnail at: {full_path}")
                return full_path
        
        print("DEBUG: No thumbnail found in cache.")
        return None
    except Exception:
        traceback.print_exc()
        return None


def update_thumbnail(thumb_path, percentage, mode):
    """Handles backup, restoration, and drawing."""
    try:
        bak_path = thumb_path + ".bak"

        # Manage the Backup
        if not os.path.exists(bak_path):
            # Create backup from original if it doesn't exist
            os.replace(thumb_path, bak_path)
        
        if mode == "unwatched":
            # Restore original and remove backup
            os.replace(bak_path, thumb_path)
            return

        # Draw on top of the CLEAN backup
        with Image.open(bak_path) as img:
            # This captures the Thumb::MTime, Thumb::URI, etc.
            metadata = img.info

            img = img.convert("RGBA")
            draw = ImageDraw.Draw(img)
            w, h = img.size
            
            if mode == "watched":
                # Green Checkmark
                box_s = int(w * 0.25)
                draw.rectangle([w-box_s, h-box_s, w, h], fill=(0, 150, 0, 200))
                draw.line([w-box_s*0.8, h-box_s*0.5, w-box_s*0.5, h-box_s*0.2, w-box_s*0.2, h-box_s*0.8], fill="white", width=3)
            elif mode == "sync":
                # Percentage Text
                text = f"{int(percentage)}%"
                draw.rectangle([0, h-int(h*0.2), w, h], fill=(0, 0, 0, 160))
                draw.text((int(w/2)-10, h-int(h*0.18)), text, fill="white")
                # draw.text((w-40, h-20), text, fill="white", stroke_fill="black", stroke_width=1)
            
            # Save with Original Metadata
            # This is the critical step to stop Dolphin from deleting it
            pnginfo = PngImagePlugin.PngInfo()
            for k, v in metadata.items():
                if isinstance(v, str): # Only copy string-based metadata
                    pnginfo.add_text(k, v)
            
            img.save(thumb_path, "PNG", pnginfo=pnginfo)
    except Exception:
        traceback.print_exc()


def process_item(item_path, mode):
    t_path = get_kde_thumbnail_path(item_path)
    print(f"Thumbnail path: {t_path}")
    if not t_path and not os.path.exists(str(t_path)):
        print("Thumbnail not found")
        return

    percentage = 0
    if mode == "watched":
        percentage = 100
    elif mode == "sync":
        current_sec = get_progress_from_ini(item_path)
        current_sec = float(current_sec.strip())
        duration = get_duration(item_path)
        percentage = (current_sec / duration * 100) if duration > 0 else 0
        print(f"Video duration from INI: {current_sec}")
        print(f"Video duration: {duration}")
        print(f"Watch time: {percentage}%")
        if percentage < MIN_THRESHOLD:
            mode = "unwatched"
        elif percentage > MAX_THRESHOLD:
            mode = "watched"
        print("Updated mode: " + mode)

    update_thumbnail(t_path, percentage, mode)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('paths', nargs='+', help="File or folder paths")
    parser.add_argument('--mark-watched', action='store_true')
    parser.add_argument('--mark-unwatched', action='store_true')
    parser.add_argument('--sync', action='store_true')
    args = parser.parse_args()

    mode = "sync"
    if args.mark_watched: mode = "watched"
    if args.mark_unwatched: mode = "unwatched"

    for path in args.paths:
        if os.path.isdir(path):
            for root, _, files in os.walk(path):
                for f in files:
                    if f.lower().endswith(VIDEO_EXTS):
                        process_item(os.path.join(root, f), mode)
        else:
            process_item(path, mode)

if __name__ == "__main__":
    main()

