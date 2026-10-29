#!/bin/sh
# Installs Meeting Copilot on Linux or macOS and adds an app launcher with an icon
# (app menu + desktop on Linux, ~/Applications + desktop on macOS).
#
#   From a checkout:   ./install.sh
#   One line:          curl -LsSf https://raw.githubusercontent.com/masrisystems/meeting-copilot/main/install.sh | sh
#   Remove launchers:  ./install.sh --uninstall   (keeps the app folder with your .env and notes)
#
# Without a checkout, the app goes to $MEETING_COPILOT_DIR (default ~/meeting-copilot).
# Re-running the installer updates it and keeps .env, knowledge_base/, facts.json and sessions/.
set -eu

REPO=https://github.com/masrisystems/meeting-copilot
APP="Meeting Copilot"
OS=$(uname -s)

here=$(cd "$(dirname "$0")" 2>/dev/null && pwd || true)
if [ -n "$here" ] && grep -qs 'name = "meeting-copilot"' "$here/pyproject.toml"; then
  DIR=$here
else
  DIR=${MEETING_COPILOT_DIR:-$HOME/meeting-copilot}
fi

desktop_dir() {
  d=$(xdg-user-dir DESKTOP 2>/dev/null || true)
  [ -n "$d" ] && [ "$d" != "$HOME" ] || d=$HOME/Desktop
  echo "$d"
}
LINUX_MENU=${XDG_DATA_HOME:-$HOME/.local/share}/applications/meeting-copilot.desktop
LINUX_DESKTOP=$(desktop_dir)/meeting-copilot.desktop
MAC_APP=$HOME/Applications/$APP.app
MAC_DESKTOP=$HOME/Desktop/$APP

if [ "${1:-}" = "--uninstall" ]; then
  rm -rf "$LINUX_MENU" "$LINUX_DESKTOP" "$MAC_APP" "$MAC_DESKTOP"
  echo "Launchers removed. The app and your data are still in $DIR (delete the folder to remove them)."
  exit 0
fi

step() { printf '\n==> %s\n' "$1"; }

# --- 1. uv (installs Python 3.12 and all dependencies) ---
UV=$(command -v uv || true)
[ -n "$UV" ] || [ ! -x "$HOME/.local/bin/uv" ] || UV=$HOME/.local/bin/uv
if [ -z "$UV" ]; then
  step "Installing uv"
  curl -LsSf https://astral.sh/uv/install.sh | sh
  UV=$HOME/.local/bin/uv
fi

# --- 2. App files ---
if [ "$DIR" != "$here" ]; then
  if [ -d "$DIR/.git" ]; then
    step "Updating $DIR"
    git -C "$DIR" pull --ff-only
  elif command -v git >/dev/null; then
    step "Downloading to $DIR"
    git clone --depth 1 "$REPO.git" "$DIR"
  else
    step "Downloading to $DIR"
    mkdir -p "$DIR"
    curl -LsSf "$REPO/archive/refs/heads/main.tar.gz" | tar xz --strip-components=1 -C "$DIR"
  fi
fi
cd "$DIR"

step "Installing Python and dependencies"
"$UV" sync --locked
[ -f .env ] || cp .env.example .env

# --- 3. Models, so the first start doesn't wait for downloads ---
mkdir -p knowledge_base
step "Downloading the embedding model for your notes"
"$UV" run python -m copilot.rag download
step "Downloading the Whisper model (about 1.6 GB with an NVIDIA GPU, less without)"
"$UV" run python -c "from copilot.stt import Transcriber; Transcriber()" \
  || echo "Could not load Whisper now; it is downloaded on the first start instead."

# --- 4. Launcher ---
PY=$DIR/.venv/bin/python
ARGS="-m copilot --exit-on-close --log \"$DIR/sessions/copilot.log\""
if [ "$OS" = Darwin ]; then
  step "Creating $MAC_APP"
  rm -rf "$MAC_APP"
  mkdir -p "$MAC_APP/Contents/MacOS" "$MAC_APP/Contents/Resources"
  cat > "$MAC_APP/Contents/MacOS/meeting-copilot" <<EOF
#!/bin/sh
cd "$DIR" && exec "$PY" $ARGS
EOF
  chmod +x "$MAC_APP/Contents/MacOS/meeting-copilot"
  cat > "$MAC_APP/Contents/Info.plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>CFBundleName</key><string>$APP</string>
  <key>CFBundleIdentifier</key><string>com.masrisystems.meeting-copilot</string>
  <key>CFBundleExecutable</key><string>meeting-copilot</string>
  <key>CFBundleIconFile</key><string>icon</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>LSUIElement</key><true/>
  <key>NSMicrophoneUsageDescription</key><string>Meeting Copilot transcribes your microphone locally.</string>
</dict></plist>
EOF
  iconset=$(mktemp -d)/icon.iconset
  mkdir -p "$iconset"
  for s in 16 32 128 256 512; do
    sips -z $s $s packaging/icon.png --out "$iconset/icon_${s}x${s}.png" >/dev/null
    sips -z $((s * 2)) $((s * 2)) packaging/icon.png --out "$iconset/icon_${s}x${s}@2x.png" >/dev/null
  done
  iconutil -c icns "$iconset" -o "$MAC_APP/Contents/Resources/icon.icns" || true
  ln -sfn "$MAC_APP" "$MAC_DESKTOP"
  echo "Open $APP from your Desktop, Launchpad or Spotlight."
else
  step "Creating the app launcher"
  mkdir -p "$(dirname "$LINUX_MENU")"
  cat > "$LINUX_MENU" <<EOF
[Desktop Entry]
Type=Application
Name=$APP
Comment=Live transcript and reply suggestions for your calls
Exec="$PY" $ARGS
Path=$DIR
Icon=$DIR/packaging/icon.png
Terminal=false
Categories=Office;
StartupWMClass=MeetingCopilot
EOF
  chmod +x "$LINUX_MENU"
  if [ -d "$(dirname "$LINUX_DESKTOP")" ]; then
    cp "$LINUX_MENU" "$LINUX_DESKTOP"
    chmod +x "$LINUX_DESKTOP"
    gio set "$LINUX_DESKTOP" metadata::trusted true 2>/dev/null || true  # GNOME: allow launching
  fi
  command -v update-desktop-database >/dev/null && update-desktop-database "$(dirname "$LINUX_MENU")" || true
  echo "Open $APP from your app menu or desktop."
fi

# --- 5. Hints ---
command -v ollama >/dev/null || printf '\nTip: install Ollama (https://ollama.com) for the offline fallback and pull a model, e.g.\n"ollama pull granite4:7b-a1b-h". Any other installed model can be picked in the window.\n'
printf '\nDone. Put your notes as Markdown files in %s/knowledge_base,\nand AI keys and settings in %s/.env (both open from buttons in the window).\n' "$DIR" "$DIR"
