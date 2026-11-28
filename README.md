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

### Windows: the .exe (easiest)
Download **FilamentForge.exe** from the repo's **Releases** page (or from the latest run under **Actions → Build Windows exe → Artifacts**) and double-click it. A small window opens with the app's address and your browser opens the app. **Keep that window open while you use it; closing it quits the app and stops the AI.**

Windows may warn the first time ("Windows protected your PC") because the exe isn't signed: click **More info → Run anyway**.

### From the source code (any OS)
You need Python 3.10 or newer ([python.org](https://www.python.org/downloads/); on Windows tick "Add Python to PATH").

- **Windows:** double-click `start.bat`
- **macOS / Linux:** run `./start.sh`

It sets up a private `.venv` folder and installs what it needs, then opens <http://127.0.0.1:8765>.

## Your data (and the AI) survive updates

Everything you make (filament log, profiles, history, photos, backups) **and the downloaded AI** is kept in one folder outside the app:

| OS | Folder |
|---|---|
| Windows | `%LOCALAPPDATA%\FilamentForge` |
| macOS | `~/Library/Application Support/FilamentForge` |
| Linux | `~/.local/share/filament-forge` |

So updating never loses anything or re-downloads the AI. If you used an older version that kept a `data` folder inside the app folder, it's moved there automatically the first time you start the new version. (Set `FORGE_DATA_DIR` to use a different folder.)

## Updating

- **.exe:** download the new `FilamentForge.exe` and replace the old one.
- **Source:** download the new ZIP and unzip it over the old folder (or `git pull`), then run `start.bat` / `start.sh` as usual; new packages install automatically.

### Making a release (for the repo owner)
Every push to `main` builds the exe (Actions tab). To publish it on the Releases page, create a tag: on GitHub, **Releases → Draft a new release → Choose a tag → type e.g. `v1.0.0` → Create new tag → Publish**. The workflow attaches `FilamentForge.exe` to that release.

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

## Fixing print problems

On a profile, click **Fix a print problem**. Add a photo and a sentence or two (or pick the problem from the list yourself).

- **The AI only diagnoses.** It picks from a fixed list of common problems (stringing, under-extrusion, warping, bulging corners…) and says how sure it is and what it saw. It never invents settings.
- **The app suggests the changes**: small, well-known steps for that problem, kept within your filament's temperature range and your printer's limits, at most two at a time so you can tell what helped. Problems that aren't caused by the filament (like ringing) get tips instead.
- **You get the final say.** What you write counts strongly ("wavy lines" points at VFA, "strings" at stringing), weak guesses are dropped, and you can untick anything wrong or add a problem it missed before anything is changed.
- **Print settings too.** For problems that aren't the filament (VFA, ringing) it tells you which Orca print setting to change; for VFA it uses the speed your VFA calibration found smoothest.
- **Make a trial profile** to try the changes as a copy; your original isn't touched. Print with it, then click **It's better** (copies the changes into the original, marked "Proven in a print"), **Not better** (deletes the trial), or **Keep as its own profile**.

Every report, with its photos, is kept under **Print problems** on the profile.

Photos are shrunk to 1024 px before the AI sees them, and the AI's context (how much it can take in at once) defaults to 16k, enough for up to 4 photos. You can change it in the AI tab.

## QR swatches

On a profile, click **Print QR swatch** to download a `.3mf` plate (60 × 60 × 5 mm by default) with a QR code on top. Scanning it with a phone opens a page showing the filament's settings, with buttons to **save or send** the `.json` or download the **VlottizFF bundle**. The whole profile is packed into the QR link itself, so it works anywhere, even with your PC off.

- **Two-colour** (best scanning): the QR code is a separate 0.6 mm part. In Orca, give "QR code (second colour)" a dark filament in another toolhead.
- **Hollowed out** (one colour): the dark squares are cut almost all the way through, so their shadows form the code.

The filament's name is printed under the code (you can change the text in the dialog, or leave it blank).

**Scanning shows nothing?** Check that the QR part was printed in a *different, darker* filament than the plate (same filament = invisible code), that GitHub Pages is turned on (below), and try scanning in good light from about 15–20 cm. Without the scan page, you can still get the profile: copy the link your phone shows, and in the app use **Filaments → Paste swatch link**.

**One-time setup:** the scan page is `docs/index.html` in this repo. Turn on GitHub Pages: repo **Settings → Pages → Deploy from a branch → main → /docs → Save**. The page address (default `https://vlottiz.github.io/VlottizFillimentHub/`) can be changed in the QR dialog.

## Reaching 100%

The confidence score reaches 100% when every key setting is either **calibrated** or **proven in a print**. Each profile lists what's holding it back, biggest gains first: run the calibration that sets it, or for settings no test covers (bed temperatures, fans), click **It worked in a print** once a print has come out well.

Which values were calibrated is saved in the preset's **Notes** (visible in Orca's filament settings), so it survives exporting and importing again.

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
