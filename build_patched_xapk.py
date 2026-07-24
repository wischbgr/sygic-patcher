#!/usr/bin/env python3
# /// script
# requires-python = ">=3.9"
# dependencies = []
# ///
"""
Build a patched, signed Sygic .xapk from the original.

All patches below were derived against com.sygic.aura 26.4.2-115708 (arm64-v8a).
Every binary/smali patch asserts the expected original bytes/text before writing
anything, and aborts loudly if they don't match -- so running this against a
different app version will fail safely (offsets/instructions likely shifted)
rather than silently producing a broken build. If Sygic ships an update, the
constants near the top of this file are what need re-deriving.

Usage:
  python3 build_patched_xapk.py INPUT.xapk OUTPUT.xapk \\
      [--keystore KEYSTORE.jks] [--ks-alias ALIAS] \\
      [--ks-pass pass:XXXX] [--key-pass pass:XXXX] \\
      [-F] [-D] [-R] [-T {linear,accelerate,decelerate,accel_decel,bounce}] \\
      [-L] [-N] [-S SKIN_DIR] [--all] [--keep-temp]

--keystore/--ks-alias/--ks-pass all default to what generate_keystore.sh
produces with no arguments (./sygic-patcher.jks, alias sygic-patcher,
password changeit) -- run that script once with no arguments and every flag
here can be omitted. --keystore only falls back silently if the default file
actually exists; otherwise it's an error telling you to generate one.

Toggles (all off by default; --all turns everything on except -L, with turn-ease=decelerate):
  -F, --fps-unlock        libsygic.so: CSDKMapView fps-override default
                           -10.0 -> +120.0 (BALANCED -> PERFORMANCE)
  -D, --debug-menu        classes6.dex: nop out FEATURE_DEBUG_MENU.isActive()
                           check in SettingItemsManager.E(), exposing the
                           internal Debug/DevSettings/DevActions/Features/UiKit
                           menu unconditionally
  -R, --native-res        classes6.dex: raise LowGL.MAX_SHORTER_DIMENSION's
                           1080px render-surface cap to 32767 (both call sites),
                           so the map renders at true device resolution instead
                           of being downscaled on >1080px-shorter-side screens
  -T, --turn-ease CURVE   libsygic.so: CViewCamera::UpdateRotation's hardcoded
                           InterpolatorForCurve(...,0) call -> (...,CURVE), so
                           navigation-follow camera rotation eases instead of
                           being linear. CURVE in: linear, accelerate,
                           decelerate, accel_decel, bounce
  -L, --debug-licenses    classes6.dex: SettingItemsManager.J() builds the
                           "Licenses" folder (inside Debug) and then discards
                           it, unconditionally returning null -- not gated by
                           any feature flag, just dead code. Makes it return
                           the built folder instead. Requires -D (or the Debug
                           menu already unlocked some other way) to be reachable
                           at all, since it's a sub-item of that menu. NOTE: the
                           screen it exposes is permanently empty (its
                           ViewModel is an unfinished stub with no data
                           source) -- included for completeness, not useful.
  -N, --no-startup-promo  classes2.dex: ModalManagerImpl.checkShowPromoDialog()
                           (uX2.D0()) forced onto its own existing "nothing to
                           show" early-return path, disabling the startup
                           webview promo dialog ("buy Premium Plus" etc). That
                           check has no license/premium gate at all, so it
                           fires for lifetime/premium accounts same as free.
  -S, --skins DIR          splice in any .xml/.json files under
                           DIR/assets/res/skin/ that differ from the originals
                           (day/night skin color tables etc.) Defaults to
                           ./skin_override (see extract_skins.py) and is applied
                           automatically whenever that directory exists --
                           pass -S explicitly only to point elsewhere. If a
                           DIR is given explicitly and doesn't exist, that's
                           an error rather than a silent skip.

Example (after running ./generate_keystore.sh once):
  python3 build_patched_xapk.py Sygic.xapk Sygic_patched.xapk --all
"""

import argparse
import json
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import zipfile
import zlib

HERE = os.path.dirname(os.path.abspath(__file__))
# Where fetch_tools.py downloads things -- kept out of the repo root itself.
DEPS_ROOT = os.path.join(HERE, "deps")

# ---------------------------------------------------------------------------
# Tooling locations (override via env vars, e.g. if you move this elsewhere)
# ---------------------------------------------------------------------------

def _default_zipalign_apksigner(bare_name, local_rel_path):
    """zipalign/apksigner have real apt packages (Debian/Ubuntu: `apt install
    zipalign apksigner`) that put them on PATH -- prefer that if present,
    since it's simpler than fetch_tools.py's Android-build-tools download.
    Fall back to the local copy fetch_tools.py would have downloaded."""
    return shutil.which(bare_name) or os.path.join(DEPS_ROOT, local_rel_path)


ZIPALIGN = os.environ.get("ZIPALIGN") or _default_zipalign_apksigner("zipalign", os.path.join("android-15", "zipalign"))
APKSIGNER = os.environ.get("APKSIGNER") or _default_zipalign_apksigner("apksigner", os.path.join("android-15", "apksigner"))
# smali/baksmali have no apt package -- always local (see fetch_tools.py).
SMALI_JAR = os.environ.get("SMALI_JAR", os.path.join(DEPS_ROOT, "smali.jar"))
BAKSMALI_JAR = os.environ.get("BAKSMALI_JAR", os.path.join(DEPS_ROOT, "baksmali.jar"))
SMALI_LIBS_DIR = os.environ.get("SMALI_LIBS_DIR", os.path.join(DEPS_ROOT, "smalilibs"))


def smali_classpath(main_jar):
    libs = [os.path.join(SMALI_LIBS_DIR, f) for f in os.listdir(SMALI_LIBS_DIR) if f.endswith(".jar")]
    return ":".join([main_jar] + libs)


# ---------------------------------------------------------------------------
# Patch definitions -- version-pinned against 26.4.2-115708
# ---------------------------------------------------------------------------

ARM64_SO_ENTRY = "lib/arm64-v8a/libsygic.so"

# Same default extract_skins.py writes to -- so a bare `extract_skins.py`
# followed by a bare `build_patched_xapk.py` (no -S needed) round-trips
# through the same directory automatically.
DEFAULT_SKINS_DIR = "skin_override"

# Same defaults generate_keystore.sh uses -- so a keystore made with no
# arguments there needs no --keystore/--ks-alias/--ks-pass here either.
DEFAULT_KEYSTORE = "sygic-patcher.jks"
DEFAULT_KS_ALIAS = "sygic-patcher"
DEFAULT_KS_PASS = "pass:changeit"

FPS_UNLOCK_PATCH = (
    0x1684c60,
    struct.pack("<f", -10.0),
    struct.pack("<f", 120.0),
    "CSDKMapView fps-override default -10.0 -> +120.0 (BALANCED -> PERFORMANCE)",
)

TURN_EASE_OFFSET = 0x2a8f770
TURN_EASE_OLD = bytes.fromhex("e1031f2a")  # mov w1, wzr  (curve=0, Linear)
TURN_EASE_CURVES = {
    "linear": 0,
    "accelerate": 1,
    "decelerate": 2,
    "accel_decel": 3,
    "bounce": 4,
}


def turn_ease_new_bytes(curve_name):
    imm16 = TURN_EASE_CURVES[curve_name]
    word = 0x52800000 | (imm16 << 5) | 1  # movz w1, #imm16
    return struct.pack("<I", word)


# smali patches. Regex patterns deliberately avoid anchoring on baksmali-assigned
# label names (:cond_N) -- those numbers depend on baksmali's invocation context
# (whole-dex vs single-class-file decode, version, etc.) and are NOT stable
# across different decompile runs of the byte-identical dex, even though the
# actual instructions are unique and stable.
DEBUG_MENU_SMALI_FILE = "com/sygic/navi/settings2/d.smali"
# anchor on the FEATURE_DEBUG_MENU sget (unique in the file), then the next
# "if-eqz v1, :cond_XX" that follows it is the isActive() branch to remove.
DEBUG_MENU_PATTERN = re.compile(
    r"(sget-object v1, Lsdk/network/Hx5;->FEATURE_DEBUG_MENU:Lsdk/network/Hx5;.*?)"
    r"if-eqz v1, :cond_\w+",
    re.DOTALL,
)
DEBUG_MENU_REPL = r"\1nop"

NATIVE_RES_SMALI_FILE_1 = "com/sygic/sdk/low/LowGL$ViewScaling.smali"
NATIVE_RES_OLD_1 = "    const/16 p2, 0x438"
NATIVE_RES_NEW_1 = "    const/16 p2, 0x7fff"

NATIVE_RES_SMALI_FILE_2 = "com/sygic/sdk/low/gl/GlSurfaceListenerFactory$GlSurfaceHolderCallback.smali"
NATIVE_RES_OLD_2 = "    const/16 p2, 0x438"
NATIVE_RES_NEW_2 = "    const/16 p2, 0x7fff"

DEBUG_LICENSES_SMALI_FILE = "com/sygic/navi/settings2/d.smali"
# anchor on the DebugLicenses sget (unique in the file); the constructed Folder
# stays live in v0 across the constructor call, then gets clobbered with
# "const/4 v0, 0x0" right before "return-object v0" -- nop that clobber out.
DEBUG_LICENSES_PATTERN = re.compile(
    r"(sget-object v1, Lsdk/network/vt1;->DebugLicenses:Lsdk/network/vt1;.*?)"
    r"const/4 v0, 0x0\n\n    \.line 49\n    return-object v0",
    re.DOTALL,
)
DEBUG_LICENSES_REPL = "\\1nop\n\n    .line 49\n    return-object v0"

# ModalManagerImpl.checkShowPromoDialog() (uX2.D0(), classes2.dex) fires the
# startup "buy Premium Plus" webview promo purely on session count +
# connectivity + a remote LifetimeMonetizationFeature flag -- it has no
# license/premium check at all, so it nags lifetime/premium owners exactly
# like free users. Force it onto the same early-return "nothing to show"
# path the method already takes when promoChecked/session-count/connectivity
# fail, by jumping straight to that path's label from method entry. The
# label name is looked up dynamically (last label in the method body)
# instead of hardcoded, since baksmali's per-run numbering isn't guaranteed
# stable across decompile contexts.
PROMO_DIALOG_SMALI_FILE = "sdk/network/uX2.smali"
PROMO_DIALOG_METHOD_PATTERN = re.compile(
    r"(\.method private static final D0\(Lsdk/network/uX2;\)Lio/reactivex/SingleSource;\n"
    r"    \.registers 4\n\n)(.*?)(\n\.end method)",
    re.DOTALL,
)


def patch_promo_dialog(root):
    path = os.path.join(root, PROMO_DIALOG_SMALI_FILE)
    with open(path, "r") as f:
        content = f.read()
    m = PROMO_DIALOG_METHOD_PATTERN.search(content)
    if not m:
        raise SystemExit("ERROR: checkShowPromoDialog() (uX2.D0) method not found -- "
                          "app version likely doesn't match what this script was derived against.")
    head, body, tail = m.group(1), m.group(2), m.group(3)
    if body.startswith("goto/16 :"):
        print("  [skip, already patched] startup promo webview dialog disabled")
        return
    labels = re.findall(r"^    :(\w+)$", body, re.MULTILINE)
    if not labels:
        raise SystemExit("ERROR: no labels found in checkShowPromoDialog() -- "
                          "app version likely doesn't match what this script was derived against.")
    target = labels[-1]
    new_content = content[:m.start()] + head + f"goto/16 :{target}\n\n" + body + tail + content[m.end():]
    with open(path, "w") as f:
        f.write(new_content)
    print(f"  patched: startup promo webview dialog ('buy Premium Plus' popup) disabled (-> :{target})")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def run(cmd, **kw):
    print("+", " ".join(cmd))
    subprocess.run(cmd, check=True, **kw)


def patch_smali_text(root, rel_path, old, new, desc):
    path = os.path.join(root, rel_path)
    with open(path, "r") as f:
        content = f.read()
    if new in content and old not in content:
        print(f"  [skip, already patched] {desc}")
        return
    if old not in content:
        raise SystemExit(f"ERROR: expected smali text not found for '{desc}' in {rel_path}\n"
                          f"App version likely doesn't match what this script was derived against.")
    content = content.replace(old, new, 1)
    with open(path, "w") as f:
        f.write(content)
    print(f"  patched: {desc}")


def patch_smali_regex(root, rel_path, pattern, repl, desc):
    path = os.path.join(root, rel_path)
    with open(path, "r") as f:
        content = f.read()
    new_content, n = pattern.subn(repl, content, count=1)
    if n == 0:
        raise SystemExit(f"ERROR: expected smali pattern not found for '{desc}' in {rel_path}\n"
                          f"App version likely doesn't match what this script was derived against.")
    with open(path, "w") as f:
        f.write(new_content)
    print(f"  patched: {desc}")


def patch_so_in_zip(apk_path, patches):
    """Apply a list of (offset, old_bytes, new_bytes, desc) raw patches to
    lib/arm64-v8a/libsygic.so inside apk_path, in place. Entry must be STORED
    (uncompressed), which it is for this app (extractNativeLibs=false)."""
    zf = zipfile.ZipFile(apk_path, "r")
    info = zf.getinfo(ARM64_SO_ENTRY)
    if info.compress_type != zipfile.ZIP_STORED:
        raise SystemExit(f"ERROR: {ARM64_SO_ENTRY} is not STORED (compress_type={info.compress_type}); "
                          f"raw offset patch assumptions no longer hold.")
    with open(apk_path, "rb") as f:
        f.seek(info.header_offset)
        local_header = f.read(30)
        sig, ver, flag, method, mtime, mdate, crc, csize, usize, fnlen, exlen = struct.unpack(
            "<IHHHHHIIIHH", local_header)
        assert sig == 0x04034b50
        data_start = info.header_offset + 30 + fnlen + exlen
        f.seek(data_start)
        entry_data = bytearray(f.read(info.file_size))
        assert zlib.crc32(bytes(entry_data)) & 0xffffffff == crc, "CRC mismatch reading original entry"

    for off, old, new, desc in patches:
        cur = bytes(entry_data[off:off + len(old)])
        if cur == new:
            print(f"  [skip, already patched] {desc}")
            continue
        if cur != old:
            raise SystemExit(f"ERROR: unexpected bytes at {hex(off)} for '{desc}': "
                              f"got {cur.hex()}, expected {old.hex()}. "
                              f"App version likely doesn't match what this script was derived against.")
        entry_data[off:off + len(new)] = new
        print(f"  patched: {desc}")

    new_crc = zlib.crc32(bytes(entry_data)) & 0xffffffff

    with open(apk_path, "r+b") as f:
        f.seek(data_start)
        f.write(bytes(entry_data))
        f.seek(info.header_offset + 14)
        f.write(struct.pack("<I", new_crc))

    with open(apk_path, "rb") as f:
        raw = f.read()
    cd_start = raw.rfind(b"PK\x05\x06")
    eocd = raw[cd_start:cd_start + 22]
    cd_size, cd_offset = struct.unpack("<II", eocd[12:20])
    pos = cd_offset
    found = False
    while pos < cd_offset + cd_size:
        if raw[pos:pos + 4] != b"PK\x01\x02":
            break
        fnlen2, exlen2, colen2 = struct.unpack("<HHH", raw[pos + 28:pos + 34])
        name = raw[pos + 46:pos + 46 + fnlen2].decode("utf-8", "replace")
        rec_len = 46 + fnlen2 + exlen2 + colen2
        if name == ARM64_SO_ENTRY:
            found = True
            with open(apk_path, "r+b") as fo:
                fo.seek(pos + 16)
                fo.write(struct.pack("<I", new_crc))
            break
        pos += rec_len
    if not found:
        raise SystemExit("ERROR: central directory record not found after patching")


def replace_dex_in_apk(apk_path, entry_name, new_dex_bytes):
    """Rebuild apk_path with entry_name's content replaced (size may change,
    so this rewrites the whole zip via the zipfile module rather than an
    in-place splice)."""
    tmp_path = apk_path + ".tmp"
    src = zipfile.ZipFile(apk_path, "r")
    dst = zipfile.ZipFile(tmp_path, "w", allowZip64=True)
    for item in src.infolist():
        data = src.read(item.filename)
        if item.filename == entry_name:
            data = new_dex_bytes
        zi = zipfile.ZipInfo(item.filename, date_time=item.date_time)
        zi.compress_type = item.compress_type
        zi.external_attr = item.external_attr
        zi.create_system = item.create_system
        dst.writestr(zi, data, compress_type=item.compress_type)
    src.close()
    dst.close()
    os.replace(tmp_path, apk_path)


def replace_files_in_apk(apk_path, replacements):
    """replacements: dict of {zip entry name: local file path}. Only entries
    that actually differ get replaced; rewrites the whole zip since sizes
    may change."""
    tmp_path = apk_path + ".tmp"
    src = zipfile.ZipFile(apk_path, "r")
    dst = zipfile.ZipFile(tmp_path, "w", allowZip64=True)
    seen = set()
    changed = 0
    for item in src.infolist():
        data = src.read(item.filename)
        if item.filename in replacements:
            seen.add(item.filename)
            with open(replacements[item.filename], "rb") as f:
                new_data = f.read()
            if new_data != data:
                print(f"  replacing {item.filename}: {len(data)} -> {len(new_data)} bytes")
                changed += 1
            data = new_data
        zi = zipfile.ZipInfo(item.filename, date_time=item.date_time)
        zi.compress_type = item.compress_type
        zi.external_attr = item.external_attr
        zi.create_system = item.create_system
        dst.writestr(zi, data, compress_type=item.compress_type)
    src.close()
    dst.close()
    os.replace(tmp_path, apk_path)
    missing = set(replacements) - seen
    if missing:
        print("  WARNING: these files had no matching zip entry (typo / renamed?):")
        for m in sorted(missing):
            print(f"    {m}")
    return changed


def sign_apk(apk_path, keystore, ks_alias, ks_pass, key_pass):
    aligned = apk_path + ".aligned"
    run([ZIPALIGN, "-f", "-P", "16", "4", apk_path, aligned])
    os.replace(aligned, apk_path)
    run([APKSIGNER, "sign",
         "--ks", keystore,
         "--ks-key-alias", ks_alias,
         "--ks-pass", ks_pass,
         "--key-pass", key_pass or ks_pass,
         apk_path])


def find_base_and_splits(extract_dir):
    manifest_path = os.path.join(extract_dir, "manifest.json")
    with open(manifest_path) as f:
        manifest = json.load(f)
    base_file = None
    split_files = []
    for entry in manifest["split_apks"]:
        if entry["id"] == "base":
            base_file = entry["file"]
        else:
            split_files.append(entry["file"])
    return manifest, base_file, split_files


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("input_xapk")
    p.add_argument("output_xapk")
    p.add_argument("--keystore", default=None,
                    help=f"default: {DEFAULT_KEYSTORE}, if it exists (see generate_keystore.sh)")
    p.add_argument("--ks-alias", default=None, help=f"default: {DEFAULT_KS_ALIAS}")
    p.add_argument("--ks-pass", default=None, help=f"e.g. pass:mypassword (default: {DEFAULT_KS_PASS}, "
                                                     f"matching generate_keystore.sh's default)")
    p.add_argument("--key-pass", default=None, help="defaults to --ks-pass if omitted")
    p.add_argument("-F", "--fps-unlock", action="store_true")
    p.add_argument("-D", "--debug-menu", action="store_true")
    p.add_argument("-R", "--native-res", action="store_true")
    p.add_argument("-T", "--turn-ease", choices=list(TURN_EASE_CURVES), default=None)
    p.add_argument("-L", "--debug-licenses", action="store_true",
                    help="requires -D (or the Debug menu already unlocked) to be reachable")
    p.add_argument("-N", "--no-startup-promo", action="store_true",
                    help="disable the startup 'buy Premium Plus' webview promo dialog "
                         "(fires regardless of license/premium status)")
    p.add_argument("-S", "--skins", default=None, metavar="DIR",
                    help="directory containing assets/res/skin/*.xml overrides "
                         f"(default: {DEFAULT_SKINS_DIR}, used automatically if it exists)")
    p.add_argument("--all", action="store_true",
                    help="enable -F -D -R -N --turn-ease=decelerate (not -L)")
    p.add_argument("--keep-temp", action="store_true")
    args = p.parse_args()

    if args.keystore is None:
        if not os.path.isfile(DEFAULT_KEYSTORE):
            raise SystemExit(f"ERROR: --keystore not given and {DEFAULT_KEYSTORE} doesn't exist here -- "
                              f"run ./generate_keystore.sh first, or pass --keystore explicitly.")
        args.keystore = DEFAULT_KEYSTORE
    if args.ks_alias is None:
        args.ks_alias = DEFAULT_KS_ALIAS

    ks_pass_explicit = args.ks_pass is not None
    if args.ks_pass is None:
        args.ks_pass = DEFAULT_KS_PASS

    skins_explicit = args.skins is not None
    if args.skins is None:
        args.skins = DEFAULT_SKINS_DIR
    skin_dir = os.path.join(args.skins, "assets", "res", "skin")
    skins_available = os.path.isdir(skin_dir)
    if skins_explicit and not skins_available:
        raise SystemExit(f"ERROR: {skin_dir} not found")

    if args.all:
        args.fps_unlock = True
        args.debug_menu = True
        args.native_res = True
        args.no_startup_promo = True
        # debug_licenses deliberately NOT included: the screen it unlocks is
        # permanently empty (its ViewModel is an unfinished stub with no data
        # source), so there's nothing to show for it. Still available via -L
        # explicitly if you want the menu entry to exist anyway.
        if args.turn_ease is None:
            args.turn_ease = "decelerate"

    if args.debug_licenses and not args.debug_menu:
        print("WARNING: -L without -D -- the Licenses item will be patched to appear, but its "
              "parent Debug menu stays hidden unless it's already unlocked in what you're "
              "patching on top of, or you add -D too.", file=sys.stderr)

    if not any([args.fps_unlock, args.debug_menu, args.native_res, args.turn_ease,
                args.debug_licenses, args.no_startup_promo, skins_available]):
        print("Nothing to do -- pick at least one of -F -D -R -T -L -N -S (or --all), or "
              f"run extract_skins.py to populate ./{DEFAULT_SKINS_DIR}. "
              "Will still re-sign with the new cert if you proceed.", file=sys.stderr)

    tmp = tempfile.mkdtemp(prefix="sygic_build_")
    print(f"working dir: {tmp}")
    try:
        extract_dir = os.path.join(tmp, "xapk")
        os.makedirs(extract_dir)
        with zipfile.ZipFile(args.input_xapk) as zf:
            zf.extractall(extract_dir)

        manifest, base_file, split_files = find_base_and_splits(extract_dir)
        base_path = os.path.join(extract_dir, base_file)
        arm64_split = next((f for f in split_files if "arm64_v8a" in f), None)

        # --- native .so patches (arm64 split) ---
        so_patches = []
        if args.fps_unlock:
            so_patches.append(FPS_UNLOCK_PATCH)
        if args.turn_ease:
            so_patches.append((TURN_EASE_OFFSET, TURN_EASE_OLD, turn_ease_new_bytes(args.turn_ease),
                                f"CViewCamera::UpdateRotation curve -> {args.turn_ease}"))
        if so_patches:
            if not arm64_split:
                raise SystemExit("ERROR: no arm64_v8a split found in this xapk")
            print(f"patching {arm64_split} ...")
            patch_so_in_zip(os.path.join(extract_dir, arm64_split), so_patches)

        # --- dex/smali patches (base apk, classes6.dex) ---
        if args.debug_menu or args.native_res or args.debug_licenses:
            print("decompiling classes6.dex ...")
            smali_root = os.path.join(tmp, "smali_c6")
            with zipfile.ZipFile(base_path) as zf:
                dex_bytes = zf.read("classes6.dex")
            dex_path = os.path.join(tmp, "classes6.dex")
            with open(dex_path, "wb") as f:
                f.write(dex_bytes)
            run(["java", "-cp", smali_classpath(BAKSMALI_JAR), "org.jf.baksmali.Main",
                 "disassemble", "-a", "26", "-o", smali_root, dex_path])

            if args.debug_menu:
                patch_smali_regex(smali_root, DEBUG_MENU_SMALI_FILE, DEBUG_MENU_PATTERN, DEBUG_MENU_REPL,
                                   "FEATURE_DEBUG_MENU.isActive() check nop'd out")
            if args.native_res:
                patch_smali_text(smali_root, NATIVE_RES_SMALI_FILE_1, NATIVE_RES_OLD_1, NATIVE_RES_NEW_1,
                                  "LowGL$ViewScaling 1080px cap -> 32767")
                patch_smali_text(smali_root, NATIVE_RES_SMALI_FILE_2, NATIVE_RES_OLD_2, NATIVE_RES_NEW_2,
                                  "GlSurfaceHolderCallback 1080px cap -> 32767")
            if args.debug_licenses:
                patch_smali_regex(smali_root, DEBUG_LICENSES_SMALI_FILE, DEBUG_LICENSES_PATTERN,
                                   DEBUG_LICENSES_REPL,
                                   "SettingItemsManager.J() returns the built Licenses folder instead of null")

            print("reassembling classes6.dex ...")
            new_dex_path = os.path.join(tmp, "classes6_patched.dex")
            run(["java", "-cp", smali_classpath(SMALI_JAR), "org.jf.smali.Main",
                 "assemble", "-a", "26", "-o", new_dex_path, smali_root])
            with open(new_dex_path, "rb") as f:
                new_dex_bytes = f.read()
            replace_dex_in_apk(base_path, "classes6.dex", new_dex_bytes)

        # --- dex/smali patches (base apk, classes2.dex) ---
        if args.no_startup_promo:
            print("decompiling classes2.dex ...")
            smali_root2 = os.path.join(tmp, "smali_c2")
            with zipfile.ZipFile(base_path) as zf:
                dex_bytes = zf.read("classes2.dex")
            dex_path2 = os.path.join(tmp, "classes2.dex")
            with open(dex_path2, "wb") as f:
                f.write(dex_bytes)
            run(["java", "-cp", smali_classpath(BAKSMALI_JAR), "org.jf.baksmali.Main",
                 "disassemble", "-a", "26", "-o", smali_root2, dex_path2])

            patch_promo_dialog(smali_root2)

            print("reassembling classes2.dex ...")
            new_dex_path2 = os.path.join(tmp, "classes2_patched.dex")
            run(["java", "-cp", smali_classpath(SMALI_JAR), "org.jf.smali.Main",
                 "assemble", "-a", "26", "-o", new_dex_path2, smali_root2])
            with open(new_dex_path2, "rb") as f:
                new_dex_bytes2 = f.read()
            replace_dex_in_apk(base_path, "classes2.dex", new_dex_bytes2)

        # --- skin files (base apk, assets/res/skin/*) ---
        if skins_available:
            replacements = {}
            for fname in os.listdir(skin_dir):
                if fname.endswith((".xml", ".json")):
                    replacements[f"assets/res/skin/{fname}"] = os.path.join(skin_dir, fname)
            print(f"applying skin overrides from {args.skins} ({len(replacements)} file(s) tracked) ...")
            changed = replace_files_in_apk(base_path, replacements)
            print(f"  {changed} skin file(s) actually differed and were applied")

        # --- sign everything with the provided cert ---
        key_pass = args.key_pass or args.ks_pass
        all_apks = [base_path] + [os.path.join(extract_dir, f) for f in split_files]
        for apk in all_apks:
            print(f"aligning + signing {os.path.basename(apk)} ...")
            try:
                sign_apk(apk, args.keystore, args.ks_alias, args.ks_pass, key_pass)
            except subprocess.CalledProcessError as e:
                # Only second-guess the password if apksigner itself (not
                # zipalign, which fails for unrelated reasons) was the one
                # that failed, and only if we picked the password by default
                # rather than the caller asking for it specifically.
                if not ks_pass_explicit and e.cmd and e.cmd[0] == APKSIGNER:
                    raise SystemExit(
                        f"\nERROR: signing failed using the default password ({DEFAULT_KS_PASS}) -- "
                        f"your keystore likely uses a different one. Pass it explicitly with "
                        f"--ks-pass pass:<yourpassword>.")
                raise

        # --- fix up manifest.json total_size ---
        icon = manifest.get("icon", "icon.png")
        sized_files = [base_file] + split_files + [icon]
        manifest["total_size"] = sum(os.path.getsize(os.path.join(extract_dir, f)) for f in sized_files)
        manifest_path = os.path.join(extract_dir, "manifest.json")
        with open(manifest_path, "w") as f:
            json.dump(manifest, f, separators=(",", ":"))

        # --- repackage ---
        print(f"packaging {args.output_xapk} ...")
        if os.path.exists(args.output_xapk):
            os.remove(args.output_xapk)
        with zipfile.ZipFile(args.output_xapk, "w", zipfile.ZIP_STORED, allowZip64=True) as zf:
            for f in ["manifest.json", icon, base_file] + split_files:
                zf.write(os.path.join(extract_dir, f), arcname=f)

        print(f"\ndone -> {args.output_xapk}")

    finally:
        if args.keep_temp:
            print(f"(kept temp dir: {tmp})")
        else:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
