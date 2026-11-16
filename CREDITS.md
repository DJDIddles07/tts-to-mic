# Credits

## Who made this
- **Idea, design, testing and direction:** the repository owner.
- **Code:** written in collaboration with **Claude** (Anthropic), an AI assistant.

## Libraries used (installed by `requirements.txt`)
| Project | Used for | License |
|---|---|---|
| [pyttsx3](https://github.com/nateshmbhat/pyttsx3) | Text to speech (Windows SAPI voices) | MPL-2.0 |
| [comtypes](https://github.com/enthought/comtypes) | Windows COM access for speech | MIT |
| [sounddevice](https://github.com/spatialaudio/python-sounddevice) | Playing audio to the chosen device | MIT |
| [NumPy](https://numpy.org) | Audio data handling | BSD-3-Clause |
| [Pillow](https://python-pillow.org) | Screen capture and image prep for OCR | HPND |
| [pytesseract](https://github.com/madmaze/pytesseract) | Python wrapper for Tesseract | Apache-2.0 |
| [pynput](https://github.com/moses-palmer/pynput) | Global keyboard and mouse keybinds | LGPL-3.0 |
| [pyspellchecker](https://github.com/barrust/pyspellchecker) | Spell check and its English word list | MIT |
| [PyInstaller](https://pyinstaller.org) | Building the .exe (build time only) | GPL-2.0+ with bootloader exception |
| Python, Tkinter / Tcl/Tk | The language and the window toolkit | PSF License / Tcl license |

## Tools you install separately (not included in this repository)
- **[Tesseract OCR](https://github.com/tesseract-ocr/tesseract)**, the OCR engine (Apache-2.0).
  The Windows installer is provided by **[UB Mannheim](https://github.com/UB-Mannheim/tesseract/wiki)**.
- **[VB-CABLE Virtual Audio Device](https://vb-audio.com/Cable/)** by **VB-Audio Software**
  (Vincent Burel). It is donationware; do not redistribute it, and check VB-Audio's terms before any
  commercial use. Please support them if you use it.
- **Microsoft Windows speech voices** (for example "Microsoft David"), part of Windows.

## Notes
- Licenses above are listed as understood at the time of writing. Check each project's own page for the current terms
  before you redistribute anything.
- If you share a built `.exe`, it bundles the libraries above. Their license terms (notably pynput's LGPL and
  PyInstaller's exception) apply to what you distribute.
