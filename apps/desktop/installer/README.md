# Windows installer (Inno Setup)

`racetag.iss` wraps the PyInstaller one-directory build (`apps/desktop/dist/Racetag`) into a single `Racetag-Setup-<version>.exe`. CI builds it in `.github/workflows/release.yml`. This page explains how to build it by hand on a Windows PC.

## What the installer does

- Installs per user into `%LOCALAPPDATA%\Programs\Racetag`. No admin rights and no UAC prompt.
- Adds a Start menu entry. Optionally adds a desktop icon (ticked by default) and an autostart entry at login (`HKCU\Software\Microsoft\Windows\CurrentVersion\Run`, unticked by default).
- Installs the Microsoft Edge WebView2 runtime silently (`/silent /install`) if it is missing. It checks the registry value `pv` of `{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}` under `HKLM\SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients` and `HKCU\Software\Microsoft\EdgeUpdate\Clients`. If that install fails (usually because there is no Internet), setup still finishes and shows the download link. The app shows the same link again when it starts.
- Welcome page in German, including the SmartScreen hint ("Weitere Informationen" → "Trotzdem ausführen").
- Upgrades in place. A running Racetag is closed first, and the old `_internal` folder is replaced.
- Never touches the data directory `%USERPROFILE%\.racetag`, not even when uninstalling.

## Prerequisites

1. **Inno Setup 6.3 or newer**, from https://jrsoftware.org/isdl.php or `choco install innosetup -y`. The German language file ships with it.
2. **The PyInstaller build**, run from `apps/desktop`:

   ```powershell
   python generate_win_version_info.py
   pyinstaller pyinstaller.win.spec --clean --noconfirm
   ```

3. **The WebView2 Evergreen bootstrapper** next to `racetag.iss`. It is about 2 MB, gitignored, and never committed:

   ```powershell
   Invoke-WebRequest -Uri "https://go.microsoft.com/fwlink/p/?LinkId=2124703" `
     -OutFile apps\desktop\installer\MicrosoftEdgeWebview2Setup.exe
   ```

   The script refuses to compile without this file.

## Build

From `apps\desktop\installer`:

```powershell
& "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" racetag.iss
```

The output is `apps\desktop\installer\output\Racetag-Setup-<version>.exe`, which is also gitignored.

Optional preprocessor parameters:

| Parameter | Default | Purpose |
| --- | --- | --- |
| `/DMyAppVersion=0.3.0` | contents of `apps/desktop/VERSION` | Numeric version (x.y.z). Used for the file name, the "Apps" list and the file properties. |
| `/DMyVersionSuffix=+abc1234` | empty | Appended to the displayed version and the file name. CI uses it for test builds from `workflow_dispatch`. |
| `/DSourceDir=C:\path\to\Racetag` | `..\dist\Racetag` | PyInstaller output folder, without a trailing backslash. |

## Testing a build

On a Windows 10/11 test PC, ideally one without admin rights:

1. Run the setup. Check the welcome text, that the desktop icon is ticked and autostart is not, and that no UAC prompt appears.
2. Leave "Racetag starten" ticked on the last page. The app must open.
3. To test the WebView2 path, run setup on a machine without the runtime. The setup log is `%TEMP%\Setup Log *.txt` and has lines starting with `WebView2`.
4. Install again over the existing installation while Racetag is running. Setup must offer to close it, and races must still be there afterwards.
5. Uninstall via Settings → Apps. The message must say that `%USERPROFILE%\.racetag` was kept.

## Unsigned installer

The installer is not code-signed. Windows SmartScreen shows "Der Computer wurde durch Windows geschützt" for files downloaded from the Internet. Click "Weitere Informationen" and then "Trotzdem ausführen". Copying the installer from a USB stick avoids the warning entirely, because the file carries no "downloaded from the Internet" mark.

## Releases from CI

1. Set `apps/desktop/VERSION` to the new version (for example `0.4.0`) and commit.
2. Tag that commit with the same version and push the tag: `git tag v0.4.0 && git push origin v0.4.0`. A pre-release tag keeps the same number, for example `v0.4.0-rc1`.

`release.yml` refuses to build or publish when the tag (without `-rc…`/`-beta…`) does not match `VERSION`. Otherwise the installed app would still report the old version, and its update notice ("Update auf 0.4.0 verfügbar") would keep coming back after every install. To fix a wrong tag, delete it (`git push --delete origin v0.4.0`, `git tag -d v0.4.0`), bump `VERSION`, commit and tag again.

Before the installer is built, the Windows job also stops on:

- a failed `pip install` of any requirements file;
- a GUI stack (`clr`, `webview.platforms.winforms`, `webview.platforms.edgechromium`) that does not import in the build environment. The step log prints the pywebview, pythonnet and clr_loader versions that were used;
- an `ERROR: Hidden import '…' not found` line in the PyInstaller output. PyInstaller itself still exits with 0 in that case;
- a DLL shipped by pythonnet, clr_loader or pywebview (`webview/lib`) that is missing from `dist\Racetag`. `Racetag.exe --selftest` does not load the GUI stack, so it cannot catch this;
- a failing `Racetag.exe --selftest`.
