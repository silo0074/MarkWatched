# You need to create a .desktop file in
# ~/.local/share/kio/servicemenu/ (or /usr/share/kio/servicemenus/).

# You will need to install the Python Imaging Library:
# For openSUSE: sudo zypper install python3-Pillow ffmpeg xdotool
# For Debian/Ubuntu: sudo apt install python3-pil ffmpeg xdotool


# qdbus6 | grep dolphin
#  org.kde.dolphin-342959
# qdbus6 org.kde.dolphin-342959 /dolphin/Dolphin_1

import os
import struct
import hashlib
import argparse
import subprocess
import time
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


def get_qdbus_cmd():
    """Detects the available qdbus command on the system."""
    for cmd in ["qdbus-qt6", "qdbus6", "qdbus"]:
        if subprocess.run(["which", cmd], capture_output=True).returncode == 0:
            return cmd
    return "qdbus" # Fallback

QDBUS = get_qdbus_cmd()
print(QDBUS)


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
    try:
        h = get_smplayer_hash(filename)
        if not h:
            return {}  # Return empty dict if hash fails

        ini_path = os.path.join(INI_BASE_PATH, h[0], f"{h}.ini")
        print("SMPlayer INI path: " + ini_path)

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

        print(f"INI data: {data}")
        return data
    except Exception:
        traceback.print_exc()
        return {}


def write_smplayer_ini_data(filename, data_to_write):
    """Writes multiple key-value pairs to the SMPlayer INI file, replacing if they exist."""
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

        print(f"Wrote {data_to_write} to {ini_path}")
    except Exception:
        traceback.print_exc()


def get_kde_thumbnail_path(video_path):
    """Finds the existing KDE thumbnail by matching KDE's URI encoding style."""
    """Returns a list of all existing thumbnail size variants for the file."""
    try:
        abs_path = os.path.abspath(video_path)
        
        # We manually build the URI to control percent-encoding.
        # Freedesktop/KDE typically does NOT encode: / _ - . ~ [ ]
        # It DOES encode: # % and non-ascii (like the lightning bolt)
        
        path_encoded = urllib.parse.quote(abs_path, safe="!;:()&$/_-.,~*+=@")
        uri = f"file://{path_encoded}"
        
        # MD5 hash of the URI
        thumb_name = hashlib.md5(uri.encode('utf-8')).hexdigest() + ".png"

        # print(f"DEBUG: Processing {os.path.basename(video_path)}")
        # print(f"DEBUG: URI: {uri}")
        # print(f"DEBUG: Calculated Hash: {thumb_name}")

        # Check all standard thumbnail locations
        found_paths = []
        for size in ['large', 'normal', 'x-large', 'xx-large']:
            full_path = os.path.join(THUMB_BASE, size, thumb_name)
            if os.path.exists(full_path):
                print(f"DEBUG: Found thumbnail at: {full_path}")
                found_paths.append(full_path)
        
        return found_paths
    except Exception:
        traceback.print_exc()
        return []


def update_thumbnail(thumb_path, percentage, mode):
    """Handles backup, restoration, and drawing."""
    """Adaptive drawing for large vs small (Details/Compact) thumbnails."""
    try:
        bak_path = thumb_path + ".bak"

        # Manage the Backup
        if not os.path.exists(bak_path):
            # Create backup from original if it doesn't exist
            os.replace(thumb_path, bak_path)
        
        if mode == "unwatched":
            # Restore original and remove backup
            os.replace(bak_path, thumb_path)
            print("Mode is 'unwatched'")
            return

        with Image.open(bak_path) as img:
            # This captures the Thumb::MTime, Thumb::URI, etc.
            metadata = img.info

            img = img.convert("RGBA")
            # Alpha Compositing: The script now uses a separate overlay layer 
            # to ensure transparency and anti-aliasing look smooth.
            overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
            draw = ImageDraw.Draw(overlay)
            w, h = img.size
            
            # Identify if we are dealing with small thumbnails (Compact/Details view)
            is_small = h <= 128

            if mode == "watched":
                if is_small:
                    # Centered checkmark taking entire height
                    center = (w // 2, h // 2)
                    size = h // 2
                    draw.ellipse([center[0]-size, center[1]-size, center[0]+size, center[1]+size], fill=(0, 0, 0, 150))
                    draw.line([(center[0]-size*0.5, center[1]), 
                               (center[0]-size*0.1, center[1]+size*0.4), 
                               (center[0]+size*0.5, center[1]-size*0.4)], 
                              fill=(50, 255, 50, 255), width=max(2, h // 10))
                else:
                    # Corner checkmark for large views
                    circle_r = int(min(w, h) * 0.15)
                    margin_bottom = int(h * 0.05)
                    margin_right = int(w * 0.10)
                    center = (w - circle_r - margin_right, h - circle_r - margin_bottom)
                    draw.ellipse([center[0]-circle_r, center[1]-circle_r, center[0]+circle_r, center[1]+circle_r], fill=(0, 0, 0, 180))
                    draw.line([(center[0]-circle_r*0.5, center[1]), (center[0]-circle_r*0.1, center[1]+circle_r*0.4), (center[0]+circle_r*0.5, center[1]-circle_r*0.4)], fill=(50, 255, 50, 255), width=max(2, int(circle_r*0.2)))

            elif mode == "sync":
                # Progress Bar Height Logic
                bar_height = h // 3 if is_small else max(4, int(h * 0.08))
                
                # Background
                draw.rectangle([0, h - bar_height, w, h], fill=(0, 0, 0, 160))
                # Progress
                bar_width = int(w * (percentage / 100))
                draw.rectangle([0, h - bar_height, bar_width, h], fill=(0, 255, 0, 255))
            
            # Save with Original Metadata
            # This is the critical step to stop Dolphin from deleting it
            combined = Image.alpha_composite(img, overlay)
            pnginfo = PngImagePlugin.PngInfo()
            for k, v in metadata.items():
                if isinstance(v, (str, bytes)):
                    pnginfo.add_text(k, str(v))
            
            combined.convert("RGB").save(thumb_path, "PNG", pnginfo=pnginfo)
            
    except Exception:
        traceback.print_exc()


def process_item(item_path, mode):
    t_paths = get_kde_thumbnail_path(item_path)

    print(f"Item path: {item_path}")
    print(f"Thumbnail path: {t_paths}")

    if not t_paths:
        print("Thumbnail not found")
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

    print(f"Received mode: {mode}")

    if mode == "watched":
        # Set the manual override to 1 to prevent sync from changing it, 
        # set watch progress to 100 to save the 'watched' status.
        # Override bit and 100 percent watch progress means watched.
        # Write INI only if data differs.
        watch_progress_perc = 100
        if ini_progress_float != 100 or ini_override_int != 1:
            ini_data_to_write["watchmark_override"] = 1
            ini_data_to_write["watchmark_progress"] = watch_progress_perc
            should_update_thumbnail = True

    elif mode == "unwatched":
        # Remove manual override and set progress to 0
        if ini_progress_float != 0 or ini_override_int != 0:
            ini_data_to_write["watchmark_override"] = 0
            ini_data_to_write["watchmark_progress"] = watch_progress_perc
        should_update_thumbnail = True

    elif mode == "sync":
        duration = 0
        current_sec = 0

        if ini_current_sec_str is None:
            print("No progress time found in INI")
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
                else:
                    # Update thumbnail if backup doesn't exist to sync with
                    # ini file for watched mode
                    ini_data_to_write["watchmark_progress"] = ini_progress_str
            else:
                # A sync is required. Queue progress and update mode for thumbnail.
                ini_data_to_write["watchmark_progress"] = watch_progress_perc
                print(f"Video position from INI: {current_sec}s")
                print(f"Video duration: {duration}s")
                print(f"Watch time: {watch_progress_perc}%")

                if watch_progress_perc < MIN_THRESHOLD:
                    mode = "unwatched"
                elif watch_progress_perc > MAX_THRESHOLD:
                    mode = "watched"
                print("Updated mode: " + mode)

    if ini_data_to_write:
        write_smplayer_ini_data(item_path, ini_data_to_write)
    else:
        print("Skipping INI update")

    if should_update_thumbnail:
        for path in t_paths:
            update_thumbnail(path, watch_progress_perc, mode)
    else:
        print("Skipping thumbnail update")


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
    args = parser.parse_args()

    mode = "sync"
    processed_count = 0
    if args.mark_watched: mode = "watched"
    if args.mark_unwatched: mode = "unwatched"

    # Pre-calculate count for accurate progress bar
    all_files = []
    for path in args.paths:
        if os.path.isdir(path):
            for root, _, files in os.walk(path):
                for f in files:
                    if f.lower().endswith(VIDEO_EXTS):
                        all_files.append(os.path.join(root, f))
        else:
            all_files.append(path)

    if not all_files:
        return

    dbus_ref = create_progress_bar(len(all_files))
    print(f"DEBUG: Number of total files: {len(all_files)}")
    print(f"DEBUG: dbus_ref: {dbus_ref}")

    # Give the DBus service a brief moment to become ready
    # Instead of time.sleep(0.1), use the poll function:
    if wait_for_dbus_object(dbus_ref):
        print("DEBUG: DBus service is ready.")
    else:
        print("DEBUG: Continuing anyway, DBus might be slow.")
    
    for i, file_path in enumerate(all_files):
        print("----------------------------------")
        print(f"DEBUG: File: {i+1}/{len(all_files)}")
        process_item(file_path, mode)
        update_progress_bar(dbus_ref, i + 1)

    close_progress_bar(dbus_ref)
    force_dolphin_reload()


if __name__ == "__main__":
    main()
