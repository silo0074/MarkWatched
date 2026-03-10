# You need to create a .desktop file in
# ~/.local/share/kxmlgui5/servicemenu/ (or /usr/share/kio/servicemenus/).

# You will need to install the Python Imaging Library:
# pip install Pillow


from PIL import Image, ImageDraw, ImageFont
import hashlib
import os
import urllib.parse
import subprocess
import sys
import argparse

def get_thumbnail_path(video_path):
    # Standard Freedesktop pathing
    uri = "file://" + urllib.parse.quote(os.path.abspath(video_path))
    hash_name = hashlib.md5(uri.encode('utf-8')).hexdigest()
    # Check 'large' or 'normal' folders
    for folder in ['large', 'normal']:
        t_path = os.path.expanduser(f"~/.cache/thumbnails/{folder}/{hash_name}.png")
        if os.path.exists(t_path):
            return t_path
    return None


def apply_overlay(video_path, percentage, watched=False):
    t_path = get_thumbnail_path(video_path)
    if not t_path: return

    with Image.open(t_path) as img:
        draw = ImageDraw.Draw(img)
        w, h = img.size

        if watched:
            # Draw green checkmark in bottom right
            draw.rectangle([w-30, h-30, w-5, h-5], fill="green")
            draw.line([w-25, h-18, w-20, h-10, w-10, h-25], fill="white", width=3)
        else:
            # Draw percentage text
            text = f"{int(percentage)}%"
            draw.text((w-40, h-20), text, fill="white", stroke_fill="black", stroke_width=1)

        img.save(t_path)


def mark_metadata_watched(video_path):
    # Adding a tag is often more flexible than comments
    subprocess.run(["tagger", video_path, "+watched"], capture_output=True)
    # Or set a custom property via baloo
    # subprocess.run(["balooctl", "index", video_path], capture_output=True)


# To get duration if not in INI:
result = subprocess.run(
    ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", video_file],
    capture_output=True, text=True
)
duration = float(result.stdout)

parser = argparse.ArgumentParser()
parser.add_argument('path')
parser.add_argument('--sync', action='store_true')
args = parser.parse_args()

def process_path(target):
    if os.path.isdir(target):
        for root, dirs, files in os.walk(target):
            for f in files:
                if f.lower().endswith(('.mp4', '.mkv', '.avi')):
                    update_video_status(os.path.join(root, f))
    else:
        update_video_status(target)

def update_video_status(video_file):
    # 1. Get rotation (your current code)
    # 2. Get current_sec from INI
    # 3. Calculate percentage (requires duration, see below)
    # 4. Run apply_overlay()
    pass




# --- CONFIGURATION ---
INI_BASE_PATH = os.path.expanduser("~/.config/smplayer/file_settings/")
THUMB_BASE = os.path.expanduser("~/.cache/thumbnails/")
MIN_THRESHOLD = 5.0   # % below which is "unwatched"
MAX_THRESHOLD = 90.0  # % above which is "watched" (green check)

def get_smplayer_hash(filename):
    """Calculates the 64-bit MD5-like hash used by SMPlayer."""
    try:
        size = os.path.getsize(filename)
        longlongformat = '<q'
        bytesize = struct.calcsize(longlongformat)
        with open(filename, "rb") as f:
            hash_val = size
            for _ in range(65536 // bytesize):
                buffer = f.read(bytesize)
                if not buffer: break
                (l_value,) = struct.unpack(longlongformat, buffer)
                hash_val = (hash_val + l_value) & 0xFFFFFFFFFFFFFFFF
            f.seek(max(0, size - 65536), 0)
            for _ in range(65536 // bytesize):
                buffer = f.read(bytesize)
                if not buffer: break
                (l_value,) = struct.unpack(longlongformat, buffer)
                hash_val = (hash_val + l_value) & 0xFFFFFFFFFFFFFFFF
        return "%016x" % hash_val
    except: return None

def get_kde_thumbnail_path(video_path):
    """Finds the existing KDE thumbnail for a given file path."""
    abs_path = os.path.abspath(video_path)
    uri = "file://" + urllib.parse.quote(abs_path)
    # KDE/Freedesktop thumbnails are MD5(URI).png
    thumb_name = hashlib.md5(uri.encode('utf-8')).hexdigest() + ".png"

    for size in ['large', 'normal']:
        full_path = os.path.join(THUMB_BASE, size, thumb_name)
        if os.path.exists(full_path):
            return full_path
    return None

def get_progress_from_ini(filename):
    """Extracts current_sec and duration to calculate percentage."""
    h = get_smplayer_hash(filename)
    if not h: return None

    ini_path = os.path.join(INI_BASE_PATH, h[0], f"{h}.ini")
    if not os.path.exists(ini_path):
        return None

    data = {}
    with open(ini_path, "r") as f:
        for line in f:
            if "=" in line:
                key, val = line.strip().split("=", 1)
                data[key] = val

    curr = float(data.get("current_sec", 0))
    total = float(data.get("duration", 0))

    if total > 0:
        return (curr / total) * 100
    return 0

def draw_overlay(thumb_path, percentage):
    """Modifies the thumbnail with a checkmark or progress text."""
    try:
        with Image.open(thumb_path) as img:
            img = img.convert("RGBA")
            draw = ImageDraw.Draw(img)
            w, h = img.size

            if percentage >= MAX_THRESHOLD:
                # Green Checkmark Box
                box_size = int(w * 0.2)
                draw.rectangle([w-box_size, h-box_size, w, h], fill=(0, 150, 0, 200))
                draw.line([w-(box_size*0.8), h-(box_size*0.5), w-(box_size*0.5), h-(box_size*0.2), w-(box_size*0.2), h-(box_size*0.8)], fill="white", width=2)
            elif percentage > MIN_THRESHOLD:
                # Progress Text
                text = f"{int(percentage)}%"
                draw.rectangle([w-int(w*0.3), h-20, w, h], fill=(0, 0, 0, 150))
                draw.text((w-int(w*0.25), h-18), text, fill="white")

            img.save(thumb_path)
    except Exception as e:
        print(f"Error drawing on {thumb_path}: {e}")

def process_file(filepath):
    perc = get_progress_from_ini(filepath)
    if perc is not None:
        t_path = get_kde_thumbnail_path(filepath)
        if t_path:
            draw_overlay(t_path, perc)
            print(f"Updated: {os.path.basename(filepath)} ({int(perc)}%)")

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument('path', help="File or directory to process")
    parser.add_argument('--recursive', action='store_true')
    args = parser.parse_args()

    if os.path.isdir(args.path):
        for root, dirs, files in os.walk(args.path):
            for f in files:
                if f.lower().endswith(('.mp4', '.mkv', '.avi', '.mov')):
                    process_file(os.path.join(root, f))
            if not args.recursive: break
    else:
        process_file(args.path)

