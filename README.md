# sc2-ultrawide

StarCraft II limits its view to 16:9. On a wider monitor you get black bars on the sides or a stretched picture. This tool removes that limit while the game is running, so the game fills a 21:9 or 32:9 screen and the camera shows more of the screen.

It is one Python script. It switches two internal game settings on for a fraction of a second, resizes the game window, and switches them back. It does not modify any game files, and closing the game undoes the change.

## Risks and disclaimer

Read this section before you run anything.

- This program changes the memory of a running StarCraft II client. Blizzard's terms of use forbid third-party programs that modify the game. Your Battle.net account can be suspended or banned for it, including when you only play single-player content. StarCraft II runs anti-cheat checks while you are connected to Battle.net, and nobody outside Blizzard knows exactly what they look for.
- The two settings it switches belong to StarCraft II's built-in client API, the interface Blizzard provides for bots and machine-learning research. While they are on, parts of the game behave as if that API were active. They stay on until the game accepts the new window size, which usually takes well under a second. No side effects showed up in testing, but a crash is possible. Save before you run it during a mission.
- A wider view shows more of the map, which is an unfair advantage against other players. The tool does not check what you are playing, and the window stays wide until you run `revert` or restart StarCraft II. Do one of those before you play any game with other people.

Use it at your own risk. I am not responsible if your Battle.net account is suspended or banned, or if the game crashes and you lose progress.

## Tested resolutions

Tested on StarCraft II 5.0.16 (build 97563), 64-bit client, Windows 11, Direct3D 9 renderer, in Windowed (Fullscreen) mode.

| Resolution | Aspect | Result |
|---|---|---|
| 5120×1440 | 32:9 | Works. Used in campaign missions on a 5120×1440 monitor. |
| 3440×1440 | 21:9 | Works. The game accepted and stored the exact size. |
| 2560×1080 | 21:9 | Works. The game accepted and stored the exact size. |
| 3840×1600 | 24:10 | Works. The game accepted and stored the exact size. |

The 21:9 and 24:10 rows come from forcing the game window to those sizes on the 5120×1440 monitor. A monitor of that size goes through the same code, but nobody has tried one yet.

## Requirements

- Windows, 64-bit (tested on Windows 11)
- StarCraft II build 97563 (patch 5.0.16), 64-bit client. Other builds are not supported without updating the addresses in the script; see [known issues](#known-issues).
- Python 3 (tested with 3.14). The script uses only the standard library.
- Display Mode set to Windowed (Fullscreen)

## Beginner guide for Windows

This section is for people who have never used Python or a command line. Do the steps in order. If you already have Python, skip to [Usage](#usage).

### Step 1: Install Python

1. Go to [python.org/downloads](https://www.python.org/downloads/) and click the yellow **Download Python 3.x** button. Save the installer file.
2. Open the installer. On the first screen, **tick the box "Add python.exe to PATH"** at the bottom. This is the step people miss, and nothing below works without it.
3. Click **Install Now** and wait for it to finish. Click **Close**.
4. Check that it worked. Press the **Windows key**, type `powershell`, and press Enter. A blue or black window opens. Type this and press Enter:

   ```
   python --version
   ```

   You should see something like `Python 3.14.0`. If Windows says Python was not found or opens the Microsoft Store, close the window and reinstall Python with the "Add python.exe to PATH" box ticked.

### Step 2: Download the script

1. Open the [project page](https://github.com/PKDT-93/sc2-ultrawide).
2. Click the green **Code** button, then **Download ZIP**.
3. Open your Downloads folder, right-click the ZIP file, and choose **Extract All...**, then **Extract**. You now have a normal folder called `sc2-ultrawide-main` that contains `sc2_ultrawide.py`.

### Step 3: Set up StarCraft II

1. Start StarCraft II and go to **Options > Graphics**.
2. Set **Display Mode** to **Windowed (Fullscreen)** and apply the change.
3. Go back to the main menu and leave the game running.

### Step 4: Run the script

1. Open the extracted `sc2-ultrawide-main` folder in File Explorer.
2. Click an empty area of the address bar at the top of the window, type `powershell`, and press Enter. A PowerShell window opens already pointed at this folder.
3. Type this and press Enter:

   ```
   python sc2_ultrawide.py apply
   ```

4. The game window stretches to the full width of your monitor. Load a campaign mission and play.

If something goes wrong:

| What you see | What to do |
|---|---|
| `'python' is not recognized` or the Microsoft Store opens | Python is not on your PATH. Reinstall it and tick "Add python.exe to PATH". |
| `SC2_x64.exe is not running` or `Game window not found` | Start StarCraft II, wait for the main menu, then run the command again. |
| The script says the build is not supported | Your game version is not 97563. See [known issues](#known-issues). |
| An "access denied" message | Close PowerShell, search for it in the Start menu, right-click it, choose **Run as administrator**, then `cd` into the folder and run the command again. |

### Every time you play

The change only lasts until you close StarCraft II. Each time you start the game, repeat Step 4 (open PowerShell in the folder and run the `apply` command). You do not need to redo Steps 1 to 3.

To go back to the normal 16:9 window, for example before you play against other people, run this in the same PowerShell window:

```
python sc2_ultrawide.py revert
```

Read the [risks and disclaimer](#risks-and-disclaimer) before you use this.

## Usage

1. In StarCraft II, open Options > Graphics and set Display Mode to Windowed (Fullscreen).
2. Start the game and wait for the main menu.
3. From a terminal in this folder, run:

   ```
   python sc2_ultrawide.py apply
   ```

The window grows to the full width of your monitor. Play the campaign as usual.

Two more commands:

```
python sc2_ultrawide.py status    # read-only: build, window size, whether the wide mode is on
python sc2_ultrawide.py revert    # resize the window back to 16:9
```

The change lasts until StarCraft II closes, so run `apply` again after every launch. Anything that resizes the game window, such as changing display settings, makes the game enforce its 16:9 limit again. Run `apply` again if that happens.

## How it works

StarCraft II limits its aspect ratio (width divided by height) to 1366/768, about 1.7786, a single number stored in `SC2_x64.exe`. Three pieces of code read it: one sizes the game window, one sizes the picture in exclusive fullscreen mode, and one filters the resolution list in the Options menu. When the window is wider than that ratio, the game cuts its width to height × 1.7786. On a 5120×1440 monitor that leaves a 2561-pixel-wide window in the middle of the screen.

Finding that number meant working on the running game, because the executable on disk is encrypted and its code exists in plain form only in memory. The first clue was in the settings: `Documents\StarCraft II\Variables.txt` already held `width=5120`, `height=1440` and `displaymode=1`, yet the game still drew at 2561×1440, so the limit had to be inside the game itself. A read-only copy of the running game's memory (made with `OpenProcess` using `PROCESS_QUERY_INFORMATION | PROCESS_VM_READ`, the game module located through a Toolhelp snapshot, its memory pages listed with `VirtualQueryEx` and copied with `ReadProcessMemory`) confirmed the encryption: the code section looks like random data in the file (8.0 bits of entropy per byte, the maximum) but like normal program code in memory. Searching that copy for values near 16/9, then disassembling the code that reads them (turning machine code back into readable instructions), found the 1366/768 value and the three pieces of code that read it.

Changing that number from outside the game is not possible. `VirtualQueryEx` shows the game's code and fixed values are loaded as read-only, with permission to run but never to write. `VirtualProtectEx` refused every request to make them writable (error 87), and `WriteProcessMemory` failed (error 998), so no other program can change them. Editing the file on disk does not help either, because the code in the file is encrypted.

The window-sizing code has an exception, though. Before it measures anything, it checks whether the client API's render mode is active, and if it is, it accepts any window size. Research clients use that mode to ask for arbitrary resolutions. The check reads two settings, `listen` and `gameStateRender`, and both are stored in a part of the game's memory that can be changed. Their names were confirmed by reading them back from memory.

So `sc2_ultrawide.py apply` does the following:

1. Confirms the game build and reads both settings back by name, so it stops before writing anything if the game's memory does not look the way it expects.
2. Switches both settings on, resizes the game window to fill the monitor with `SetWindowPos`, and waits until the game has stored the new size.
3. Restores both settings to their original bytes.

From then on the game draws at the full width. The 3D camera shows more of the map from side to side, and the interface lays itself out across the whole screen. The game only checks the size again when the window is resized. Running those same steps at several ultrawide sizes, and taking a screenshot with GDI (`BitBlt`) each time, produced the results in the table above.

APIs and tools used:

- Windows process and memory: `OpenProcess`, `CreateToolhelp32Snapshot`, `Module32FirstW`, `VirtualQueryEx`, `ReadProcessMemory`, and, only for `apply`, `VirtualProtectEx` and `WriteProcessMemory`.
- Windows window and screen: `EnumWindows`, `GetWindowRect`, `GetClientRect`, `MonitorFromWindow`, `GetMonitorInfoW`, `SetWindowPos`, and `BitBlt`.
- StarCraft II's own status service at `http://127.0.0.1:6119/game` and `/ui`, used during research to tell the menu apart from a loaded game. The tool itself does not call it.
- For the disassembly and scanning: `pefile`, `capstone`, and `numpy`.

Reading memory never needs write access, so the parts that only read cannot change the game. Only `apply` opens the game for writing, and it changes only those two settings. `revert` writes nothing; it only resizes the window.

## Known issues

- The bottom HUD framing does not fill the width. The console along the bottom of the screen (minimap frame, unit panel, command card) is built from three fixed-width art pieces that meet at 16:9. At wider sizes they sit at the left edge, the center, and the right edge, with bare dark strips between them: about 1,280 pixels each on 32:9 and about 440 pixels on 3440×1440. Some elements, such as control group tabs, hang past the edge of the art. The strip is most likely the band under the console where the game stops drawing the 3D world. It does not affect gameplay.
- Exclusive fullscreen is not supported. The Fullscreen display mode has its own 16:9 limit with no exception, so use Windowed (Fullscreen).
- Only game build 97563 is supported. On any other build, `status` still reports the aspect limit, but `apply` stops before it writes anything, because the writable addresses match build 97563 only. The addresses at the top of `sc2_ultrawide.py` (`LISTEN_OBJECT` through `STORED_HEIGHT`) would need to be found again for the new build.

## TODO

- [ ] HUD: fill in the bottom console framing at ultrawide sizes. One attempt moved the whole console into a centered 16:9 area. That worked, but it looked worse, because the dark band under the console still stretched across the full width. Not tried yet: stretching the plain middle part of the console art to cover the gaps, or making the game draw the battlefield underneath the console. The game rebuilds the HUD every time a mission loads, so a fix would need to re-apply itself each time.

## Credits

Researched and built with Claude Opus 5.5.

## License

MIT for the code in this repository. StarCraft II is a trademark of Blizzard Entertainment. This project is not affiliated with or endorsed by Blizzard, and it contains no Blizzard code or assets.
