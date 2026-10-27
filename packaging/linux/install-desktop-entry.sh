#!/bin/sh
# Adds Surgical Annotation Studio to the applications menu for this user.
# Run it from the unpacked folder; run it again if you move the folder.
DIR="$(cd "$(dirname "$0")" && pwd)"
APPS="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
mkdir -p "$APPS"
cat > "$APPS/surgical-annotation-studio.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=Surgical Annotation Studio
Comment=Annotate surgical videos
Exec="$DIR/Surgical Annotation Studio"
Path=$DIR
Terminal=false
Categories=Science;Video;
EOF
echo "Added Surgical Annotation Studio to the applications menu."
if ! ldconfig -p 2>/dev/null | grep -q libxcb-cursor.so; then
    echo "Also run once:  sudo apt install libxcb-cursor0"
fi
