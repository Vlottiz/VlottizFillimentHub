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

- **From the app (most reliable):** open the profile and click **Install into Orca**, then restart Orca. The profile shows whether it's in Orca and up to date; installing again updates it in place, never a second copy (older copies under its old name or from an imported bundle are removed). **Remove from Orca** takes it out again. Anything replaced or removed is backed up in **Orca & backup → Snapshots** first.
- **By hand:** in Orca, **File → Import → Import Configs…** and pick the `.json` file.

Then pick it in the filament dropdown in the Prepare tab; it's under the user presets. If it isn't listed, make sure the Snapmaker U1 is the selected printer.

## VlottizFF bundle (recommended way to get presets into Orca)

Click **Download VlottizFF bundle for Orca** on the Profiles page (or in **Orca & backup**). You get one `VlottizFF.orca_filament` file with all your profiles. In Orca: **File → Import → Import Configs…** and pick it. Your filaments then appear in the filament dropdown under **Bundle presets → VlottizFF**, the same way OrcaGen does.

To update, download a new bundle and import it the same way: it has the same id, so it replaces the old one. Orca treats bundle presets as read-only; make changes in this app and re-import.

## Walls

Each profile has a **Walls** section: outer and inner wall speed, outer wall acceleration, number of walls, and a note. Orca applies these from its **print settings** (not the filament preset), so set them there when you print with this filament; the app keeps them per filament, writes them into the filament's Notes in Orca, and warns when a wall speed needs more plastic than this filament can melt (its max volumetric speed). Your **VFA** calibration fills in the outer wall speed, and **Fix a print problem → Rough / uneven walls** can try slower walls in a trial profile.

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

On a profile, click **Print QR swatch** to download a `.3mf` plate (60 × 60 × 5 mm by default) with a QR code on top. Choose what the code holds:

- **Quick scan** (default): just the filament's name, material and colour. The code is small (41 × 41 squares, about 1.3 mm each on a 60 mm plate), so phones read it easily off a print. Scanning it brings the filament up in *your* app; the settings stay in the app.
- **Full settings**: the whole profile is packed into the code, so anyone can scan it and save the `.json` or the **VlottizFF bundle**, even with your PC off. The code has about 65 × 65 squares, so it needs a plate of about **90 mm** or more. At 60 mm the squares are under 1 mm and phones can't read them off a print.

The dialog shows the square size: aim for **1.2 mm or more** with a 0.4 mm nozzle.

- **Two-colour** (best scanning): the QR code is a separate 0.6 mm part. In Orca, give "QR code (second colour)" a dark filament in another toolhead.
- **Hollowed out** (one colour): the dark squares are cut almost all the way through, so their shadows form the code.

The filament's name is printed under the code (you can change the text in the dialog, or leave it blank).

**Swatch check:** however you scan a swatch, the app walks you through three steps: **(1) In Filament Forge?** (adds it if missing; if the app's copy has changed since the swatch was printed, it lists the differences and lets you use the swatch's values or keep the app's), **(2) In OrcaSlicer?** (installs or updates it), and **(3) Which toolhead is it loaded in?** (or just adding it). The app remembers what's in each toolhead: see the Printers page (with **unload**) and the badge on the profile. Orca itself still needs you to pick the filament for that slot on the Prepare tab.

**Getting a swatch into the app:**
- **Scan swatch** (Filaments page): hold the swatch up to your PC's webcam, or upload a photo of it. The app reads the code and imports the profile.
- On the scan page (phone or PC): **Add to Filament Forge on this computer** opens the app and imports it, if the app is running on that computer. On a phone, **Save or send** the .json or bundle instead.
- Or paste the link your phone shows into **Scan swatch → Paste a swatch link**.

**Scanning shows nothing?** Use Quick scan or a bigger plate (squares of 1.2 mm or more). Print the code in a *different, darker, matte* filament than the plate (same filament = invisible code); silk and other shiny filaments reflect light and are hard to read, so use a matte plate for silk. Check that GitHub Pages is turned on (below), and try scanning in good light from about 15–20 cm. Without the scan page, you can still get the profile: copy the link your phone shows, and in the app use **Filaments → Paste swatch link**.

**One-time setup:** the scan page is `docs/index.html` in this repo. Turn on GitHub Pages: repo **Settings → Pages → Deploy from a branch → main → /docs → Save**. (If it's set to `/ (root)` instead, a small redirect page sends swatch links on to `docs/`, so either works.) The page address (default `https://vlottiz.github.io/VlottizFillimentHub/`) can be changed in the QR dialog.

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

## Printers from Orca (live)

**Printers → Link from Orca** lists the printers you've set up in OrcaSlicer (the one selected in Orca now is first).
Pick one and its toolheads, nozzle, bed size and speed limits come across, with no `printer.cfg` needed, so this works
for any printer Orca knows (a Centauri Carbon, a Bambu, anything). After that the app **follows Orca**: change a
filament slot or the printer's settings in Orca and the Printers page updates within a few seconds. Orca writes its
slots to its own settings file straight away; the app only reads that file and never changes it.

If you choose a toolhead in the app (after scanning a swatch), the app keeps your choice until you next change that
printer's filaments in Orca. Pushing a filament *into* an Orca slot from the app needs Orca's plugin system
(Orca 2.5.2 or newer); that's the next step.

## Spools

Each filament can have several spools, numbered **#1, #2, #3…**. Add them from **Spools → + Add spool** (or from the
profile page, or when you scan a swatch and pick a toolhead: it asks which spool you loaded).

- **Weigh**: put the spool on a kitchen scale and enter the reading. With the empty spool's weight, the app works
  out what's really left and corrects its count. **No empty spool to weigh?** Weigh a *new* spool before using it and
  the app works out the empty weight (reading minus the 1 kg of filament). Or, on an opened spool, weigh it before and
  after printing: the app shows what the scale says you used next to what Orca counted, and corrects what's left.
- **Count prints from Orca**: the Spools page shows a line to paste into Orca's process settings, under
  **Others → Post-processing Scripts**. After every slice you export or send, Orca runs it. Filament Forge reads the
  grams each slot uses from the G-code and takes them off the spool loaded in that toolhead. It never changes the
  G-code. If the app is closed, the print is counted when it next starts.
- **Recent prints**: mark a print as *didn't print* (all the filament goes back) or *failed part way* (you say how
  far), or move it to the spool it really used.

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
