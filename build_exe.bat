@echo off
cd /d "%~dp0"
echo.
echo === TTS to Mic: building the exe ===
echo.
where python >nul 2>nul
if errorlevel 1 (
  echo Python was not found. Install it from https://www.python.org/downloads/
  echo and tick "Add python.exe to PATH" in the installer, then run this again.
  pause
  exit /b 1
)
python -m pip install --upgrade pip
python -m pip install -r requirements.txt pyinstaller
if errorlevel 1 (
  echo Installing the packages failed. Check your internet connection and try again.
  pause
  exit /b 1
)
python -m PyInstaller --noconfirm --onefile --windowed --name "TTS to Mic" --hidden-import pyttsx3.drivers.sapi5 --hidden-import pynput.keyboard._win32 --hidden-import pynput.mouse._win32 --hidden-import comtypes --collect-data spellchecker tts_mic.py
if errorlevel 1 (
  echo The build failed. Scroll up to see why.
  pause
  exit /b 1
)
if exist "tesseract\tesseract.exe" (
  echo Copying Tesseract next to the exe...
  xcopy /E /I /Y "tesseract" "dist\tesseract" >nul
) else (
  echo Note: no Tesseract found in the "tesseract" folder, so it was not bundled.
  echo The person using the exe will be asked to install it. See SETUP.txt.
)
echo.
echo Done. Your files are in the "dist" folder:
echo   dist\TTS to Mic.exe
echo   dist\tesseract\   (only if you bundled it)
echo Send the whole dist folder, not just the exe, if Tesseract is bundled.
echo.
pause
