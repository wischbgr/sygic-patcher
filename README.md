# sygic-patcher

Patch Sygic GPS Navigation & Maps for Android (`com.sygic.aura`) to unlock native framerate, native render resolution, the internal debug menu, smoother navigation-camera easing, and to kill the startup "buy Premium Plus" promo popup. Aka. "Sygic Unshittifier".

Everything here was reverse-engineered against **26.4.2-115708 (arm64-v8a)**. It's meant for patching your own legitimately-purchased/licensed copy for personal use, not for distributing patched APKs or bypassing any license/paywall check — none of these patches touch licensing or entitlements.

## What it does

`build_patched_xapk.py` takes an original `.xapk` (e.g. from APKPure) and produces a patched, re-signed `.xapk`, ready to install. Every patch is toggled independently via CLI flags:

| Flag | Effect |
|---|---|
| `-F`, `--fps-unlock` | `libsygic.so`: raises the `CSDKMapView` FPS-override default from `-10.0` to `+120.0`, switching the map renderer from throttled BALANCED mode to PERFORMANCE (native framerate). |
| `-D`, `--debug-menu` | `classes6.dex`: nops out the `FEATURE_DEBUG_MENU.isActive()` check in `SettingItemsManager`, unconditionally exposing the internal Debug/DevSettings/DevActions/Features/UiKit menu. |
| `-R`, `--native-res` | `classes6.dex`: raises `LowGL`'s 1080px render-surface cap to 32767 (both call sites), so the map renders at true device resolution instead of being downscaled on screens with a >1080px shorter side. |
| `-T CURVE`, `--turn-ease CURVE` | `libsygic.so`: `CViewCamera::UpdateRotation`'s hardcoded interpolation curve (originally always Linear) becomes selectable — `linear`, `accelerate`, `decelerate`, `accel_decel`, `bounce` — smoothing the navigation-follow camera rotation. |
| `-L`, `--debug-licenses` | `classes6.dex`: makes `SettingItemsManager.J()` return its built "Licenses" debug-menu folder instead of discarding it (dead code, no flag gate). Requires `-D`. **Not included in `--all`** — the screen it exposes is permanently empty (unfinished ViewModel stub), so it's not actually useful. |
| `-N`, `--no-startup-promo` | `classes2.dex`: forces `ModalManagerImpl.checkShowPromoDialog()` onto its own "nothing to show" path, disabling the startup webview promo dialog. That check has no license/premium gate at all, so it nags lifetime/premium accounts exactly like free ones. |
| `-S DIR`, `--skins DIR` | Splices in any `.xml`/`.json` files under `DIR/assets/res/skin/` that differ from the originals — e.g. hand-edited night-mode color tables. Defaults to `./skin_override` (see `extract_skins.py` below) and is applied automatically whenever that directory exists; pass `-S` explicitly only to point elsewhere. |
| `--all` | Enables `-F -D -R -N` with `--turn-ease=decelerate` (not `-L`). |

Every patch asserts the expected original bytes/text before writing anything and aborts loudly on a mismatch, so running it against a different app version fails safely instead of producing a broken build.

## Quick start

```sh
make all
```

Run from a directory containing exactly one `.xapk` (or `make XAPK=path/to/file.xapk all`). Fetches whatever external tools aren't already available, generates a keystore if none exists yet, extracts skins for editing, and produces `<name>_patched.xapk` — see [Makefile](#makefile) below for the individual steps, or the sections below for what each one does and why.

## Requirements

- Python 3 and Java (JRE) on `PATH` — that's it. All three scripts are pure standard-library Python (no `pip install` needed for anything); see [Dependencies](#dependencies) below.
- [smali/baksmali](https://github.com/JesusFreke/smali) 2.5.2 + runtime deps, and `zipalign`/`apksigner` — run `./fetch_tools.py` (or `make deps`), which does a preflight check first and only downloads what isn't already available. `zipalign`/`apksigner` are real Debian/Ubuntu packages (`sudo apt install zipalign apksigner`, or whatever your distro/OS provides) picked up automatically if already on `PATH`; smali/baksmali have no such package anywhere, so those always come from Maven Central unless you point `SMALI_JAR`/`BAKSMALI_JAR`/`SMALI_LIBS_DIR` at your own copies.
- Your own signing keystore — **never commit this**. Generate one with `./generate_keystore.sh` (see below), or `keytool -genkeypair ...` yourself.

Tool locations default to `./deps/` (see the top of `build_patched_xapk.py`) for smali/baksmali, and to whatever's resolvable on `PATH` (falling back to that same `./deps/` default) for zipalign/apksigner — matching whatever `fetch_tools.py` did or didn't need to download, so no env vars are needed either way. Everything under `deps/` is downloaded tooling, not part of the repo — it's gitignored as a single directory, kept separate from the tracked source so it's obvious at a glance what's what.

## Usage

### 0. Get the external tools

```sh
./fetch_tools.py
```

Checks first whether `zipalign`/`apksigner` are already on `PATH` (or pointed at by the `ZIPALIGN`/`APKSIGNER` env vars) and skips downloading them if so; smali/baksmali get the same treatment against `SMALI_JAR`/`BAKSMALI_JAR`/`SMALI_LIBS_DIR`. Anything not already available gets downloaded into `./deps/` — smali/baksmali 2.5.2 + its runtime deps from Maven Central, and — only if needed — the Android SDK build-tools 35.0.0 archive from Google — which is exactly where `build_patched_xapk.py` already looks by default, so nothing else needs configuring. Keeping it all under one `deps/` directory (rather than scattered at the repo root) means one gitignore entry covers it and it's obvious what's tracked source vs. downloaded tooling. Every download is checksum-verified against a value hardcoded in the script before being written — a corrupted or tampered download is a hard error, not a silently broken toolchain. `--force` re-downloads and overwrites regardless of what's already there. The Android build-tools fallback path is only tested on Linux; on macOS/Windows it's fetched from the same official source but the exact extracted files aren't independently verified here.

### 1. Get a signing keystore

```sh
./generate_keystore.sh
```

Creates `sygic-patcher.jks` (RSA 2048, self-signed, valid ~25 years, password `changeit`). This keystore doesn't protect anything sensitive — it just has to stay the *same* across rebuilds, since Android only treats a patched install as an "update" if it's signed with the same key every time — so the password is a fixed default rather than something you need to manage. Defaults for the certificate's identity fields (CN/OU/O/L/ST/C), output path, alias, password, key size, and validity period are all overridable; run `./generate_keystore.sh --help` for the full list.

### 2. Build the patched xapk

```sh
python3 build_patched_xapk.py Sygic.xapk Sygic_patched.xapk --all
```

`--keystore`, `--ks-alias`, and `--ks-pass` all default to exactly what step 1 produces with no arguments (`./sygic-patcher.jks`, alias `sygic-patcher`, password `changeit`), so none of them need to be passed for the common case — only override them if you generated the keystore with different `-o`/`-a`/`-p` values. `--keystore` only falls back silently when the default file actually exists; with no keystore anywhere, it's a clear error telling you to run `generate_keystore.sh` first rather than a confusing failure deeper in the build. Install the resulting `.xapk` with any XAPK-aware installer (it must replace the original install, since it's re-signed with your own cert — the Play Store copy and this one can't coexist or be updated into one another).

### Skin edits (`-S`)

Sygic's skin/theme XMLs live at `assets/res/skin/*.xml` (and a few `.json`) inside the base APK. `extract_skins.py` pulls them straight out of your `.xapk` into `./skin_override` by default — the same directory `build_patched_xapk.py` looks for automatically, so neither script needs a directory argument for the common case:

```sh
python3 extract_skins.py Sygic.xapk
# edit files under skin_override/assets/res/skin/ ...
python3 build_patched_xapk.py Sygic.xapk Sygic_patched.xapk --all
```

Pass an explicit directory to either script (`extract_skins.py Sygic.xapk my_dir`, `build_patched_xapk.py ... -S my_dir`) if you want a different location — in that case a missing directory is an error rather than a silent skip. `extract_skins.py` won't overwrite files already present in the output directory (so re-running it after you've started editing is safe) unless you pass `--force`. Only files that actually differ from the original get spliced back in by `build_patched_xapk.py`.

## APatch module (`module/`)

An alternative to reinstalling: `module/module.prop` + `module/service.sh` bind-mount patched APKs over the live install paths at boot (via [APatch](https://github.com/bmax121/APatch), a rooted Magisk-alternative), leaving the original Play Store install/signature untouched. The service script waits for `pm` to come up, resolves the installed paths via `pm path com.sygic.aura`, and bind-mounts your patched `base.apk` / `arm64_v8a` split over them.

To use it: run `build_patched_xapk.py`, then copy the patched `com.sygic.aura.apk` (base) and `config.arm64_v8a.apk` → `split_config.arm64_v8a.apk` next to `module.prop`/`service.sh`, zip the four files together, and flash the zip as an APatch module. The APK binaries aren't included in this repo — you generate them yourself.

## Makefile

A thin wrapper around the scripts above, for when the individual flags/steps don't matter. Run from a directory with exactly one `.xapk` in it (or pass `XAPK=path/to/file.xapk` to any target):

| Target | Runs |
|---|---|
| `make deps` | `fetch_tools.py` |
| `make keystore` | `generate_keystore.sh`, only if `sygic-patcher.jks` doesn't exist yet |
| `make extract` | `extract_skins.py`, only if `skin_override/` doesn't exist yet |
| `make patchall` | `deps`, then `build_patched_xapk.py <xapk> <name>_patched.xapk --all` |
| `make all` | `deps` + `extract` + `patchall` (which pulls in `keystore` as a prerequisite) |

`keystore`/`extract`/`patchall` are real GNU Make file targets (not just command aliases), so re-running `make all` after the output already exists is a fast no-op rather than redoing the whole pipeline — delete the specific output you want regenerated (or touch its input) to force a rebuild. `--all` is hardcoded for `patchall`/`all`; for anything more selective (individual `-F`/`-D`/`-R`/etc. flags, `-T` curve choice, custom `-S` directory), call `build_patched_xapk.py` directly instead.

## Why no patched APKs, skin files, or decompiled sources in this repo

This tool operates on Sygic's proprietary binary — redistributing patched APKs, extracted skin assets, or decompiled sources would be redistributing their copyrighted material. The repo only contains the patch logic (offsets, bytecode diffs, smali transforms) needed to reproduce the patches yourself against your own legitimately-obtained copy.

## Dependencies

All three Python scripts (`build_patched_xapk.py`, `extract_skins.py`, `fetch_tools.py`) are standard-library only — there's nothing to `pip install`. `pyproject.toml` and `uv.lock` exist anyway so [uv](https://docs.astral.sh/uv/) has a canonical place to pin the Python version and track dependencies if that ever changes, and so each script's own [PEP 723](https://peps.python.org/pep-0723/) inline metadata block lets it run standalone via `uv run script.py` with no project setup at all. `uv run` is entirely optional here — plain `python3 script.py` works identically since there's nothing to resolve.

`pipx` wasn't a fit: it installs *packaged* Python applications (with a `pyproject.toml` build backend and console-script entry points) as isolated global CLI tools, which is more structure than three independent utility scripts with zero dependencies actually need — `[tool.uv] package = false` in `pyproject.toml` says as much explicitly.

`zipalign`/`apksigner` and `smali`/`baksmali` aren't Python packages at all, so neither uv nor pipx can manage them regardless. `zipalign`/`apksigner` do have real OS packages though (`apt install zipalign apksigner` on Debian/Ubuntu), so `fetch_tools.py` checks `PATH` first and only downloads its own pinned copy if they're not already there; `smali`/`baksmali` have no such package anywhere, so those always come from Maven Central. Either way it's a plain checksum-verified downloader, not anything routed through Python's package index.
