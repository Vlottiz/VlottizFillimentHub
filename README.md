# Filament Forge

A small local app for building, calibrating and keeping track of OrcaSlicer filament profiles. It's tuned for the Snapmaker U1 but works with any Klipper printer.

- **Filament log:** what you bought, where, the price, how many spools are left, and the specs printed on the box.
- **Profile builder:** starts from sensible defaults for the material, applies the manufacturer's specs, then caps anything your printer can't do (from your `printer.cfg`).
- **Confidence score:** every setting shows where it came from (material default, manufacturer, printer limit, you, or calibration) and a likely range. The score goes up as you calibrate.
- **Calibration wizard:** step-by-step through Orca's built-in calibration prints. You enter what you read off the print (and optionally attach photos), and the app works out the new value and updates the profile.
- **Export to Orca:** download a `.json` to import, or install it straight into Orca's user preset folder.
- **History and backups:** every change is versioned and can be restored. You can also snapshot all your Orca filament presets, so an Orca update can't lose them.

Everything runs on your computer. Nothing is uploaded anywhere.

## Run it

You need Python 3.10 or newer ([python.org](https://www.python.org/downloads/); on Windows tick "Add Python to PATH").

- **Windows:** double-click `start.bat`
- **macOS / Linux:** run `./start.sh`

The first run sets up a private `.venv` folder and installs three packages. The app then opens at <http://127.0.0.1:8765>. Your data is saved in the `data/` folder.

Manual start:

```bash
python -m venv .venv
.venv/bin/pip install -r requirements.txt   # Windows: .venv\Scripts\pip ...
.venv/bin/python run.py
```

## Typical workflow

1. **Printers:** a Snapmaker U1 is already set up using the speeds and sizes from Orca's own U1 profile. Click **Import printer.cfg** and pick your config files (download them from the printer's Fluidd/Mainsail config page). That fills in temperature limits and nozzle size.
2. **Filaments:** add a spool. Copy the temperature range and density from the box or store page into "Manufacturer specs".
3. **Make profile:** builds the profile. Check the values, change anything you like, then **Save**.
4. **Export:** **Install into Orca** (then restart Orca), or **Export .json** and in Orca use **File → Import → Import Configs…**.
5. **Calibrate:** follow the steps (temperature → flow → pressure advance → retraction → max flow). After each test, enter the result, save, and re-export to Orca so the next test uses the new values.

## How exported presets work in Orca

Exported presets inherit from Orca's matching generic preset (e.g. `Generic PETG @System`), so Orca fills in anything this app doesn't manage, such as filament G-code. Every setting the app does manage is written into the file, so your tuned values don't depend on the parent preset. You can change or clear the parent on the profile page.

By default the presets appear for every printer. To make them show only for the U1, edit the printer and tick "only show in Orca for this printer".

Orca's folders:

| OS | Location |
|---|---|
| Windows | `%APPDATA%\OrcaSlicer\user\<account>\filament` |
| macOS | `~/Library/Application Support/OrcaSlicer/user/<account>/filament` |
| Linux | `~/.config/OrcaSlicer/user/<account>/filament` |

`<account>` is `default` when you're not logged in to Orca. If your Orca lives elsewhere, set the `ORCA_CONFIG_DIR` environment variable to the folder before starting the app.

## Backups

**Orca & backup** tab:

- **Snapshot Orca presets:** stores a copy of every user filament preset in Orca. Only changed files are saved again.
- **Download backup:** one zip with your whole log, profiles, history and photos. **Restore backup** puts it back and saves a safety copy of the current data first.
- **All profiles as Orca .json:** a zip of ready-to-import presets.

## Roadmap

- Read a store page, photo or PDF and fill in the filament specs automatically
- Look up community settings online and fold them into the confidence score
- Suggest calibration readings from photos of the test print
- Per-toolhead profiles for the U1

## Development

```bash
pip install -r requirements-dev.txt
python -m pytest
```

Code layout: `forge/materials.py` (material defaults), `forge/tuning.py` (layering and confidence), `forge/klipper.py` (printer.cfg parser), `forge/calibration.py` (calibration maths), `forge/orca.py` (preset read/write), `forge/app.py` (API), `forge/static/` (UI).
