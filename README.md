# sygic-patcher

**Patch Sygic GPS Navigation & Maps for Android** (`com.sygic.aura`) to unlock native fps, full render resolution, the internal debug menu, smoother map rotation easing, patch skin (map colors) and to kill promo popups on startup. You might call it **Sygic Unshittifier**.

It's meant for **patching your own legitimately-purchased/licensed copy** for personal use, not for distributing patched APKs or bypassing any license/paywall check — **none of these patches touch licensing or entitlements.**

> **Installing the patched `.xapk`** needs an XAPK-aware installer — Android's own installer only handles single `.apk` files. [**Universal Installer**](https://f-droid.org/packages/app.pwhs.universalinstaller/) (F-Droid) is a good free option.

You can stil login with your account to access your license. Sygic seems to be fine with patched APKs as there was no code which for checks signature or hashes.

## Tested versions (arm64-v8a)

All patches were originally derived against **26.4.2-115708**, but the update-resistant search logic (see below) has been confirmed working on the following versions:

* [**26.4.2-115708**](https://apkpure.com/de/sygic-gps-navigation-maps-for-mobile/com.sygic.aura/download/26.4.2-115708)
* [**26.4.1-114499**](https://apkpure.com/de/sygic-gps-navigation-maps-for-mobile/com.sygic.aura/download/26.4.1-114499)
* [**26.3.2-112577**](https://apkpure.com/de/sygic-gps-navigation-maps-for-mobile/com.sygic.aura/download/26.3.2-112577)
* [**26.2.0-108257**](https://apkpure.com/de/sygic-gps-navigation-maps-for-mobile/com.sygic.aura/download/26.2.0-108257)

Newer versions might work too but they haven't been tested.

## About
I highly prefer Sygic's approach to driver-centered usability and UI design over Google Maps and always liked the smooth navigation experience it provided. However, a few years ago (anything after Version 18, I guess) they removed the **Battery Management** feature, which allowed switching between *OPTIMIZED* and *PERFORMANCE* mode. What *OPTIMIZED* effectively does is constantly change the framerate of the natively rendered OpenGL view to **15fps** during slow movement and boost it to max (**60fps or 120fps**) during faster camera movements. In reality, this results in a noticeably choppy and inconsistent experience, which makes your high-end phone look like it can't handle simple navigation apps.

**I ABSOLUTELY HATED THIS.** I decide whether I want to save power! Also, my Android head unit in my car doesn't have a battery to save at all. It was absolutely idiotic to remove this feature from the UI while the embedded Sygic SDK core still has the option to enable *PERFORMANCE* mode. So I combined my reversing skills with Claude's superpowers to fix this bullshit.

While at it, I discovered more: on **high-res devices** like my Android tablet with a QHD screen, the map view was really blurry. Turns out, anything above 1080p is **rendered at 1080p and then upscaled**, making text hard to read. WTF?! Like my device can't handle its own display resolution or what?! So, let's fix this. And guess what, the result makes a hell of a visual difference while not impacting battery life at all!

Next, I noticed my old Sygic version had really **smooth navigation camera movements** while the new ones moved linearly and thus stopped abruptly at the end of the rotation. Maybe this was changed so it does not interfere with their stupid *optimized* fps bullshit. Well, after patching a tiny integer, it uses the built-in `decelerate` interpolation again, so no abrupt stops.

Another goal was to fix the **night mode map skin**, because it was too bright on my Android head unit and also hard to read (gray streets on gray background). So, feel free to recolor your maps as you wish. I added my custom night skin in the `skin_examples` folder — feel free to try it by replacing the relevant files.

Finally, I bought a lifetime license with live traffic ages ago. No, I don't want any subscriptions. I'd rather pay 100€ once than 20€ a year. So, fuck off with your **stupid promo popups** which take extra time to load while interrupting me from entering the destination I want to navigate to. Guess what `-N` does for you...

## What it does

`build_patched_xapk.py` takes an original `.xapk` (e.g. from APKPure) and produces a patched, re-signed `.xapk`, ready to install. Every patch is toggled independently via CLI flags:

| Flag | Effect |
|---|---|
| `-F`, `--fps-unlock` | `libsygic.so`: raises the `CSDKMapView` FPS-override default from `-10.0` to `+120.0`, switching the map renderer from throttled BALANCED mode to PERFORMANCE (native framerate). |
| `-D`, `--debug-menu` | `classes6.dex`: nops out the `FEATURE_DEBUG_MENU.isActive()` check in `SettingItemsManager`, unconditionally exposing the internal Debug/DevSettings/DevActions/Features/UiKit menu. |
| `-R`, `--native-res` | `classes6.dex`: raises `LowGL`'s 1080px render-surface cap to 32767 (both call sites), so the map renders at true device resolution instead of being upscaled on screens with a >1080px shorter side. |
| `-T CURVE`, `--turn-ease CURVE` | `libsygic.so`: Smooth the navigation-follow camera rotation. `CViewCamera::UpdateRotation`'s hardcoded interpolation curve (originally always Linear) becomes selectable — `linear`, `accelerate`, `decelerate`, `accel_decel`, `bounce` |
| `-L`, `--debug-licenses` | `classes6.dex`: makes `SettingItemsManager.J()` return its built "Licenses" debug-menu folder instead of discarding it (dead code, no flag gate). Requires `-D`. **Not included in `--all`** — the screen it exposes is permanently empty (unfinished ViewModel stub), so it's not actually useful. |
| `-N`, `--no-startup-promo` | `classes2.dex`: Disable the startup webview promo dialog by forcing `ModalManagerImpl.checkShowPromoDialog()` onto its own "nothing to show" path. That check has no license/premium gate at all, so it nags lifetime/premium accounts exactly like free ones. |
| `-S DIR`, `--skins DIR` | Splices in any `.xml`/`.json` files under `DIR/assets/res/skin/` that differ from the originals — e.g. hand-edited night-mode map color tables. Defaults to `./skin_override` (see `extract_skins.py` below) and is applied automatically whenever that directory exists; pass `-S` explicitly only to point elsewhere. |
| `--all` | Enables `-F -D -R -N` with `--turn-ease=decelerate` (not `-L`). |
| `-v`, `--verbose` | Print the full external commands (`java`, `zipalign`, `apksigner`) as they run. Off by default. |

Every patch verifies its target before writing anything, so a version mismatch fails safely instead of producing a broken build. Each one also searches fresh for whatever stays stable across versions (surrounding bytes, a field name, a debug string, which `classesN.dex` a class actually lives in) rather than trusting a fixed offset or path — but only applies the fix when that search is unambiguous.

If a target still can't be found, it's not fatal: run from a terminal, you're asked whether to skip just that patch and continue.

## Requirements

- **Install:** Python 3 and Java (JRE) on `PATH`.
- **Optional install:** `zipalign`/`apksigner` (`sudo apt install zipalign apksigner` on Debian/Ubuntu, or your distro's equivalent) — used automatically from `PATH` if present, skipping the download below.
- **Downloaded automatically** into `./deps/` by `./fetch_tools.py` (or `make deps`), checksum-verified, skipping anything already available: [smali/baksmali](https://github.com/JesusFreke/smali) 2.5.2 + runtime deps from Maven Central (always, no OS package exists), and `zipalign`/`apksigner` as a fallback if not found above.
- **Your own signing keystore** — generate one with `./generate_keystore.sh` (see below), or `keytool -genkeypair ...` yourself. **Never commit this.**
- No `pip install` needed for anything — all three scripts are standard-library-only Python; `pyproject.toml`/`uv.lock` just let you `uv run script.py` if you prefer.

Override any tool location via `ZIPALIGN`/`APKSIGNER`/`SMALI_JAR`/`BAKSMALI_JAR`/`SMALI_LIBS_DIR` env vars; otherwise everything defaults to `./deps/` or `PATH` as above.

> **Tested on Linux only** but might work on other OS as well.

## Usage

### Makefile (quickest)

```sh
make
```

Place your unpatched Sygic `.xapk` in the current directory and run `make` (or `make XAPK=path/to/file.xapk`). Fetches whatever tools are missing, generates a keystore if needed, extracts skins for editing, and produces `<name>_patched.xapk`.

| Target | Runs |
|---|---|
| `make deps` | `fetch_tools.py` |
| `make keystore` | `generate_keystore.sh`, if `sygic-patcher.jks` doesn't exist yet |
| `make extract` | `extract_skins.py`, if `skin_override/` doesn't exist yet |
| `make patchall` | `deps`, then `build_patched_xapk.py <xapk> <name>_patched.xapk --all` |
| `make all` | `deps` + `extract` + `patchall` (pulls in `keystore` too) — the default, same as bare `make` |

Re-running `make all` is a fast no-op once the output exists — delete what you want regenerated to force a rebuild. `--all` is hardcoded here; for individual flags or a custom `-S` directory, use the scripts directly (below).

### Manual

**1. External tools**

```sh
./fetch_tools.py
```

Downloads smali/baksmali, and `zipalign`/`apksigner` if not already on `PATH`, into `./deps/` — skipping anything already available. Checksum-verified; `--force` to redo.

**2. Signing keystore**

```sh
./generate_keystore.sh
```

Creates `sygic-patcher.jks` (self-signed, password `changeit`) — doesn't need to be secret, just consistent across rebuilds so Android treats them as updates to the same app. `--help` lists the overridable options (identity fields, alias, password, etc.).

**3. Build**

```sh
./build_patched_xapk.py Sygic.xapk Sygic_patched.xapk --all
```

`--keystore`/`--ks-alias`/`--ks-pass` default to exactly what step 2 produces, so nothing else needs passing. Install the result the same way as the original — it replaces the existing install, since it's signed with your own cert.

**Skin edits (`-S`)**

```sh
./extract_skins.py Sygic.xapk
# edit skin_override/assets/res/skin/*.xml ...
./build_patched_xapk.py Sygic.xapk Sygic_patched.xapk --all
```

Both scripts default to `./skin_override`; pass a directory explicitly to either one for a different location. `extract_skins.py` won't overwrite existing files there unless you pass `--force`.

## APatch module (`module/`)

An alternative to reinstalling: `module/module.prop` + `module/service.sh` bind-mount patched APKs over the live install paths at boot (via [APatch](https://github.com/bmax121/APatch), a rooted Magisk-alternative), leaving the original Play Store install/signature untouched. The service script waits for `pm` to come up, resolves the installed paths via `pm path com.sygic.aura`, and bind-mounts your patched `base.apk` / `arm64_v8a` split over them.

To use it: run `build_patched_xapk.py`, then copy the patched `com.sygic.aura.apk` (base) and `config.arm64_v8a.apk` → `split_config.arm64_v8a.apk` next to `module.prop`/`service.sh`, zip the four files together, and flash the zip as an APatch module. The APK binaries aren't included in this repo — you generate them yourself.

## Why no patched APKs, skin files, or decompiled sources in this repo

This tool operates on Sygic's proprietary binary — redistributing patched APKs, extracted skin assets, or decompiled sources could violate law or user agreements. The repo only contains the patch logic (offsets, bytecode diffs, smali transforms) needed to reproduce the patches yourself against an unpatched APK.
