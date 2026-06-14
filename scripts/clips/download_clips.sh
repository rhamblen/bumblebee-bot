#!/bin/bash

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
UR1_REFERENCES="/mnt/ur1/bumblebee/references"

echo ""
echo "============================================================"
echo " Bumblebee Clip Downloader (WSL/Ubuntu)"
echo " Connect NordVPN on Windows if clips fail with region errors"
echo "============================================================"
echo ""

# yt-dlp
if ! command -v yt-dlp &>/dev/null; then
    echo "Installing yt-dlp..."
    sudo curl -sL https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp -o /usr/local/bin/yt-dlp
    sudo chmod a+rx /usr/local/bin/yt-dlp
fi

# deno (required by yt-dlp for YouTube)
if [ ! -f "$HOME/.deno/bin/deno" ]; then
    echo "Installing deno..."
    sudo apt-get install -y -q unzip
    curl -fsSL https://deno.land/install.sh | DENO_INSTALL="$HOME/.deno" sh -s -- --no-modify-path
fi
export PATH="$HOME/.deno/bin:$PATH"

# ffmpeg
if ! command -v ffmpeg &>/dev/null; then
    echo "Installing ffmpeg..."
    sudo apt-get install -y -q software-properties-common
    sudo add-apt-repository -y universe
    sudo apt-get update -q
    sudo apt-get install -y -q ffmpeg
fi

# Python venv
VENV_DIR="$SCRIPT_DIR/.venv"
if [ ! -d "$VENV_DIR" ]; then
    echo "Setting up Python environment..."
    sudo apt-get install -y -q python3-venv python3-full
    python3 -m venv "$VENV_DIR"
    "$VENV_DIR/bin/pip" install -q python-dotenv requests
fi
PYTHON="$VENV_DIR/bin/python3"

# Mount UR1 share — remount every time to ensure correct write permissions
sudo mkdir -p /mnt/ur1
sudo umount /mnt/ur1 2>/dev/null || true
sudo mount -t cifs //192.168.1.33/media /mnt/ur1 -o "username=nobody,password=,uid=$(id -u),gid=$(id -g),file_mode=0777,dir_mode=0777,noperm,vers=3.0"
echo "UR1 share mounted."

echo ""
echo "Options:"
echo "  1. Dry run (preview only - no downloads)"
echo "  2. Download ALL pending clips (via server - recommended)"
echo "  3. Download clips for ONE person (via server - recommended)"
echo "  4. Download ALL pending clips (via local machine + VPN)"
echo "  5. Exit"
echo ""
read -p "Select option (1-5): " choice

case $choice in
    1)
        "$PYTHON" "$SCRIPT_DIR/process_archetype_csv.py" --dry-run
        ;;
    2)
        "$PYTHON" "$SCRIPT_DIR/process_archetype_csv.py"
        ;;
    3)
        read -p "Enter person name (e.g. Julia Child): " name
        "$PYTHON" "$SCRIPT_DIR/process_archetype_csv.py" --person "$name"
        ;;
    4)
        "$PYTHON" "$SCRIPT_DIR/process_archetype_csv.py" \
            --local \
            --output-dir "$UR1_REFERENCES"
        ;;
    5)
        exit 0
        ;;
    *)
        echo "Invalid option"
        ;;
esac

echo ""
read -p "Press Enter to continue..."
