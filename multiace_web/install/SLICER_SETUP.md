# Snapmaker Orca + multiACE 8-color slicer setup

## Prerequisites

- multiACE installed and running on the U1 (`/api/state` returns device_count ≥ 1)
- Spoolman / FilamentHub running with spools bound to slots
- Python 3.11+ on the machine running Snapmaker Orca
- `multiace_postprocess.py` saved to a stable path (e.g. `~/bin/multiace_postprocess.py`)

## Step 1: Set the printer host env var

Add to your shell profile (`~/.zshrc`, `~/.bashrc`, or Windows environment variables):

```
export DAVINCI_U1_HOST=192.168.1.136
```

Replace `192.168.1.136` with your printer's IP. Restart Snapmaker Orca after setting it.

## Step 2: Install the multiACE 8-color printer profile

1. Locate your Orca profile directory:
   - Linux/macOS: `~/.config/SnapmakerOrca/user/`
   - Windows: `%APPDATA%\SnapmakerOrca\user\`
2. Copy `snapmaker-orca-profile-multiace.json` to that directory.
3. Restart Snapmaker Orca.
4. In Printer settings, select **Snapmaker U1 + multiACE 8-color**.

> **If Orca expects a `.orca_printer` bundle:** open the stock U1 `.orca_printer` file
> (it is a zip archive), replace its `machine.json` extruder count with 8, add the
> 8 `extruder_colour` entries, and re-zip. File a GitHub issue so we can ship a
> pre-built bundle.

## Step 3: Configure the post-processing script

1. In Snapmaker Orca, go to **Process → Others → Post-processing Scripts**.
2. Add:
   ```
   python3 /absolute/path/to/multiace_postprocess.py;%1
   ```
   The `%1` placeholder is replaced by Orca with the exported gcode path.
3. Click OK.

## Step 4: Verify the setup (2-color sanity check)

1. Load any model and assign 2 filaments in the 8-color profile.
2. Slice → Export.
3. Check the console / Orca log for `[postprocess]` lines.
4. Look for a `.multiace.json` sidecar next to the exported gcode.
5. Open the multiACE web console → **Print queue** tab. The file should appear with
   a status chip.

## Step 5: Bind spools and print

1. Open **FilamentHub** and bind spools to the slots your print uses.
2. Re-validate in the Print queue tab if needed.
3. When the status chip turns **Ready** (green), click **Print**.

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| No `[postprocess]` lines in Orca log | Post-script path wrong | Check Step 3 exact path + `python3` on PATH |
| `no_8_tool_header` error in sidecar | Wrong slicer profile selected | Switch to the multiACE 8-color profile |
| All tools `match_quality=none` | `DAVINCI_U1_HOST` not set | See Step 1 |
| `moonraker_unreachable` | Printer off or wrong IP | Check `DAVINCI_U1_HOST` value |
| Ambiguous match | Two slots have same (type, color) | Remove duplicate from Spoolman or use different filament |
