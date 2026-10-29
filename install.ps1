# Installs Meeting Copilot on Windows and adds Desktop and Start Menu shortcuts with an icon.
#
#   From a checkout:   powershell -ExecutionPolicy Bypass -File install.ps1
#   One line:          irm https://raw.githubusercontent.com/masrisystems/meeting-copilot/main/install.ps1 | iex
#   Remove shortcuts:  powershell -ExecutionPolicy Bypass -File install.ps1 -Uninstall
#
# Without a checkout, the app goes to $env:MEETING_COPILOT_DIR (default %USERPROFILE%\meeting-copilot).
# Re-running the installer updates it and keeps .env, knowledge_base\, facts.json and sessions\.
param([switch]$Uninstall)
$ErrorActionPreference = "Stop"

$Repo = "https://github.com/masrisystems/meeting-copilot"
$App = "Meeting Copilot"

$Here = $PSScriptRoot
if ($Here -and (Select-String -Quiet -Path (Join-Path $Here "pyproject.toml") -Pattern 'name = "meeting-copilot"' -ErrorAction SilentlyContinue)) {
    $Dir = $Here
} elseif ($env:MEETING_COPILOT_DIR) {
    $Dir = $env:MEETING_COPILOT_DIR
} else {
    $Dir = Join-Path $env:USERPROFILE "meeting-copilot"
}

$Shortcuts = @(
    (Join-Path ([Environment]::GetFolderPath("Desktop")) "$App.lnk"),
    (Join-Path ([Environment]::GetFolderPath("Programs")) "$App.lnk")
)

if ($Uninstall) {
    $Shortcuts | ForEach-Object { Remove-Item $_ -ErrorAction SilentlyContinue }
    Write-Host "Shortcuts removed. The app and your data are still in $Dir (delete the folder to remove them)."
    return
}

function Step($text) { Write-Host "`n==> $text" -ForegroundColor Cyan }

# --- 1. uv (installs Python 3.12 and all dependencies, including the CUDA libraries) ---
$Uv = (Get-Command uv -ErrorAction SilentlyContinue).Source
if (-not $Uv -and (Test-Path "$env:USERPROFILE\.local\bin\uv.exe")) { $Uv = "$env:USERPROFILE\.local\bin\uv.exe" }
if (-not $Uv) {
    Step "Installing uv"
    powershell -ExecutionPolicy Bypass -NoProfile -Command "irm https://astral.sh/uv/install.ps1 | iex"
    $Uv = "$env:USERPROFILE\.local\bin\uv.exe"
}

# --- 2. App files ---
if ($Dir -ne $Here) {
    if (Test-Path (Join-Path $Dir ".git")) {
        Step "Updating $Dir"
        git -C $Dir pull --ff-only
    } elseif (Get-Command git -ErrorAction SilentlyContinue) {
        Step "Downloading to $Dir"
        git clone --depth 1 "$Repo.git" $Dir
    } else {
        Step "Downloading to $Dir"
        $Zip = Join-Path $env:TEMP "meeting-copilot.zip"
        $Unpacked = Join-Path $env:TEMP "meeting-copilot-main"
        Invoke-WebRequest "$Repo/archive/refs/heads/main.zip" -OutFile $Zip
        Remove-Item $Unpacked -Recurse -Force -ErrorAction SilentlyContinue
        Expand-Archive $Zip -DestinationPath $env:TEMP -Force
        New-Item -ItemType Directory -Force $Dir | Out-Null
        Copy-Item "$Unpacked\*" $Dir -Recurse -Force
        Remove-Item $Zip, $Unpacked -Recurse -Force
    }
    if ($LASTEXITCODE) { throw "Download failed" }
}
Set-Location $Dir

Step "Installing Python and dependencies"
& $Uv sync --locked
if ($LASTEXITCODE) { throw "uv sync failed" }
if (-not (Test-Path ".env")) { Copy-Item ".env.example" ".env" }

# --- 3. Models, so the first start doesn't wait for downloads ---
New-Item -ItemType Directory -Force "knowledge_base" | Out-Null
Step "Downloading the embedding model for your notes"
& $Uv run python -m copilot.rag download
Step "Downloading the Whisper model (about 1.6 GB with an NVIDIA GPU, less without)"
& $Uv run python -c "from copilot.stt import Transcriber; Transcriber()"
if ($LASTEXITCODE) { Write-Host "Could not load Whisper now; it is downloaded on the first start instead." }

# --- 4. Shortcuts (pythonw: no console window) ---
Step "Creating shortcuts"
$Shell = New-Object -ComObject WScript.Shell
foreach ($Path in $Shortcuts) {
    $Link = $Shell.CreateShortcut($Path)
    $Link.TargetPath = Join-Path $Dir ".venv\Scripts\pythonw.exe"
    $Link.Arguments = "-m copilot --exit-on-close --log `"$Dir\sessions\copilot.log`""
    $Link.WorkingDirectory = $Dir
    $Link.IconLocation = Join-Path $Dir "packaging\icon.ico"
    $Link.Description = "Live transcript and reply suggestions for your calls"
    $Link.Save()
}
Write-Host "Open $App from your Desktop or the Start Menu."

# --- 5. Hints ---
if (-not (Get-Command ollama -ErrorAction SilentlyContinue)) {
    Write-Host "`nTip: install Ollama (https://ollama.com) for the offline fallback and pull a model, e.g.`n'ollama pull granite4:7b-a1b-h'. Any other installed model can be picked in the window."
}
Write-Host "`nDone. Put your notes as Markdown files in $Dir\knowledge_base,`nand AI keys and settings in $Dir\.env (both open from buttons in the window)."
