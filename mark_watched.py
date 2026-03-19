# You need to create a .desktop file in
# ~/.local/share/kio/servicemenus/ (or /usr/share/kio/servicemenus/).

# You will need to install the Python Imaging Library:
# For openSUSE: sudo zypper install python3-Pillow ffmpeg xdotool rsvg-convert
# For Debian/Ubuntu: sudo apt install python3-pil ffmpeg xdotool rsvg-convert

# Usage
# Use all view modes in file manager to generate thumbnails.
# Use the menu to mark a folder or multiple file as watched.
# Use sync to synchronize the watch progress from SMPlayer INI files with thumbnails.
# Use unwatched to remove watch progress from thumbnails.
# Even if thumbnails are deleted the progress will be updated using INI files.

# Debug commands:
# qdbus6 | grep dolphin
# org.kde.dolphin-342959
# qdbus6 org.kde.dolphin-342959 /dolphin/Dolphin_1

import os
import struct
import hashlib
import argparse
import subprocess
import time
import concurrent.futures
import shutil
import traceback  # Added for detailed error reporting
import urllib.parse
import threading
import configparser
from enum import Enum
# from pathlib import Path
from PIL import Image, ImageDraw, ImageFont, PngImagePlugin


# --- CONFIGURATION ---
INI_BASE_PATH = os.path.expanduser("~/.config/smplayer/file_settings/")
THUMB_BASE = os.path.expanduser("~/.cache/thumbnails/")
MIN_THRESHOLD = 5.0   # % below which is "unwatched"
MAX_THRESHOLD = 90.0  # % above which is "watched" (green check)
VIDEO_EXTS = ('.mp4', '.mkv', '.avi', '.mov', '.webm', '.flv', '.wmv', '.mpeg', '.mpg')

CONFIG_DIR = os.path.expanduser("~/.config/markwatched/")
CONFIG_FILE = os.path.join(CONFIG_DIR, "config.ini")

APP_NAME = 'MarkWatched'
APP_VERSION = '1.0.0'

class AppMode(Enum):
    WATCHED = "watched"
    UNWATCHED = "unwatched"
    SYNC = "sync"
    BACKUP = "backup"

# Create a global lock
progress_lock = threading.Lock()
GLOBAL_BACKUP_PATH = None

def get_qdbus_cmd():
    """Detects the available qdbus command on the system."""
    for cmd in ["qdbus-qt6", "qdbus6", "qdbus"]:
        if subprocess.run(["which", cmd], capture_output=True).returncode == 0:
            return cmd
    return "qdbus" # Fallback

QDBUS = get_qdbus_cmd()
print(QDBUS)


def force_baloo_update(file_path):
    """Tell Baloo to forget the file and re-index it from scratch."""
    thread_id = threading.get_native_id()
    
    try:
        # 1. Clear the old entry
        subprocess.run(["balooctl6", "clear", file_path], capture_output=True, text=True)
        
        # 2. THE FIX: Small sleep to let Baloo's database lock release
        # 0.2 seconds is usually enough for the Baloo worker to cycle
        time.sleep(0.2)
        
        # 3. Force a fresh index
        subprocess.run(["balooctl6", "index", file_path], capture_output=True, text=True)
        
        print(f"[Thread-{thread_id}] Baloo index cycled for {os.path.basename(file_path)}")
    except Exception:
        traceback.print_exc()


def get_current_tags(file_path):
    """Returns a list of current tags for the file."""
    try:
        # -n user.xdg.tags: get only the tags attribute
        # --only-values: just get the string value, no header
        result = subprocess.run(
            ["getfattr", "-n", "user.xdg.tags", "--only-values", file_path],
            capture_output=True, text=True
        )
        if result.returncode == 0:
            # Tags are usually comma-separated in the attribute
            return [t.strip() for t in result.stdout.split(",") if t.strip()]
    except Exception:
        pass
    return []


def add_dolphin_tag(file_path, tag_name="watched"):
    """Adds a tag without duplicating it or overwriting others."""
    thread_id = threading.get_native_id()
    current_tags = get_current_tags(file_path)

    if tag_name in current_tags:
        print(f"[Thread-{thread_id}] Tag '{tag_name}' already exists.")
        return

    current_tags.append(tag_name)
    new_tags_str = ",".join(current_tags)

    try:
        subprocess.run(
            ["setfattr", "-n", "user.xdg.tags", "-v", new_tags_str, file_path],
            check=True, capture_output=True
        )

        # Force Baloo to update its database for this file
        force_baloo_update(file_path)

        print(f"[Thread-{thread_id}] Added tag '{tag_name}' to {os.path.basename(file_path)}")
    except subprocess.CalledProcessError as e:
        print(f"[Thread-{thread_id}] Error adding tag: {e.stderr.decode().strip()}")


def remove_dolphin_tag(file_path, tag_name="watched"):
    """Removes ONLY the specified tag, preserving all others."""
    thread_id = threading.get_native_id()
    current_tags = get_current_tags(file_path)

    if tag_name not in current_tags:
        return

    # Filter out the tag
    new_tags = [t for t in current_tags if t != tag_name]

    try:
        if not new_tags:
            # If no tags left, remove the attribute entirely to keep it clean
            subprocess.run(["setfattr", "-x", "user.xdg.tags", file_path], check=True)
        else:
            # Otherwise, write the remaining tags back
            new_tags_str = ",".join(new_tags)
            subprocess.run(
                ["setfattr", "-n", "user.xdg.tags", "-v", new_tags_str, file_path],
                check=True
            )

        # Force Baloo to update its database for this file
        force_baloo_update(file_path)

        print(f"[Thread-{thread_id}] Removed tag '{tag_name}' from {os.path.basename(file_path)}")
    except subprocess.CalledProcessError as e:
        # Note: -x might fail if the attribute was already gone, which is fine
        pass


def get_backup_directory():
    """Gets or sets the backup directory using a persistent config file."""
    config = configparser.ConfigParser()
    if os.path.exists(CONFIG_FILE):
        config.read(CONFIG_FILE)
    
    path = config.get("General", "backup_path", fallback=None)
    
    if path and os.path.isdir(path):
        return path
    
    # Prompt user
    try:
        cmd = ["kdialog", "--title", APP_NAME, "--getexistingdirectory", os.path.expanduser("~")]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode == 0:
            selected_path = result.stdout.strip()
            if selected_path:
                if not os.path.exists(CONFIG_DIR):
                    os.makedirs(CONFIG_DIR)
                if not config.has_section("General"):
                    config.add_section("General")
                config.set("General", "backup_path", selected_path)
                with open(CONFIG_FILE, "w") as f:
                    config.write(f)
                return selected_path
    except Exception:
        traceback.print_exc()
    
    return None

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


def get_smplayer_ini_data(filename):
    """Extracts specified data from the SMPlayer INI file."""

    # Get a unique identifier for the current thread
    thread_id = threading.get_native_id()

    try:
        h = get_smplayer_hash(filename)
        if not h:
            return {}  # Return empty dict if hash fails

        ini_path = os.path.join(INI_BASE_PATH, h[0], f"{h}.ini")
        print(f"[Thread-{thread_id}] SMPlayer INI path: " + ini_path)

        if not os.path.exists(ini_path):
            return {} # Return empty dict if file doesn't exist

        data = {}
        desired_keys = {
            "current_sec",
            "watchmark_duration",
            "watchmark_progress",
            "watchmark_override",
        }

        with open(ini_path, "r", errors='replace') as f:
            for line in f:
                if "=" in line:
                    key, val = line.strip().split("=", 1)
                    if key in desired_keys:
                        data[key] = val

        print(f"[Thread-{thread_id}] INI data: {data}")
        return data
    except Exception:
        traceback.print_exc()
        return {}


def write_smplayer_ini_data(filename, data_to_write):
    """Writes multiple key-value pairs to the SMPlayer INI file, replacing if they exist."""
    # Get a unique identifier for the current thread
    thread_id = threading.get_native_id()

    if not data_to_write:
        return
    try:
        h = get_smplayer_hash(filename)
        if not h: return

        ini_dir = os.path.join(INI_BASE_PATH, h[0])
        os.makedirs(ini_dir, exist_ok=True)
        ini_path = os.path.join(ini_dir, f"{h}.ini")

        lines = []
        if os.path.exists(ini_path):
            with open(ini_path, 'r', errors='replace') as f:
                lines = f.readlines()

        keys_to_replace = data_to_write.keys()
        new_lines = [line for line in lines if not any(line.strip().startswith(key + '=') for key in keys_to_replace)]
        new_lines.extend([f"{key}={value}\n" for key, value in data_to_write.items()])

        with open(ini_path, "w", errors='replace') as f:
            f.writelines(new_lines)

        print(f"[Thread-{thread_id}] Wrote {data_to_write} to {ini_path}")
    except Exception:
        traceback.print_exc()


def get_kde_thumbnail_path(video_path):
    """Finds the existing KDE thumbnail by matching KDE's URI encoding style."""
    """Returns a list of all existing thumbnail size variants for the file."""

    # Get a unique identifier for the current thread
    thread_id = threading.get_native_id()

    try:
        abs_path = os.path.abspath(video_path)
        
        # We manually build the URI to control percent-encoding.
        # Freedesktop/KDE typically does NOT encode: / _ - . ~ [ ]
        # It DOES encode: # % and non-ascii (like the lightning bolt)
        
        path_encoded = urllib.parse.quote(abs_path, safe="!;:()&$/_-.,~*+=@")
        uri = f"file://{path_encoded}"
        
        # MD5 hash of the URI
        thumb_name = hashlib.md5(uri.encode('utf-8')).hexdigest() + ".png"

        # print(f"[Thread-{thread_id}] DEBUG: Processing {os.path.basename(video_path)}")
        # print(f"[Thread-{thread_id}] DEBUG: URI: {uri}")
        # print(f"[Thread-{thread_id}] DEBUG: Calculated Hash: {thumb_name}")

        # Check all standard thumbnail locations
        found_paths = []
        for size in ['large', 'normal', 'x-large', 'xx-large']:
            full_path = os.path.join(THUMB_BASE, size, thumb_name)
            if os.path.exists(full_path):
                print(f"[Thread-{thread_id}] DEBUG: Found thumbnail at: {full_path}")
                found_paths.append(full_path)
        
        return found_paths
    except Exception:
        traceback.print_exc()
        return []


def draw_checkmark(draw, center, radius, color=(50, 255, 50, 255)):
    """
    Draws a smooth checkmark. Uses a 'joint circle' at the vertex 
    to ensure no pixels are missing in the V-shape.
    """
    # Scale width for oversampling (approx 20% of circle radius)
    line_width = max(8, int(radius * 0.22))
    joint_radius = line_width // 2
    
    # Points for the checkmark 'V'
    # p1: Left Start, p2: Bottom Vertex (Pivot), p3: Right Top
    p1 = (center[0] - radius * 0.5, center[1] + radius * 0.05)
    p2 = (center[0] - radius * 0.1, center[1] + radius * 0.45)
    p3 = (center[0] + radius * 0.55, center[1] - radius * 0.35)
    
    points = [p1, p2, p3]

    # 1. Draw the lines with rounded joints (Pillow 8.2+)
    draw.line(points, fill=color, width=line_width, joint="round")
    
    # Draw a circle exactly at the vertex p2 to bridge any gaps
    # This ensures the 'hinge' of the V is perfectly solid.
    draw.ellipse(
        [p2[0] - joint_radius, p2[1] - joint_radius, 
         p2[0] + joint_radius, p2[1] + joint_radius], 
        fill=color
    )
    
    # Optional: Add small round caps at the start and end of the lines
    # for an even more polished 'premium' look
    for p in [p1, p3]:
        draw.ellipse(
            [p[0] - joint_radius, p[1] - joint_radius, 
             p[0] + joint_radius, p[1] + joint_radius], 
            fill=color
        )


def apply_visual_overlay(img, percentage, mode, folder, is_small):
    """
    Creates a high-resolution overlay layer, draws UI elements, 
    and downscales back to the image size for antialiasing.
    """

    # Get a unique identifier for the current thread
    thread_id = threading.get_native_id()

    w, h = img.size
    oversample = 4  # Draw at 4x size for high-quality edges
    canvas_w, canvas_h = w * oversample, h * oversample
    
    # High-res transparent layer
    overlay = Image.new("RGBA", (canvas_w, canvas_h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    if mode == AppMode.WATCHED:
        if is_small:
            print(f"[Thread-{thread_id}] DEBUG: checkmark for small thumbnails")
            # For list/details view
            center = (canvas_w // 2, canvas_h // 2)
            circle_r = int(canvas_h * 0.45)
        else:
            print(f"[Thread-{thread_id}] DEBUG: checkmark for bigger thumbnails")
            # For icon view
            circle_r = int(min(canvas_w, canvas_h) * 0.16)
            # Higher margin for folders to avoid overlapping the 'tab' of the folder icon
            margin_bottom = int(canvas_h * 0.15) if folder else int(canvas_h * 0.08)
            margin_right = int(canvas_w * 0.12)
            center = (canvas_w - circle_r - margin_right, canvas_h - circle_r - margin_bottom)

        # Draw Background Circle (Dark translucent)
        draw.ellipse(
            [center[0]-circle_r, center[1]-circle_r, center[0]+circle_r, center[1]+circle_r], 
            fill=(0, 0, 0, 180)
        )

        # Draw the Checkmark inside the circle
        draw_checkmark(draw, center, circle_r)

    elif mode == AppMode.SYNC:
        # Progress Bar Logic (Green)
        bar_h = canvas_h // 4 if is_small else max(20, int(canvas_h * 0.08))
        # Background track
        draw.rectangle([0, canvas_h - bar_h, canvas_w, canvas_h], fill=(0, 0, 0, 160))
        # Progress fill
        bar_w = int(canvas_w * (percentage / 100))
        draw.rectangle([0, canvas_h - bar_h, bar_w, canvas_h], fill=(50, 255, 50, 255))

    # Downscale overlay using LANCZOS (highest quality)
    overlay = overlay.resize((w, h), resample=Image.LANCZOS)
    
    # Merge overlay with original
    return Image.alpha_composite(img.convert("RGBA"), overlay)


def update_thumbnail(thumb_path, percentage, mode, folder=False):
    """Handles backup, restoration, and drawing."""
    # Get a unique identifier for the current thread
    thread_id = threading.get_native_id()

    try:
        # Determine paths
        bak_path = thumb_path if folder else thumb_path + ".bak"
        print(f"[Thread-{thread_id}] Updating thumbnail: {thumb_path}")

        # Manage the Backup (for non-folder items)
        if not folder and not os.path.exists(bak_path):
            os.replace(thumb_path, bak_path)
        
        # Handle Restoration for 'unwatched' mode
        if mode == AppMode.UNWATCHED:
            if not folder:
                if os.path.exists(bak_path):
                    os.replace(bak_path, thumb_path)
            else:
                item_path = thumb_path 
                print(f"[Thread-{thread_id}] Removing watch mode from folder: {item_path}")
                for f_name in [".directory", ".folder_watched.png"]:
                    f_path = os.path.join(item_path, f_name)
                    if os.path.exists(f_path):
                        os.remove(f_path)
                        print(f"[Thread-{thread_id}] Deleted: {f_path}")
            return
                
        # Drawing Logic
        with Image.open(bak_path) as img:
            # Capture KDE metadata (Thumb::URI, etc.)
            metadata = img.info
            is_small = img.size[1] <= 128

            # Apply the visuals (Antialiased)
            combined = apply_visual_overlay(img, percentage, mode, folder, is_small)

            # Re-attach Metadata to the PNG
            pnginfo = PngImagePlugin.PngInfo()
            for k, v in metadata.items():
                if isinstance(v, (str, bytes)):
                    pnginfo.add_text(k, str(v))
            
            # Save final result
            combined.convert("RGB").save(thumb_path, "PNG", pnginfo=pnginfo)
            
    except Exception:
        traceback.print_exc()


def process_item(item_path, mode):
    # Get a unique identifier for the current thread
    thread_id = threading.get_native_id()

    print("----------------------------------------")
    print(f"[Thread-{thread_id}] Starting: {item_path}")

    if mode == AppMode.BACKUP:
        if not GLOBAL_BACKUP_PATH:
            return

        h = get_smplayer_hash(item_path)
        if not h: return
        
        src_ini = os.path.join(INI_BASE_PATH, h[0], f"{h}.ini")
    
        if os.path.exists(src_ini):
            dest_dir = os.path.join(GLOBAL_BACKUP_PATH, h[0])
            os.makedirs(dest_dir, exist_ok=True)
            
            # This will overwrite the file if it exists.
            # It also preserves the original metadata (timestamps, etc.)
            shutil.copy2(src_ini, dest_dir)
            
            print(f"[Thread-{thread_id}] Backed up INI to {dest_dir}")
        return

    t_paths = get_kde_thumbnail_path(item_path)

    print(f"[Thread-{thread_id}] Thumbnail path: {t_paths}")

    if not t_paths:
        print(f"[Thread-{thread_id}] Thumbnail not found")
        return

    ini_data_to_write = {}
    watch_progress_perc = 0
    ini_override_int = 0
    ini_progress_float = 0
    should_update_thumbnail = False
    thumbnail_backup_exists = True

    ini_data = get_smplayer_ini_data(item_path)
    ini_current_sec_str = ini_data.get("current_sec")
    ini_duration_str = ini_data.get("watchmark_duration")
    ini_progress_str = ini_data.get("watchmark_progress")
    ini_override_str = ini_data.get("watchmark_override")

    if ini_override_str is not None and ini_override_str != "None":
        ini_override_int = int(ini_override_str.strip())

    if ini_progress_str is not None and ini_progress_str != "None":
        ini_progress_float = float(ini_progress_str.strip())

    # Update the thumbnails if INI file exists but thumbnail backup does not
    for path in t_paths:
        bak_path = path + ".bak"
        if not os.path.exists(bak_path):
            should_update_thumbnail = True
            thumbnail_backup_exists = False

    print(f"[Thread-{thread_id}] Received mode: {mode}")

    if mode == AppMode.WATCHED:
        # Set the manual override to 1 to prevent sync from changing it, 
        # set watch progress to 100 to save the 'watched' status.
        # Override bit and 100 percent watch progress means watched.
        # Write INI only if data differs.
        watch_progress_perc = 100
        if ini_progress_float != 100 or ini_override_int != 1:
            ini_data_to_write["watchmark_override"] = 1
            ini_data_to_write["watchmark_progress"] = watch_progress_perc
            should_update_thumbnail = True

    elif mode == AppMode.UNWATCHED:
        # Remove manual override and set progress to 0
        if ini_progress_float != 0 or ini_override_int != 0:
            ini_data_to_write["watchmark_override"] = 0
            ini_data_to_write["watchmark_progress"] = watch_progress_perc
        should_update_thumbnail = True

    elif mode == AppMode.SYNC:
        duration = 0
        current_sec = 0

        if ini_current_sec_str is None:
            print(f"[Thread-{thread_id}] No progress time found in INI")
            # If there's no progress data, we can't sync.
            should_update_thumbnail = False
        else:
            current_sec = float(ini_current_sec_str.strip())
            should_update_thumbnail = True

        if should_update_thumbnail:
            if ini_duration_str is None and ini_duration_str != "None":
                duration = get_duration(item_path)
                if duration:
                    ini_data_to_write["watchmark_duration"] = duration
            else:
                duration = float(ini_duration_str.strip())

            watch_progress_perc = round((current_sec / duration * 100)) if duration > 0 else 0

            # If an override is set, or progress hasn't changed, don't update the thumbnail.
            if ini_override_int != 0 or round(ini_progress_float) == watch_progress_perc:
                if thumbnail_backup_exists:
                    should_update_thumbnail = False
                    print(f"[Thread-{thread_id}] Found thumbnail .bak")
                else:
                    # Update thumbnail if .bak file doesn't exist, to sync with
                    # ini file for watched mode
                    if ini_override_int == 1:
                        mode = AppMode.WATCHED
                        print(f"[Thread-{thread_id}] Sync watched mode")
            else:
                # A sync is required. Queue progress and update mode for thumbnail.
                ini_data_to_write["watchmark_progress"] = watch_progress_perc
                print(f"[Thread-{thread_id}] Video position from INI: {current_sec}s")
                print(f"[Thread-{thread_id}] Video duration: {duration}s")
                print(f"[Thread-{thread_id}] Watch time: {watch_progress_perc}%")

                if watch_progress_perc < MIN_THRESHOLD:
                    mode = AppMode.UNWATCHED
                elif watch_progress_perc > MAX_THRESHOLD:
                    mode = AppMode.WATCHED

            print(f"[Thread-{thread_id}] Updated mode from sync to {mode}")

    # Add/remove KDE tags
    if mode == AppMode.WATCHED:
        add_dolphin_tag(item_path, "watched")
    elif mode == AppMode.UNWATCHED:
        remove_dolphin_tag(item_path, "watched")

    # Write INI file
    if ini_data_to_write:
        write_smplayer_ini_data(item_path, ini_data_to_write)
    else:
        print(f"[Thread-{thread_id}] Skipping INI update")

    # Update thumbnail
    if should_update_thumbnail:
        for path in t_paths:
            update_thumbnail(path, watch_progress_perc, mode)

    else:
        print(f"[Thread-{thread_id}] Skipping thumbnail update")


def create_progress_bar(total):
    """Creates a kdialog progress bar with a cancel button."""
    try:
        # We don't use --persistent here because we want to control 
        # the closing via D-Bus at the very end.
        process = subprocess.Popen(
            ["kdialog", "--progressbar", "Processing Videos...", str(total)],
            stdout=subprocess.PIPE, text=True
        )
        dbus_ref = process.stdout.readline().strip()
        return dbus_ref
    except Exception:
        traceback.print_exc()
        return None


def update_progress_bar(dbus_ref, current_val):
    if not dbus_ref: return

    # Use the lock to ensure only one thread speaks to DBus at a time
    with progress_lock:
        # Split "org.kde.kdialog-xxxx /ProgressDialog" into two parts
        parts = dbus_ref.split(' ')
        service = parts[0]
        path = parts[1] if len(parts) > 1 else "/ProgressDialog"
        
        # Use the specific 'Set' method for the value
        # subprocess.run([QDBUS, service, path, "setValue", str(current_val)], capture_output=True)
        # subprocess.run([QDBUS, service, path, "Set", "", "value", str(current_val)], capture_output=True)

        # We replace the empty "" with the explicit interface name
        subprocess.run([
            QDBUS, service, path, 
            "org.freedesktop.DBus.Properties.Set",           # The method
            "",                                              # The Interface
            "value",                                         # The Property
            str(current_val)                                 # The Value
        ], capture_output=True)


def close_progress_bar(dbus_ref):
    """Instead of just closing, we can update the label and let the user click Close."""
    if not dbus_ref: return
    
    parts = dbus_ref.split(' ')
    service = parts[0]
    path = parts[1] if len(parts) > 1 else "/ProgressDialog"
    
    # Update the label to tell the user we are done
    subprocess.run([QDBUS, service, path, "setLabelText", "Processing Complete!"], capture_output=True)
    
    # We can wait for the user to close it manually OR show a final info box:
    subprocess.run(["kdialog", "--msgbox", "Thumbnail processing is complete."], capture_output=True)
    
    # Finally, close the progress bar
    subprocess.run([QDBUS, service, path, "close"], capture_output=True)


def refresh_kde_cache():
    """Forces KDE to rebuild its configuration and icon caches."""
    # kbuildsycoca6 rebuilds the system configuration cache (Service Menus, Icons, etc.)
    subprocess.run(["kbuildsycoca6"], capture_output=True)
    
    # Additionally, we can notify the system that the icon theme changed
    # This is a bit 'nuclear' but ensures the UI updates
    # subprocess.run([
    #     "qdbus6", "org.kde.KWin", "/KWin", "org.kde.KWin.reconfigure"
    # ], capture_output=True)


def force_dolphin_reload():
    """
    Sends an F5 key press to the active window to force a refresh.
    This is a workaround for Dolphin on Plasma 6 not reliably refreshing
    via D-Bus commands. Requires 'xdotool' to be installed.
    """
    try:
        if subprocess.run(["which", "xdotool"], capture_output=True).returncode != 0:
            print("WARNING: 'xdotool' not found. Cannot refresh Dolphin.")
            subprocess.run(
                ["kdialog", "--error", "Cannot refresh Dolphin. Please install 'xdotool'."],
                capture_output=True
            )
            return

        # Brief pause to allow focus to return to Dolphin after the script's
        # progress dialog closes.
        time.sleep(0.2)
        subprocess.run(["xdotool", "key", "F5"], capture_output=True)
        print("Sent F5 to refresh Dolphin.")

        refresh_kde_cache()

    except Exception:
        traceback.print_exc()
        print("Failed to send F5 key press. View may not be updated.")


def wait_for_dbus_object(dbus_ref, timeout=3.0):
    """Polls until the kdialog D-Bus service responds to a Ping."""
    if not dbus_ref: return False
    
    service = dbus_ref.split(' ')[0]
    start_time = time.time()
    
    while time.time() - start_time < timeout:
        # We try to Ping the service directly.
        # This confirms not just that the name exists, but that it's responding.
        check = subprocess.run(
            [QDBUS, service, "/", "org.freedesktop.DBus.Peer.Ping"],
            capture_output=True
        )
        if check.returncode == 0:
            return True
        time.sleep(0.05)
        
    print(f"DEBUG: Timeout waiting for {service}")
    return False


def mark_folder_watched(folder_path, icon_path, mode):
    """
    Converts a system SVG icon to PNG, applies a checkmark, 
    and configures the folder to show only that icon.
    """
    print(f"DEBUG: Marking folder: {folder_path}")

    if mode == AppMode.UNWATCHED:
        print("Setting folder to unwatched")
        update_thumbnail(folder_path, 0, AppMode.UNWATCHED, True)
        return

    try:
        # Define paths
        dest_icon_name = ".folder_watched.png"  # Hidden file
        dest_icon_path = os.path.join(folder_path, dest_icon_name)

        # Check if rsvg-convert is actually available in the environment
        if not shutil.which("rsvg-convert"):
            print("ERROR: 'rsvg-convert' not found. Folder icon cannot be generated.")
            return
        
        # Rasterize SVG to PNG using rsvg-convert
        # -w 256 ensures a high-quality thumbnail size
        if os.path.exists(icon_path):
            result = subprocess.run(
                ["rsvg-convert", "-w", "256", "-f", "png", "-o", dest_icon_path, icon_path],
                capture_output=True, text=True
            )
            
            if result.returncode != 0:
                print(f"RSVG Error: {result.stderr}")
                return
        else:
            print(f"Error: Icon not found at {icon_path}")
            return

        # Use existing Pillow function to draw the 'Watched' checkmark
        # We pass 100 to trigger the MAX_THRESHOLD (green check)
        if os.path.exists(dest_icon_path):
            update_thumbnail(dest_icon_path, 100, AppMode.WATCHED, True)
            
            # Create the .directory file
            dot_dir_path = os.path.join(folder_path, ".directory")
            
            # Use the ABSOLUTE path for the Icon key to ensure Dolphin finds it
            content = (
                "[Desktop Entry]\n"
                f"Icon={dest_icon_path}\n\n"
                "[ViewProperties]\n"
                "ShowPreview=false\n"
            )
            
            with open(dot_dir_path, "w") as f:
                f.write(content)
                
            # Refresh
            os.utime(folder_path, None)

            # Tell all file managers that this specific directory changed
            subprocess.run([
                QDBUS, "org.freedesktop.FileManager1", "/org/freedesktop/FileManager1",
                "org.freedesktop.FileManager1.PropertiesChanged", folder_path
            ], capture_output=True)

            print(f"Successfully marked folder: {folder_path}")

    except Exception:
        traceback.print_exc()


# def show_notification(count):
#     """Displays a KDE system notification when processing is done."""
#     msg = f"WatchMark: Processed {count} items successfully."
#     subprocess.run(["notify-send", "-i", "task-complete", "WatchMark", msg])


# def force_generate_base_thumbnail(video_path, thumb_name):
#     """Generates a standard KDE thumbnail if one doesn't exist."""
#     try:
#         # Standard KDE locations
#         sizes = {"normal": 128, "large": 256}
#         for size_name, px in sizes.items():
#             dest_dir = os.path.join(THUMB_BASE, size_name)
#             os.makedirs(dest_dir, exist_ok=True)
#             dest_path = os.path.join(dest_dir, thumb_name)
            
#             if not os.path.exists(dest_path):
#                 # Use ffmpegthumbnailer to create the base image
#                 subprocess.run([
#                     "ffmpegthumbnailer", "-i", video_path, 
#                     "-o", dest_path, "-s", str(px)
#                 ], capture_output=True)
                
#                 # NOTE: You will need to add KDE metadata (Thumb::URI, etc.) 
#                 # here so Dolphin doesn't discard them immediately.
#     except Exception:
#         traceback.print_exc()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('paths', nargs='+', help="File or folder paths")
    parser.add_argument('--mark-watched', action='store_true')
    parser.add_argument('--mark-unwatched', action='store_true')
    parser.add_argument('--sync', action='store_true')
    parser.add_argument('--backup-ini', action='store_true')
    args = parser.parse_args()

    mode = AppMode.SYNC
    all_files = []

    print(f"Starting {APP_NAME} version {APP_VERSION}")

    if args.mark_watched: mode = AppMode.WATCHED
    if args.backup_ini: mode = AppMode.BACKUP

    if mode == AppMode.BACKUP:
        global GLOBAL_BACKUP_PATH
        GLOBAL_BACKUP_PATH = get_backup_directory()
        if not GLOBAL_BACKUP_PATH:
            print("Backup path selection cancelled.")
            return

    if args.mark_unwatched:
        # Confirmation Dialog for Unwatched
        try:
            confirm = subprocess.run(
                ["kdialog", "--title", APP_NAME, "--warningyesno",
                 "Are you sure you want to mark these items as unwatched?\nThis will reset your watch progress."],
                capture_output=True
            )
            if confirm.returncode != 0:
                print("Operation cancelled by user.")
                return
        except FileNotFoundError:
            print("kdialog not found, proceeding without confirmation.")
        except Exception:
            pass  # Fallback if kdialog fails for other reasons
        mode = AppMode.UNWATCHED

    # Handle the single folder case separately as it doesn't need parallel processing
    if mode != AppMode.BACKUP and len(args.paths) == 1 and os.path.isdir(args.paths[0]):
        mark_folder_watched(args.paths[0], "/usr/share/icons/breeze/places/64/folder.svg", mode)
        force_dolphin_reload()
        return  # Exit after handling the single folder

    # Pre-calculate list of all video files for accurate progress bar
    for path in args.paths:
        if os.path.isdir(path):
            for root, _, files in os.walk(path):
                for f in files:
                    if f.lower().endswith(VIDEO_EXTS):
                        all_files.append(os.path.join(root, f))
        else:
            if path.lower().endswith(VIDEO_EXTS):
                all_files.append(path)

    if not all_files:
        print("No video files found to process.")
        return

    dbus_ref = create_progress_bar(len(all_files))

    print(f"DEBUG: Number of total files: {len(all_files)}")
    print(f"DEBUG: dbus_ref: {dbus_ref}")

    # Give the DBus service a brief moment to become ready
    # Instead of time.sleep(0.1), use the poll function:
    if not wait_for_dbus_object(dbus_ref):
        print("DEBUG: Continuing anyway, DBus might be slow.")

    # Benchmarking: Start the timer
    start_time = time.perf_counter()
    
    # Choose your executor here for the test:
    # Option A: concurrent.futures.ThreadPoolExecutor()
    # Option B: concurrent.futures.ProcessPoolExecutor()

    # --- Parallel Processing using ThreadPoolExecutor ---
    processed_count = 0
    with concurrent.futures.ThreadPoolExecutor() as executor:
        future_to_path = {executor.submit(process_item, path, mode): path for path in all_files}
        print(f"Processing {len(all_files)} files across multiple threads...")

        for future in concurrent.futures.as_completed(future_to_path):
            path = future_to_path[future]
            
            # Get the ID of the thread that just finished this specific future
            # Note: in a pool, threads are reused, so IDs will repeat
            # Identify the worker
            # threading.get_native_id() for Threads
            # os.getpid() for Processes
            worker_id = threading.get_native_id()
            
            try:
                future.result()  # result() will re-raise any exception from the thread
                print(f"[Thread-{worker_id}] Success: {os.path.basename(path)}")
            except Exception as exc:
                print(f"[Thread-{worker_id}] ERROR on {os.path.basename(path)}: {exc}")
                traceback.print_exc()
            finally:
                # Update progress bar regardless of success or failure
                processed_count += 1
                update_progress_bar(dbus_ref, processed_count)

    # Stop the timer
    end_time = time.perf_counter()
    total_duration = end_time - start_time

    end_time = time.perf_counter()
    print(f"\nFinished in {total_duration:.2f} seconds.")
    
    # print("\n" + "="*40)
    # print(f"BENCHMARK RESULT")
    # print(f"Mode: ThreadPoolExecutor")
    # print(f"Total Files: {len(all_files)}")
    # print(f"Total Time:  {total_duration:.2f} seconds")
    # print(f"Avg Speed:   {total_duration/len(all_files):.4f} seconds/file")
    # print("="*40)

    close_progress_bar(dbus_ref)
    force_dolphin_reload()


if __name__ == "__main__":
    main()
