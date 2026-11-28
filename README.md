# Filament Forge

A small local app for building, calibrating and keeping track of OrcaSlicer filament profiles. It's tuned for the Snapmaker U1 but works with any Klipper printer.

- **Filament log:** what you bought, where, the price, how many spools are left, and the specs printed on the box.
- **Profile builder:** starts from sensible defaults for the material, applies the manufacturer's specs, then caps anything your printer can't do (from your `printer.cfg`).
- **Confidence score:** every setting shows where it came from (material default, manufacturer, printer limit, you, or calibration) and a likely range. The score goes up as you calibrate.
- **Calibration wizard:** step-by-step through Orca's built-in calibration prints. You enter what you read off the print (and optionally attach photos), and the app works out the new value and updates the profile.
- **Export to Orca:** download a `.json` to import, or install it straight into Orca's user preset folder.
- **History and backups:** every change is versioned and can be restored. You can also snapshot all your Orca filament presets, so an Orca update can't lose them.

- **AI fill-in (optional):** paste a store-page link, PDF data sheet, photo of the spool label, or product text, and a local AI model fills in the filament form. Every value is checked against the page before it's trusted.

Everything runs on your computer. Nothing is uploaded anywhere, including the AI.

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

## Updating

1. Get the new version: **Code → Download ZIP** on GitHub and unzip it over your old copy (or `git pull` if you cloned it).
2. **Keep your `data` folder.** It holds your filaments, profiles, history, photos and the downloaded AI.
3. Run `start.bat` / `start.sh` as usual. It installs any new packages automatically when they've changed.

## Typical workflow

1. **Printers:** a Snapmaker U1 is already set up using the speeds and sizes from Orca's own U1 profile. Click **Import printer.cfg** and pick your config files (download them from the printer's Fluidd/Mainsail config page). That fills in temperature limits and nozzle size.
2. **Filaments:** add a spool. Copy the temperature range and density from the box or store page into "Manufacturer specs".
3. **Make profile:** builds the profile. Check the values, change anything you like, then **Save**.
4. **Export:** **Install into Orca** (then restart Orca), or **Export .json** and in Orca use **File → Import → Import Configs…**.
5. **Calibrate:** follow the steps (temperature → flow → pressure advance → retraction → max flow). After each test, enter the result, save, and re-export to Orca so the next test uses the new values.

## Importing presets (.json)

Click **Import .json** on the Filaments page, or drag one or more preset files (or a .zip of them) anywhere onto the app. Each one becomes:
- a **filament** in your log: brand, name and colour from the preset name, plus colour, cost and temperatures from its settings
- a **profile** with all its settings, matched to your printer from the `@Snapmaker U1` part of the name

Importing a preset that's already in the app updates that profile (with a history entry) instead of making a duplicate.

## Adding presets to OrcaSlicer

- **From the app:** open the profile and click **Install into Orca**, then restart Orca.
- **By hand:** in Orca, **File → Import → Import Configs…** and pick the `.json` file.

Then pick it in the filament dropdown in the Prepare tab; it's under the user presets. If it isn't listed, make sure the Snapmaker U1 is the selected printer.

## VlottizFF bundle (recommended way to get presets into Orca)

Click **Download VlottizFF bundle for Orca** on the Profiles page (or in **Orca & backup**). You get one `VlottizFF.orca_filament` file with all your profiles. In Orca: **File → Import → Import Configs…** and pick it. Your filaments then appear in the filament dropdown under **Bundle presets → VlottizFF**, the same way OrcaGen does.

To update, download a new bundle and import it the same way: it has the same id, so it replaces the old one. Orca treats bundle presets as read-only; make changes in this app and re-import.

## Your own group in Orca (VlottizFF)

Exported presets are grouped under **VlottizFF** by default: the preset name starts with it (`VlottizFF Giantarm PLA Silk Silver @Snapmaker U1`), so they sort together in Orca's filament list, and it's used as the vendor, which Orca uses to group presets when you add or manage filaments. The real brand stays in the app and in the name. Change or turn this off under **Orca & backup → Your group in Orca**.

## How exported presets work in Orca

Exported presets inherit from Orca's matching generic preset (e.g. `Generic PETG @System`), so Orca fills in anything this app doesn't manage, such as filament G-code. Every setting the app does manage is written into the file, so your tuned values don't depend on the parent preset. You can change or clear the parent on the profile page.

Each exported preset names the exact Orca printer preset it's for (the printer's "Orca printer preset name", e.g. `Snapmaker U1 (0.4 nozzle)`), so it shows up when that printer is selected. That name must match the printer's name in Orca exactly.

### Preset not showing in Orca?
- **Restart Orca.** It only reads user presets when it starts.
- **Logged in to Orca?** Orca reads presets from your account's folder, not `default`. The install dialog picks the folder Orca is using; check it.
- **Select the right printer** on the Prepare tab: the one named in the preset.
- **Re-export or re-install** presets made with an older version of this app: those were missing the `version` field Orca needs, and named only "@Snapmaker U1".

Orca's folders:

| OS | Location |
|---|---|
| Windows | `%APPDATA%\OrcaSlicer\user\<account>\filament` |
| macOS | `~/Library/Application Support/OrcaSlicer/user/<account>/filament` |
| Linux | `~/.config/OrcaSlicer/user/<account>/filament` |

`<account>` is `default` when you're not logged in to Orca. If your Orca lives elsewhere, set the `ORCA_CONFIG_DIR` environment variable to the folder before starting the app.

## AI helper

Open the **AI** tab and click **Download & start**. The app:

1. Detects your graphics card (NVIDIA, AMD, or none).
2. Downloads Ollama's portable build (~1.6 GB, plus an AMD add-on if needed) into `data/ollama`, checking its checksum.
3. Downloads the AI model (default `qwen2.5vl:7b`, ~6 GB, which reads text and photos).
4. Runs it in the background **only while Filament Forge is open**. Closing the app stops it. On Windows it's tied to the app's process, so even closing the window or a crash kills it. Elsewhere, a leftover from a crash is cleaned up on the next launch.

After the first setup it starts automatically with the app in a few seconds. Then use **Fill in with AI** at the top of the Add filament form.

How it stays accurate:
- The app downloads and cleans the page itself. The model only reads text and fills a fixed form, so it can't wander off.
- It must quote the sentence each value came from, and the app checks that quote is really on the page. Green outline: found on the page. Amber: check it yourself. Values outside sensible ranges are ignored.

Other ways to run it (AI tab → Settings):
- **Docker:** runs the `ollama/ollama` container while the app is open and removes it on close. GPU support needs an NVIDIA card.
- **My own Ollama:** point it at an Ollama you already run.

Tips: big stores like Amazon often block apps. The maker's own page, the technical data sheet PDF, or a screenshot work best. **Remove AI files** frees the disk space again.

## Backups

**Orca & backup** tab:

- **Snapshot Orca presets:** stores a copy of every user filament preset in Orca. Only changed files are saved again.
- **Download backup:** one zip with your whole log, profiles, history and photos. **Restore backup** puts it back and saves a safety copy of the current data first.
- **All profiles as Orca .json:** a zip of ready-to-import presets.

## Roadmap

- Look up community settings online and fold them into the confidence score
- Suggest calibration readings from photos of the test print
- Per-toolhead profiles for the U1

## Development

```bash
pip install -r requirements-dev.txt
python -m pytest
```

Code layout: `forge/materials.py` (material defaults), `forge/tuning.py` (layering and confidence), `forge/klipper.py` (printer.cfg parser), `forge/calibration.py` (calibration maths), `forge/orca.py` (preset read/write), `forge/app.py` (API), `forge/static/` (UI).
