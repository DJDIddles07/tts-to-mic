# TTS to Mic

Type a message and it is spoken into a virtual microphone, so Discord or a game hears it.
It can also read new chat messages off your screen and speak them. Windows only.

#AI
I suck at coding still and got stuck a lot so AI did help me out but not all of it is AI I'm happy it works and am proud as its helping me right now 
maybe it can help someone else too and one day I'll be good enough at coding to not need AI.

## Features
- Speak typed messages into any output device (use a virtual cable so other apps hear it as a mic)
- Quick type window: a small always-on-top box you pop up over a game with a keybind
- Screen area reader: draw a box over a chat and new messages are read out (OCR), with a chat delay
  and filters so old messages are never read twice
- Global keybinds for keyboard keys and mouse buttons
- Spell check and replacement rules (for example `wasnt=wasn't`, `fr=for real`) with your own dictionary
- Dark and light mode, built-in Help window

## Quick start
Full step-by-step instructions are in [SETUP.txt](SETUP.txt). In short:

1. Install Python 3.10+ (tick "Add python.exe to PATH").
2. Run `run_from_source.bat` to try it, or `build_exe.bat` to build `dist\TTS to Mic.exe`.
3. Install [Tesseract OCR](https://github.com/UB-Mannheim/tesseract/wiki) (needed for the screen reader).
4. Install [VB-Cable](https://vb-audio.com/Cable/) (optional, the virtual microphone) and restart.

The app checks for Tesseract and VB-Cable on start and shows download links if they are missing.

## Credits
See [CREDITS.md](CREDITS.md) for the people, projects and tools this is built on.

## License
No license has been chosen yet, so by default all rights are reserved by the author.
Add a `LICENSE` file if you want others to be able to use or change it.
"# tts-to-mic" 
