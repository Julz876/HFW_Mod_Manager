# HFW Mod Manager
Simplifies managing and packing mods for Horizon Forbidden West. It provides an intuitive GUI.

Drag & drop mod folders or ZIPs, select variations, and pack activated mods into the game’s package stream.


## Features

- **Drag & Drop Mods**: Drop directories or ZIP archives onto the drag and drop area.
- **Variation Selection**: If a mod folder/ZIP contains multiple `variation.png` subfolders, a dialog prompts for which variation to install.
- **Shared Files**: A `shared_files` folder (if present) is always included and non-toggleable.
- **Stream/Core Detection**: Automatically copies any `_texture.stream`, `_mesh.stream`, or `.core` files from the root of the mod folder or archive.
- **Exclusive Selection**: Only one variation per mod can be checked at a time; `shared_files` remains always checked.
- **Pack Mods**: Restores original game files, clears the `package/ar` directory, gathers selected streams/cores, runs the Decima packer, and injects the new stream.
- **Conflict Highlighting**: Flags files present in multiple selected variations to help avoid conflicts.
- **Configuration Persistence**: Remembers your game installation path in `decima.ini`.

---

## Mod Folder Structure

```
mods/                         # Your mod repository root
├── ModA/                     # Each mod in its folder
│   ├── shared_files/         # Always-included assets
│   ├── variant1/             # Contains `variation.png`
│   │   └── variation.png
│   └── variant2/
│       └── variation.png
└── ModB.zip                  # ZIP archive (extracted on drop)
```

For ZIP archives, the tool extracts them temporarily on drop and applies the same structure and logic as uncompressed folders.

---

## Requirements

- Python 3.8+ (tested on 3.12)
- PyQt5

---

## Installation

### User Download (.zip)

1. Download the latest `HFW_Mod_Manager.zip` archive from the [Releases](https://github.com/Julz876/HFW_Mod_Manager/releases) page.
2. Extract the ZIP to any folder (e.g., your desktop).
3. Run `HFW_MM.exe` inside the extracted folder; `Decima_pack.exe` is bundled alongside it.

### Development (from source)

If you wish to contribute or run from source:

1. Clone this repository:

   ```bash
   git clone https://github.com/Julz876/HFW_Mod_Manager.git
   cd hfw-mod-manager
   ```

2. Install dependencies:

   ```bash
   pip install PyQt5
   ```

3. Place `Decima_pack.exe` in the same folder as `hfw_mm.py`, or adjust the `pack_dir` variable in `ModManager.pack_mods()`.

---

## Usage

1. **Launch**:

   ```bash
   python main.py
   ```

   or

   Run `HFW_MM.exe`

2. **Select Game Folder**: Click **Browse**, choose the root of your HFW installation (the folder containing `LocalCacheWinGame`).

3. **Drag & Drop**: Drag mod folders or ZIPs into the drag and drop area.

4. **Choose Variations**: If prompted, select the variation you want to install.

5. **Inspect**: Expand each mod to see `shared_files` (always included) and any variation entries.

6. **Activate**: Select the checkbox next to the variation you want to install. Only one variation can be enabled per mod at a time.

7. **Pack**: Click **Pack Activated Mods** to build a new `package.20.01.core.stream` and inject it into the game.

Buttons:

- **Check All / Un-Check All**: Toggle all variations on/off.
- **Refresh Mod List**: Re-scan `mods/` folder for new entries.
- **Remove Selected Mods**: Remove the highlighted mod or variation from the list.
- **Restore Game Files**: Clears `package/ar` removing all installed mods, copies backup of `streaming_graph.core` and `streaming_links.stream` back into `LocalCacheWinGame`.
- **Pack Activated Mods**: Runs the packing workflow (restore → gather → pack → inject).

---

## Best Practices

- Place each mod in its folder under `mods/` with descriptive names.
- For mods with multiple variants, include a `variation.png` in each variant folder.
- Store common assets in a `shared_files/` subfolder.
- It is recommended to keep shared `.stream/.core` files within the mod's parent folder.
- Test mods one or two at a time to confirm functionality.
- Use **Un-Check All** before selecting specific mods to avoid mistakes.
- Back up your game’s `package/` folder externally before major changes.
- Keep `Decima_pack.exe` in the manager folder for correct output placement.
- Close the game to avoid file-lock issues during **Restore** or **Pack**.

## Troubleshooting

- **Permission denied** copying original files: close the game and retry.
- **Missing output**: ensure `Decima_pack.exe` is present and executable.

## Configuration

- **decima.ini**: Stores the path to `LocalCacheWinGame`. Created in the working directory.
- **Backup Folder**: Backups of original core/stream files are stored in `backup/`.
- **Pack Folder**: Temporary unpacked mod files are placed in `pack/`.

---

## Contributing

1. Fork the repo
2. Create a new branch (`git checkout -b feature/new-feature`)
3. Commit your changes (`git commit -am 'Add new feature'`)
4. Push to the branch (`git push origin feature/new-feature`)
5. Open a pull request

---

## License

This project is licensed under the GNU GPL v3.0 License.

