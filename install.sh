#!/bin/bash

# --- Configuration ---
PYTHON_PATH="" # Replace with "/custom/path/python3" if desired

DESKTOP_FILE="mark_watched.desktop"
PYTHON_SCRIPT="mark_watched.py"
TARGET_DIR="$HOME/.local/share/kio/servicemenus"
CURRENT_DIR=$(pwd)

# 1. Check if CUSTOM path is provided and valid
if [ -n "$PYTHON_PATH" ] && [ -f "$PYTHON_PATH" ]; then
    echo "Using custom Python path: $PYTHON_PATH"

# 2. Else detect venv
elif [ -d "$CURRENT_DIR/venv" ]; then
    PYTHON_PATH="$CURRENT_DIR/venv/bin/python3"
    echo "Using virtual environment: $PYTHON_PATH"

# 3. Fallback to system
else
    PYTHON_PATH=$(which python3)
    echo "Using system python: $PYTHON_PATH"
fi

echo "Installing MarkWatched Service Menu..."
echo "Project Path: $CURRENT_DIR"
echo "Python Path:  $PYTHON_PATH"

# Check if required tools are installed
for tool in xdotool ffprobe qdbus6 rsvg-convert setfattr; do
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