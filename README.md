# HFW Mod Manager
Simplifies managing and packing mods for Horizon Forbidden West. It provides an intuitive GUI.

Drag & drop mod folders or ZIPs, select variations, and pack activated mods into the game’s package stream.

<img width="1194" height="841" alt="image" src="https://github.com/user-attachments/assets/a0eae584-cd1b-4d0b-b9df-0067da7bf7f8" />


For better documentation and usage, check out this doc
https://hfw-mm.gitbook.io/hfw-mm-docs/

## Requirements (If running from source)

- Python 3.8+ (tested on 3.12)
- PyQt5
- pillow
- pyqtdarktheme
- markdown
- PyQtWebEngine

### Development (from source)

If you wish to contribute or run from source:


1. Clone this repository:

   ```bash
   git clone https://github.com/Julz876/HFW_Mod_Manager.git
   cd hfw_mod_manager
   ```
   
2. Set up a virtual environment (recommended)
   ```bash
   python -m venv venv
   ```
   
   Then activate it
   
   ```bash
   venv\Scripts\activate
   ```
   
3. Install dependencies:

   ```bash
   pip install -r requirements.txt
   ```

3. Place `Decima_pack.exe` in the same folder as `hfw_mm.py`, or adjust the `pack_dir` variable in `ModManager.pack_mods_worker()`.
   
4. ```bash
   python hfw_mm.py
   ```


## Contributing

1. Fork the repo
2. Create a new branch (`git checkout -b feature/new-feature`)
3. Commit your changes (`git commit -am 'Add new feature'`)
4. Push to the branch (`git push origin feature/new-feature`)
5. Open a pull request


## Credits

Special thanks to **id-daemon** for developing the packing tool.


## License

This project is licensed under the GNU GPL v3.0 License.

