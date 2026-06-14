# Build Xray_labs with PyInstaller (Windows)
# Usage: right-click -> Run with PowerShell, or .\build.ps1

$ErrorActionPreference = "Stop"

Write-Host "=== X-Ray-lab PyInstaller build ===" -ForegroundColor Cyan

# Ensure deps
python -m pip install -r requirements.txt --quiet

$dist = "dist"
if (Test-Path $dist) { Remove-Item -Recurse -Force $dist }

$icon = "icon_cat.ico"
if (-not (Test-Path $icon)) {
    Write-Host "Warning: no icon_cat.ico, building without custom icon" -ForegroundColor Yellow
    $iconArg = ""
} else {
    $iconArg = "--icon=`"$icon`""
}

Write-Host "Running PyInstaller (onedir + windowed + noupx)..." -ForegroundColor Green

$cmd = "pyinstaller --clean --noconfirm --name Xray_labs --onedir --windowed --noupx --version-file=version_info.txt $iconArg main.py"
Write-Host $cmd
Invoke-Expression $cmd

if (Test-Path "dist/Xray_labs/Xray_labs.exe") {
    Write-Host "`n=== BUILD SUCCESS ===" -ForegroundColor Green
    Write-Host "Portable folder: dist/Xray_labs/" -ForegroundColor Green

    # Add useful files
    Copy-Item "README.md" "dist/Xray_labs/README.txt" -Force -ErrorAction SilentlyContinue

    @"
X-Ray-lab - Portable version (v1.0.0)

1. Запусти Xray_labs.exe или Xray_labs.bat
2. Ничего устанавливать не нужно.

Антивирус (Windows Defender и др.) часто ругается на PyInstaller-приложения — это ложное срабатывание.

Что делать:
- Нажми "Подробнее" → "Выполнить в любом случае"
- Добавь папку с Xray_labs в исключения Windows Defender (рекомендуется)

Подробности — в README.txt
"@ | Out-File -Encoding UTF8 "dist/Xray_labs/HOW_TO_RUN.txt"

    # Convenient launcher
    @"
@echo off
cd /d "%~dp0"
start "" Xray_labs.exe
"@ | Out-File -Encoding ASCII "dist/Xray_labs/Xray_labs.bat"

    $version = "1.0.0"
    $zipName = "Xray_labs-v$version-portable.zip"
    $zipPath = "releases\$zipName"

    if (-not (Test-Path "releases")) { New-Item -ItemType Directory -Path "releases" | Out-Null }

    if (Test-Path $zipPath) { Remove-Item $zipPath -Force }

    Compress-Archive -Path "dist/Xray_labs" -DestinationPath $zipPath -Force

    $stableZip = "releases\Xray_labs-portable.zip"
    Copy-Item $zipPath $stableZip -Force

    Write-Host "`n=== PACKAGE CREATED ===" -ForegroundColor Cyan
    Write-Host "Versioned zip (for GitHub Release): $zipPath" -ForegroundColor Green
    Write-Host "Stable zip (for auto-updater): $stableZip" -ForegroundColor Green
    Write-Host ""
    Write-Host "IMPORTANT:" -ForegroundColor Yellow
    Write-Host "1. The releases/ folder is LOCAL only (it is in .gitignore)." -ForegroundColor Yellow
    Write-Host "2. Upload both zips to the GitHub Release tagged v$version." -ForegroundColor Yellow
    Write-Host "3. The in-app updater looks for assets containing 'portable' + .zip" -ForegroundColor Yellow
} else {
    Write-Host "Build may have failed. Check above output." -ForegroundColor Red
}
