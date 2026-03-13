#!/bin/bash

# --- Configuration ---
PYTHON_PATH="/mnt/D_TOSHIBA_S300/python/bin/python3" # replace path

DESKTOP_FILE="mark_watched.desktop"
PYTHON_SCRIPT="mark_watched.py"
TARGET_DIR="$HOME/.local/share/kio/servicemenus"
CURRENT_DIR=$(pwd)

# Detect Python path (Checks for venv in current dir, then falls back)
# if [ -d "$CURRENT_DIR/venv" ]; then
#     PYTHON_PATH="$CURRENT_DIR/venv/bin/python3"
#     echo "Using virtual environment: $PYTHON_PATH"
# else
#     PYTHON_PATH=$(which python3)
#     echo "Using system python: $PYTHON_PATH"
# fi

echo "Installing MarkWatched Service Menu..."
echo "Project Path: $CURRENT_DIR"
echo "Python Path:  $PYTHON_PATH"

# Check if required tools are installed
for tool in xdotool ffprobe qdbus6 rsvg-convert; do
    if ! command -v $tool &> /dev/null; then
        echo "Warning: $tool is not installed. Some features may fail."
    fi
done

# Ensure target directory exists
mkdir -p "$TARGET_DIR"

# Create a temporary copy and update paths
# Using | as a delimiter in sed because paths contain /
cp "$DESKTOP_FILE" "$TARGET_DIR/$DESKTOP_FILE"

sed -i "s|Exec=.*$PYTHON_SCRIPT|Exec=$PYTHON_PATH $CURRENT_DIR/$PYTHON_SCRIPT|g" "$TARGET_DIR/$DESKTOP_FILE"
chmod +x "$TARGET_DIR/$DESKTOP_FILE"

echo "Success! Script installed to $TARGET_DIR/$DESKTOP_FILE"
echo "You may need to restart Dolphin (killall dolphin && dolphin &) to see changes."