# FFD Backup Toolkit (prototype v0.1.0)

A **read-only, rootless** exporter for an **already installed, legitimately acquired** Android copy of *Final Fantasy Dimensions*.

**It contains no game APKs, OBBs, sprites, audio, DRM bypasses, or copyrighted game data.** It never modifies, re-signs, patches, installs, uninstalls, or launches the game. The game's original split APKs and any accessible expansion OBBs are copied byte-for-byte to a new destination.

Tested in an **offline simulated ADB environment**, not on a real Android phone. The **Android app source is supplied and has not been compiled or field-tested in this environment**, because the Android SDK and Gradle build tooling are absent. Please treat it as experimental.

## Included tools

| Tool | Works without root | Copies APKs | Copies OBBs | State |
| --- | --- | --- | --- | --- |
| `windows/ffd_backup.py` (GUI/CLI) | Yes, USB debugging and ADB | Yes | If ADB shell can read them | Offline simulation tests pass |
| `android-app/` native app | Yes | Yes, PackageManager paths | Optional **Shizuku** user service, if shell UID can read them | Buildable project, **not built/phone-tested** |

Default target package: `com.square_enix.android_googleplay.ffl_gp`. There is **no language-specific APK gating** and no assumption about where translations live. The Windows program lets you override the package identifier if necessary.

## 1. Recommended: Windows desktop GUI

1. Install **Python 3.10 or newer** on Windows. Enable **Add Python to PATH** during installation.
2. Install Google's official [Android SDK Platform Tools](https://developer.android.com/tools/releases/platform-tools) and either put `adb.exe` on your PATH or select it from the GUI. You can also extract `platform-tools` inside the `windows/` directory.
3. On your Android phone, enable **Developer options > USB debugging**, connect it via USB, and authorize the computer when prompted.
4. Launch `windows/Launch-FFD-Backup.bat` (or `python windows/ffd_backup.py`).
5. Click **Refresh devices**, select the authorized device, choose an output folder, and click **Export APKs + OBBs**.
6. Inspect the resulting folder, especially **`manifest.json`**. An absent OBB directory, an inaccessible directory, and successfully copied OBBs are reported as different statuses.

**No root required. No need to install or activate FFLmod.** If a ROM blocks ADB from reading OBBs, the exporter records the limitation rather than silently declaring a complete backup.

### Command-line option

```powershell
py -3 windows/ffd_backup.py --list-devices
py -3 windows/ffd_backup.py --cli --out "C:\FFD_Backups"
py -3 windows/ffd_backup.py --cli --device YOUR_SERIAL --out "C:\FFD_Backups" --verify-device
```

`--verify-device` hashes the original files on the device (if `sha256sum` works there) and compares them with the copied files; this adds time proportional to the backup size. By default, the tool still hashes every **local** output file.

### Result layout

```text
FFD_Backup_20261121-120000_SERIAL/
  apks/
    base.apk
    split_config.arm64_v8a.apk
    ... any other installed split APKs ...
  obb/
    main.NNN.com.square_enix.android_googleplay.ffl_gp.obb  (if present)
    patch.NNN.com.square_enix.android_googleplay.ffl_gp.obb (if present)
  manifest.json
```

`manifest.json` contains app version information (where `dumpsys` exposes it), SHA-256 checksums, file sizes, original device paths, `obb_status`, and warnings. Android files not named `.obb` and private saves are **not** part of this exporter.

### Verify a backup afterwards

```powershell
py -3 windows/verify_backup.py "C:\FFD_Backups\FFD_Backup_..."
```

## 2. Standalone Android app (source / experimental)

The `android-app/` directory is an Android Studio project targeting SDK 35 (minimum Android 8.0 / API 26). It uses only platform file APIs and optional [Shizuku](https://github.com/RikkaApps/Shizuku) (v13 API) for OBB access.

Workflow after building/installing its APK:

1. Launch **FFD Backup**. The game is detected through the system package manager.
2. Choose a destination **outside** Android's restricted `Android/obb` tree, e.g. `Documents` or external SD card, through Android's native folder picker.
3. Optionally start Shizuku using Wireless Debugging, allow the backup app to connect, and wait until it reports **Shizuku connected**.
4. Tap **Export original APKs + accessible OBBs**. It creates a timestamped backup folder with SHA-256 metadata.

Without Shizuku, it will still export installed APKs. With Shizuku, the app uses a narrow AIDL user service to request file descriptors for `.obb` files **only under the target game's OBB directory**. Shizuku does not imply root or guarantee read permissions on every ROM/Android release; the app reports access errors.

**Build in Android Studio:** Open `android-app/` as a project and use *Build > Build APK(s)* (the debug APK). Java 17 and Android SDK Platform 35 are required. Gradle wrapper files are not included; Android Studio can use its installed Gradle distribution.

**Build on GitHub Actions:** Publish the toolkit contents at a repository's **root**, then run the included `.github/workflows/build-apk.yml` workflow (*Actions > Build Android APK > Run workflow*). It uploads the built debug-signed APK as an Actions artifact. The workflow has not been run in this environment.

### Android storage limitations

On Android 11+, the system Storage Access Framework does not permit ordinary document pickers to grant access to `Android/obb` or `Android/data`. Consequently, the Android app **does not pretend a regular SAF folder picker can read game OBBs**. Shizuku asks Android for ADB-shell-level access and may help, but is still subject to vendor restrictions. The desktop ADB utility is the fallback.

## Verification, scope and data protection

- The game installation is only **read**; no package install/uninstall operations appear in these tools.
- The Android app requests **only Shizuku** access and document-tree access explicitly granted by the user. It does not request Internet or broad file access permissions.
- The Windows version uses platform-tools' `adb`, optionally verifies device-side hashes, and records blocked transfers rather than inventing OBBs.
- **Game saves**, app-private databases, Google Play authorization/purchase state, and decryption keys are not exported.
- Do not redistribute game APKs or OBBs; keep the backup for your own archival and reverse-engineering work in accordance with local law and relevant terms.
- On some versions of Android (including certain Android 17 builds), shell-level access to protected directories may be further restricted.

## Tests

From the toolkit's root:

```shell
python -m unittest discover -s tests -v
```

The fake ADB integration tests validate split APK + OBB copying, SHA-256 equality, no-OBB behavior, blocked OBB access, device auto-detection, and package-name safety. They do **not** prove compatibility with a physical device.

## Engineering notes and provenance

The APK discovery model follows the approach used by [FFLmod's `GameState.kt`](https://github.com/fflmod/ffl_mod/blob/main/manager/app/src/main/java/io/github/fflmod/fflmod/GameState.kt): `ApplicationInfo.sourceDir` and `splitSourceDirs`. The tools here do **not** depend on FFLmod, copy its patches, or modify its repository. This code is a separate prototype intended to preserve the **originals** as an FFOracle/FFSeraph analysis baseline.

Android and Shizuku references:

- [Android platform Storage Access Framework restrictions](https://developer.android.com/training/data-storage/shared/documents-files)
- [Android Debug Bridge](https://developer.android.com/tools/adb)
- [Shizuku API and UserService documentation](https://github.com/RikkaApps/Shizuku-API)
