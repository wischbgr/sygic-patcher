#!/usr/bin/env python3
# /// script
# requires-python = ">=3.9"
# dependencies = []
# ///
"""
Build a patched, signed Sygic .xapk from the original. See README.md for
the full picture; flags below cover what each patch does.

All patches were derived against com.sygic.aura 26.4.2-115708 (arm64-v8a) and
are version-pinned: every binary/smali patch asserts the expected original
bytes/text before writing anything and aborts loudly on a mismatch, so a
different app version fails safely instead of producing a broken build. If
Sygic ships an update, the constants near the top of this file are what need
re-deriving.
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

# ---------------------------------------------------------------------------
# Output helpers -- [+] a step/action, [i] informational, [-] error/warning
# ---------------------------------------------------------------------------

def step(msg):
    """Announce the start of a top-level step."""
    print(f"\n[+] {msg}")


def ok(msg, indent=1):
    """A completed sub-action within the current step."""
    print("    " * indent + f"[+] {msg}")


def info(msg, indent=0, file=None):
    """Informational output -- skips, summaries, tips."""
    print("    " * indent + f"[i] {msg}", file=file)


def warn(msg, file=None):
    """A non-fatal caution."""
    print(f"[-] WARNING: {msg}", file=file)


def err(msg):
    """Abort with an error message."""
    raise SystemExit(f"[-] {msg}")


class PatchFailed(Exception):
    """A specific patch couldn't find what it needed to change (version
    mismatch) -- distinct from err()'s hard SystemExit so the caller can
    offer to skip just this one patch instead of aborting the whole build."""


def patch_fail(msg):
    raise PatchFailed(msg)


def confirm_skip(reason):
    """Ask whether to skip a failed patch and keep going. Declines (without
    asking) when not running interactively -- silently skipping a patch is
    not a safe default."""
    warn(reason)
    if not sys.stdin.isatty():
        return False
    try:
        reply = input("    Skip this patch and continue? [y/N] ")
    except EOFError:
        return False
    return reply.strip().lower().startswith("y")


def try_patch(fn, *args, **kwargs):
    """Call a patch function; if it raises PatchFailed, offer to skip just
    this one patch instead of aborting the whole build."""
    try:
        fn(*args, **kwargs)
    except PatchFailed as e:
        if confirm_skip(str(e)):
            info("Skipped -- continuing without this patch.", indent=1)
        else:
            err(str(e))


VERBOSE = False

BANNER = r"""
 SSSS Y   Y  GGG  III  CCC        PPPP    A   TTTTT  CCC  H   H EEEEE RRRR
S      Y Y  G      I  C   C       P   P  A A    T   C   C H   H E     R   R
 SSS    Y   G GG   I  C           PPPP  AAAAA   T   C     HHHHH EEEE  RRRR
    S   Y   G   G  I  C   C       P     A   A   T   C   C H   H E     R  R
SSSS    Y    GGG  III  CCC        P     A   A   T    CCC  H   H EEEEE R   R
"""

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

# -10.0f is just a generic float bit pattern -- it (and, worse, "mov w1, wzr"
# below) recurs dozens to thousands of times across the whole library, so
# every raw .so patch is located purely by searching for old_bytes (or
# new_bytes, to detect already-patched) plus 16 bytes of surrounding context
# on each side -- verified, against several real app versions, to be enough
# margin to stay unique without reaching so far as to pick up unrelated
# version drift. No fixed offset is used even as a first try: which byte
# offset something ends up at isn't any more stable across app versions than
# which classesN.dex a class ends up in (see the dex/smali patches below),
# so a hardcoded one wouldn't save anything reliable -- only a comment noting
# where it originally was, for reference.
# Originally at 0x1684c60 in 26.4.2-115708.
FPS_UNLOCK_CONTEXT_BEFORE = bytes.fromhex("010000000001000000000000c80a15ff")
FPS_UNLOCK_CONTEXT_AFTER = bytes.fromhex("00004843070000001100000020000000")
FPS_UNLOCK_PATCH = (
    FPS_UNLOCK_CONTEXT_BEFORE,
    struct.pack("<f", -10.0),
    FPS_UNLOCK_CONTEXT_AFTER,
    struct.pack("<f", 120.0),
    "CSDKMapView fps-override default -10.0 -> +120.0 (BALANCED -> PERFORMANCE)",
)

# Originally at 0x2a8f770 in 26.4.2-115708. No context_after: the 16 bytes
# immediately following "mov w1, wzr" are a `bl <function>` -- a PC-relative
# call whose encoded bytes shift whenever the called function moves anywhere
# else in the binary, which happens on basically every real build even when
# this instruction itself doesn't change at all (confirmed: byte-identical
# across 26.4.2/26.4.1/26.3.2/26.2.0). Including it as context was actively
# counterproductive -- it broke the match on older builds for a reason
# unrelated to the actual patch target. context_before alone is already
# unique in all four versions tested, so there's nothing to gain from an
# after-context here.
TURN_EASE_CONTEXT_BEFORE = bytes.fromhex("09030054966245b9e8230091e00314aa")
TURN_EASE_CONTEXT_AFTER = b""
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
# The enclosing enum class (Hx5 as of 26.4.2, Fx5 as of 26.4.1) is R8-assigned
# and drifts across builds even when nothing about the feature itself changed
# -- match it as a wildcard (backreferenced so both mentions agree) instead of
# hardcoding one version's name, keying only on the stable field name.
DEBUG_MENU_PATTERN = re.compile(
    r"(sget-object v1, L([\w/$]+);->FEATURE_DEBUG_MENU:L\2;.*?)"
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
# Same wildcard-class treatment as DEBUG_MENU_PATTERN above: the enclosing
# enum class (vt1 as of 26.4.2) is R8-assigned and drifts across builds.
DEBUG_LICENSES_PATTERN = re.compile(
    r"(sget-object v1, L([\w/$]+);->DebugLicenses:L\2;.*?)"
    r"const/4 v0, 0x0\n\n    \.line 49\n    return-object v0",
    re.DOTALL,
)
DEBUG_LICENSES_REPL = "\\1nop\n\n    .line 49\n    return-object v0"

# ModalManagerImpl.checkShowPromoDialog() (classes2.dex) fires the startup
# "buy Premium Plus" webview promo purely on session count + connectivity +
# a remote LifetimeMonetizationFeature flag -- it has no license/premium
# check at all, so it nags lifetime/premium owners exactly like free users.
# Force it onto the same early-return "nothing to show" path the method
# already takes when promoChecked/session-count/connectivity fail, by
# jumping straight to that path's label from method entry. The label name
# is looked up dynamically (last label in the method body) instead of
# hardcoded, since baksmali's per-run numbering isn't guaranteed stable
# across decompile contexts.
#
# Both the enclosing class (uX2 as of 26.4.2) and the method's own
# obfuscated name (D0) are R8-assigned and can drift between builds even
# when the method itself didn't change -- observed exactly that between
# 26.4.2 (uX2.D0) and 26.4.1 (sX2.D0). Rather than hardcode either, this
# finds the method by a debug log string baked into its body that spells
# out its real name in plain text and isn't touched by obfuscation. Keep
# this to the stable prefix only -- older builds (e.g. 26.2.0) log just
# "checkShowPromoDialog() called" without the later-added "- paywall" suffix.
PROMO_DIALOG_LOG_STRING = "checkShowPromoDialog() called"
PROMO_DIALOG_METHOD_PATTERN = re.compile(
    r"(\.method private static final \w+\(L[\w/$]+;\)Lio/reactivex/SingleSource;\n"
    r"    \.registers \d+\n\n)(.*?)(\n\.end method)",
    re.DOTALL,
)


def _find_promo_dialog_file(root):
    """Locate the smali file implementing checkShowPromoDialog() by its
    distinctive debug-log string rather than a hardcoded (R8-obfuscated,
    version-drifting) class name."""
    matches = []
    for dirpath, _dirnames, filenames in os.walk(root):
        for fname in filenames:
            if not fname.endswith(".smali"):
                continue
            path = os.path.join(dirpath, fname)
            with open(path, "r") as f:
                if PROMO_DIALOG_LOG_STRING in f.read():
                    matches.append(path)
    if len(matches) == 1:
        return matches[0]
    if not matches:
        patch_fail(f"No smali file contains the checkShowPromoDialog() debug string "
                   f"('{PROMO_DIALOG_LOG_STRING}') -- "
                   f"app version likely doesn't match what this script was derived against.")
    patch_fail(f"Ambiguous: {len(matches)} smali files contain the checkShowPromoDialog() debug string -- "
               f"can't safely pick one ({', '.join(os.path.relpath(p, root) for p in matches)}).")


def patch_promo_dialog(root):
    path = _find_promo_dialog_file(root)
    with open(path, "r") as f:
        content = f.read()
    m = None
    for candidate in PROMO_DIALOG_METHOD_PATTERN.finditer(content):
        if PROMO_DIALOG_LOG_STRING in candidate.group(2):
            m = candidate
            break
    if not m:
        patch_fail(f"checkShowPromoDialog() found in {os.path.relpath(path, root)} via its debug string, "
                   f"but not in the expected method shape -- "
                   f"app version likely doesn't match what this script was derived against.")
    head, body, tail = m.group(1), m.group(2), m.group(3)
    if body.startswith("goto/16 :"):
        info("Already patched: startup promo webview dialog disabled", indent=1)
        return
    labels = re.findall(r"^    :(\w+)$", body, re.MULTILINE)
    if not labels:
        patch_fail("No labels found in checkShowPromoDialog() -- "
                   "app version likely doesn't match what this script was derived against.")
    target = labels[-1]
    new_content = content[:m.start()] + head + f"goto/16 :{target}\n\n" + body + tail + content[m.end():]
    with open(path, "w") as f:
        f.write(new_content)
    ok(f"Patched: startup promo webview dialog ('buy Premium Plus' popup) disabled (-> :{target})")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def run(cmd, **kw):
    if VERBOSE:
        ok("$ " + " ".join(cmd))
    subprocess.run(cmd, check=True, **kw)


def _list_dex_entries(base_path):
    """All classes*.dex entries in the base APK, in whatever order the zip
    lists them (order doesn't matter -- callers search all of them)."""
    with zipfile.ZipFile(base_path) as zf:
        return [n for n in zf.namelist() if re.fullmatch(r"classes\d*\.dex", n)]


def _get_smali_root(tmp, base_path, dex_name, decompiled):
    """Decompile dex_name into tmp/, caching so multiple patches landing in
    the same dex file don't redundantly re-decompile it."""
    if dex_name in decompiled:
        return decompiled[dex_name]
    with zipfile.ZipFile(base_path) as zf:
        dex_bytes = zf.read(dex_name)
    dex_path = os.path.join(tmp, dex_name)
    with open(dex_path, "wb") as f:
        f.write(dex_bytes)
    smali_root = os.path.join(tmp, "smali_" + dex_name.replace(".", "_"))
    step(f"Decompiling {dex_name}")
    run(["java", "-cp", smali_classpath(BAKSMALI_JAR), "org.jf.baksmali.Main",
         "disassemble", "-a", "26", "-o", smali_root, dex_path])
    decompiled[dex_name] = smali_root
    return smali_root


def _find_defining_dex(base_path, dex_names, tmp, decompiled, rel_path):
    """Find which classes*.dex actually DEFINES the class at rel_path (e.g.
    "com/sygic/navi/settings2/d.smali") -- which dex a given class ends up
    in isn't stable across app versions (multidex sharding shifts a class
    to a different classesN.dex from one release to the next even when the
    class itself hasn't changed), so this can't be assumed the way a single
    hardcoded dex name could.

    A cheap raw-byte scan of each dex's own bytes for the class descriptor
    first narrows down candidates -- but a class can be *referenced* (via a
    method signature, field type, etc.) from a dex that doesn't *define*
    it, so each candidate then gets decompiled (cached) and checked for the
    file actually materializing there: baksmali only produces a .smali file
    for classes actually defined in that dex, never for bare references."""
    descriptor = ("L" + rel_path[:-len(".smali")] + ";").encode()
    candidates = []
    with zipfile.ZipFile(base_path) as zf:
        for dex_name in dex_names:
            if descriptor in zf.read(dex_name):
                candidates.append(dex_name)
    defining = [dex_name for dex_name in candidates
                if os.path.isfile(os.path.join(_get_smali_root(tmp, base_path, dex_name, decompiled), rel_path))]
    if len(defining) == 1:
        return defining[0]
    if not defining:
        patch_fail(f"Class {rel_path} not found (defined) in any classes*.dex -- "
                   f"app version likely doesn't match what this script was derived against.")
    patch_fail(f"Ambiguous: class {rel_path} is defined in {len(defining)} dex files "
               f"({', '.join(defining)}) -- can't safely pick one.")


def _find_dex_for_promo_dialog(base_path, dex_names, tmp, decompiled):
    """Same idea as _find_defining_dex(), but for the promo-dialog patch,
    which has no fixed rel_path to key on (see PROMO_DIALOG_LOG_STRING) --
    find which classes*.dex contains that debug string instead. Dex string
    pools aren't shared across dex files the way type references are, so a
    plain raw-byte scan is enough here without an extra materialization
    check."""
    needle = PROMO_DIALOG_LOG_STRING.encode()
    matches = []
    with zipfile.ZipFile(base_path) as zf:
        for dex_name in dex_names:
            if needle in zf.read(dex_name):
                matches.append(dex_name)
    if len(matches) == 1:
        _get_smali_root(tmp, base_path, matches[0], decompiled)
        return matches[0]
    if not matches:
        patch_fail(f"No classes*.dex contains the checkShowPromoDialog() debug string "
                   f"('{PROMO_DIALOG_LOG_STRING}') -- "
                   f"app version likely doesn't match what this script was derived against.")
    patch_fail(f"Ambiguous: the checkShowPromoDialog() debug string appears in {len(matches)} dex files "
               f"({', '.join(matches)}) -- can't safely pick one.")


def _read_smali_file(root, rel_path, desc):
    """Read a smali file, turning a missing file (e.g. the class got
    renamed, merged, or dropped entirely in a different app version) into a
    recoverable PatchFailed instead of an uncaught FileNotFoundError that
    would crash past try_patch()'s skip-and-continue prompt."""
    path = os.path.join(root, rel_path)
    try:
        with open(path, "r") as f:
            return path, f.read()
    except FileNotFoundError:
        patch_fail(f"Expected smali file not found for '{desc}': {rel_path} -- "
                   f"app version likely doesn't match what this script was derived against.")


def patch_smali_text(root, rel_path, old, new, desc):
    path, content = _read_smali_file(root, rel_path, desc)
    if new in content and old not in content:
        info(f"Already patched: {desc}", indent=1)
        return
    if old not in content:
        patch_fail(f"Expected smali text not found for '{desc}' in {rel_path} -- "
                   f"app version likely doesn't match what this script was derived against.")
    content = content.replace(old, new, 1)
    with open(path, "w") as f:
        f.write(content)
    ok(f"Patched: {desc}")


def patch_smali_regex(root, rel_path, pattern, repl, desc):
    path, content = _read_smali_file(root, rel_path, desc)
    new_content, n = pattern.subn(repl, content, count=1)
    if n == 0:
        patch_fail(f"Expected smali pattern not found for '{desc}' in {rel_path} -- "
                   f"app version likely doesn't match what this script was derived against.")
    with open(path, "w") as f:
        f.write(new_content)
    ok(f"Patched: {desc}")


def _find_all(data, needle):
    matches = []
    start = 0
    while True:
        idx = data.find(needle, start)
        if idx == -1:
            return matches
        matches.append(idx)
        start = idx + 1


def _locate_so_patch(entry_data, ctx_before, old, ctx_after, new, desc):
    """Find where to apply this patch by searching the whole .so for
    old_bytes (or new_bytes, to detect an already-patched build) plus
    surrounding context -- never a fixed offset, since the bare bytes alone
    are often way too generic (a float constant, a one-instruction "zero a
    register" idiom) to search for on their own; they recur constantly
    throughout a multi-MB binary, and the context is what makes a unique
    match possible at all. Only trusts a unique match; a collision would
    silently patch the wrong spot. Returns None if already patched, else the
    offset to write `new` at. Raises PatchFailed (recoverable -- caller may
    offer to skip) rather than aborting directly."""
    new_matches = _find_all(entry_data, ctx_before + new + ctx_after)
    if len(new_matches) == 1:
        return None
    if len(new_matches) > 1:
        patch_fail(f"Ambiguous: '{desc}' already-patched bytes found at {len(new_matches)} places -- "
                   f"can't safely tell whether this is already patched.")

    old_matches = _find_all(entry_data, ctx_before + old + ctx_after)
    if len(old_matches) == 1:
        return old_matches[0] + len(ctx_before)
    if len(old_matches) > 1:
        shown = ", ".join(hex(m + len(ctx_before)) for m in old_matches[:10])
        more = f", and {len(old_matches) - 10} more" if len(old_matches) > 10 else ""
        patch_fail(f"Ambiguous search for '{desc}': {len(old_matches)} candidate offsets found "
                   f"({shown}{more}) -- can't safely pick one. "
                   f"App version likely doesn't match what this script was derived against.")
    patch_fail(f"'{desc}' not found -- app version likely doesn't match what this script was derived against.")


def patch_so_in_zip(apk_path, patches):
    """Apply a list of (context_before, old_bytes, context_after, new_bytes,
    desc) raw patches to lib/arm64-v8a/libsygic.so inside apk_path, in
    place. Entry must be STORED (uncompressed), which it is for this app
    (extractNativeLibs=false)."""
    zf = zipfile.ZipFile(apk_path, "r")
    zinfo = zf.getinfo(ARM64_SO_ENTRY)
    if zinfo.compress_type != zipfile.ZIP_STORED:
        err(f"Entry {ARM64_SO_ENTRY} is not STORED (compress_type={zinfo.compress_type}); "
            f"raw offset patch assumptions no longer hold.")
    with open(apk_path, "rb") as f:
        f.seek(zinfo.header_offset)
        local_header = f.read(30)
        sig, ver, flag, method, mtime, mdate, crc, csize, usize, fnlen, exlen = struct.unpack(
            "<IHHHHHIIIHH", local_header)
        assert sig == 0x04034b50
        data_start = zinfo.header_offset + 30 + fnlen + exlen
        f.seek(data_start)
        entry_data = bytearray(f.read(zinfo.file_size))
        assert zlib.crc32(bytes(entry_data)) & 0xffffffff == crc, "CRC mismatch reading original entry"

    for ctx_before, old, ctx_after, new, desc in patches:
        try:
            off = _locate_so_patch(entry_data, ctx_before, old, ctx_after, new, desc)
        except PatchFailed as e:
            if confirm_skip(str(e)):
                info(f"Skipped: {desc}", indent=1)
                continue
            err(str(e))
        if off is None:
            info(f"Already patched: {desc}", indent=1)
            continue
        entry_data[off:off + len(new)] = new
        ok(f"Patched: {desc}")

    new_crc = zlib.crc32(bytes(entry_data)) & 0xffffffff

    with open(apk_path, "r+b") as f:
        f.seek(data_start)
        f.write(bytes(entry_data))
        f.seek(zinfo.header_offset + 14)
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
        err("Central directory record not found after patching")


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
                ok(f"Replacing {item.filename}: {len(data)} -> {len(new_data)} bytes")
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
        warn("These files had no matching zip entry (typo / renamed?): " + ", ".join(sorted(missing)))
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
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("input_xapk")
    p.add_argument("output_xapk")
    p.add_argument("--keystore", default=None,
                    help=f"default: {DEFAULT_KEYSTORE}, if it exists (see generate_keystore.sh)")
    p.add_argument("--ks-alias", default=None, help=f"default: {DEFAULT_KS_ALIAS}")
    p.add_argument("--ks-pass", default=None, help=f"e.g. pass:mypassword (default: {DEFAULT_KS_PASS}, "
                                                     f"matching generate_keystore.sh's default)")
    p.add_argument("--key-pass", default=None, help="defaults to --ks-pass if omitted")
    p.add_argument("-F", "--fps-unlock", action="store_true",
                    help="libsygic.so: raise the map renderer's FPS-override cap from -10.0 to "
                         "+120.0, switching from throttled BALANCED mode to PERFORMANCE")
    p.add_argument("-D", "--debug-menu", action="store_true",
                    help="classes6.dex: unconditionally expose the internal "
                         "Debug/DevSettings/DevActions/Features/UiKit menu")
    p.add_argument("-R", "--native-res", action="store_true",
                    help="classes6.dex: raise the map's 1080px render-surface cap to 32767, so it "
                         "renders at true device resolution instead of being upscaled")
    p.add_argument("-T", "--turn-ease", choices=list(TURN_EASE_CURVES), default=None,
                    help="libsygic.so: navigation-follow camera rotation easing curve "
                         "(unpatched default is a hardcoded linear ease)")
    p.add_argument("-L", "--debug-licenses", action="store_true",
                    help="requires -D (or the Debug menu already unlocked) to be reachable")
    p.add_argument("-N", "--no-startup-promo", action="store_true",
                    help="disable the startup 'buy Premium Plus' webview promo dialog "
                         "(fires regardless of license/premium status)")
    p.add_argument("-S", "--skins", default=None, metavar="DIR",
                    help="directory containing assets/res/skin/*.xml overrides "
                         f"(default: {DEFAULT_SKINS_DIR}, used automatically if it exists)")
    p.add_argument("--all", action="store_true",
                    help="enable -F -D -R -N --turn-ease=decelerate")
    p.add_argument("--keep-temp", action="store_true")
    p.add_argument("-v", "--verbose", action="store_true",
                    help="print the full external commands (java, zipalign, apksigner) as they run")
    args = p.parse_args()

    print(BANNER)

    global VERBOSE
    VERBOSE = args.verbose

    if args.keystore is None:
        if not os.path.isfile(DEFAULT_KEYSTORE):
            err(f"No --keystore given and {DEFAULT_KEYSTORE} doesn't exist here -- "
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
        err(f"Directory {skin_dir} not found")

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
        warn("Using -L without -D -- the Licenses item will be patched to appear, but its parent Debug menu "
             "stays hidden unless it's already unlocked in what you're patching on top of, or you add -D too.",
             file=sys.stderr)

    if not any([args.fps_unlock, args.debug_menu, args.native_res, args.turn_ease,
                args.debug_licenses, args.no_startup_promo, skins_available]):
        info(f"Nothing to do -- pick at least one of -F -D -R -T -L -N -S (or --all), or run extract_skins.py "
             f"to populate ./{DEFAULT_SKINS_DIR}. Will still re-sign with the new cert if you proceed.",
             file=sys.stderr)

    build_tmp_root = os.path.join(HERE, "build", "tmp")
    os.makedirs(build_tmp_root, exist_ok=True)
    tmp = tempfile.mkdtemp(prefix="sygic_build_", dir=build_tmp_root)
    #info(f"Working dir: {tmp}")
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
            so_patches.append((TURN_EASE_CONTEXT_BEFORE, TURN_EASE_OLD, TURN_EASE_CONTEXT_AFTER,
                                turn_ease_new_bytes(args.turn_ease),
                                f"CViewCamera::UpdateRotation curve -> {args.turn_ease}"))
        if so_patches:
            if not arm64_split:
                err("No arm64_v8a split found in this xapk")
            step(f"Patching {arm64_split}")
            patch_so_in_zip(os.path.join(extract_dir, arm64_split), so_patches)

        # --- dex/smali patches -- which classes*.dex a given class/method
        # ends up in isn't stable across app versions (multidex sharding
        # shifts things around), so nothing here assumes a fixed dex name.
        # Each patch is independently resolved to whichever dex actually
        # defines its target, decompiled on demand (cached, so patches
        # landing in the same dex don't redundantly re-decompile it), and
        # only the dex file(s) that actually got touched are reassembled.
        if args.debug_menu or args.native_res or args.debug_licenses or args.no_startup_promo:
            dex_names = _list_dex_entries(base_path)
            decompiled = {}
            modified_dex = set()

            def dex_patch(rel_path, patch_fn, *patch_args, promo=False):
                if promo:
                    dex_name = _find_dex_for_promo_dialog(base_path, dex_names, tmp, decompiled)
                else:
                    dex_name = _find_defining_dex(base_path, dex_names, tmp, decompiled, rel_path)
                patch_fn(decompiled[dex_name], *patch_args)
                modified_dex.add(dex_name)

            if args.debug_menu:
                try_patch(dex_patch, DEBUG_MENU_SMALI_FILE,
                          patch_smali_regex, DEBUG_MENU_SMALI_FILE, DEBUG_MENU_PATTERN, DEBUG_MENU_REPL,
                          "FEATURE_DEBUG_MENU.isActive() check nop'd out")
            if args.native_res:
                try_patch(dex_patch, NATIVE_RES_SMALI_FILE_1,
                          patch_smali_text, NATIVE_RES_SMALI_FILE_1, NATIVE_RES_OLD_1, NATIVE_RES_NEW_1,
                          "LowGL$ViewScaling 1080px cap -> 32767")
                try_patch(dex_patch, NATIVE_RES_SMALI_FILE_2,
                          patch_smali_text, NATIVE_RES_SMALI_FILE_2, NATIVE_RES_OLD_2, NATIVE_RES_NEW_2,
                          "GlSurfaceHolderCallback 1080px cap -> 32767")
            if args.debug_licenses:
                try_patch(dex_patch, DEBUG_LICENSES_SMALI_FILE,
                          patch_smali_regex, DEBUG_LICENSES_SMALI_FILE, DEBUG_LICENSES_PATTERN, DEBUG_LICENSES_REPL,
                          "SettingItemsManager.J() returns the built Licenses folder instead of null")
            if args.no_startup_promo:
                try_patch(dex_patch, None, patch_promo_dialog, promo=True)

            for dex_name in sorted(modified_dex):
                step(f"Reassembling {dex_name}")
                new_dex_path = os.path.join(tmp, dex_name.replace(".dex", "_patched.dex"))
                run(["java", "-cp", smali_classpath(SMALI_JAR), "org.jf.smali.Main",
                     "assemble", "-a", "26", "-o", new_dex_path, decompiled[dex_name]])
                with open(new_dex_path, "rb") as f:
                    new_dex_bytes = f.read()
                replace_dex_in_apk(base_path, dex_name, new_dex_bytes)

        # --- skin files (base apk, assets/res/skin/*) ---
        if skins_available:
            replacements = {}
            for fname in os.listdir(skin_dir):
                if fname.endswith((".xml", ".json")):
                    replacements[f"assets/res/skin/{fname}"] = os.path.join(skin_dir, fname)
            step(f"Applying skin overrides from {args.skins} ({len(replacements)} file(s) tracked)")
            changed = replace_files_in_apk(base_path, replacements)
            info(f"Applied {changed} skin file(s) that actually differed", indent=1)

        # --- sign everything with the provided cert ---
        key_pass = args.key_pass or args.ks_pass
        all_apks = [base_path] + [os.path.join(extract_dir, f) for f in split_files]
        for apk in all_apks:
            step(f"Aligning + signing {os.path.basename(apk)}")
            try:
                sign_apk(apk, args.keystore, args.ks_alias, args.ks_pass, key_pass)
            except subprocess.CalledProcessError as e:
                # Only second-guess the password if apksigner itself (not
                # zipalign, which fails for unrelated reasons) was the one
                # that failed, and only if we picked the password by default
                # rather than the caller asking for it specifically.
                if not ks_pass_explicit and e.cmd and e.cmd[0] == APKSIGNER:
                    err(f"Signing failed using the default password ({DEFAULT_KS_PASS}) -- your keystore likely "
                        f"uses a different one. Pass it explicitly with --ks-pass pass:<yourpassword>.")
                raise

        # --- fix up manifest.json total_size ---
        icon = manifest.get("icon", "icon.png")
        sized_files = [base_file] + split_files + [icon]
        manifest["total_size"] = sum(os.path.getsize(os.path.join(extract_dir, f)) for f in sized_files)
        manifest_path = os.path.join(extract_dir, "manifest.json")
        with open(manifest_path, "w") as f:
            json.dump(manifest, f, separators=(",", ":"))

        # --- repackage ---
        step(f"Packaging {args.output_xapk}")
        if os.path.exists(args.output_xapk):
            os.remove(args.output_xapk)
        with zipfile.ZipFile(args.output_xapk, "w", zipfile.ZIP_STORED, allowZip64=True) as zf:
            for f in ["manifest.json", icon, base_file] + split_files:
                zf.write(os.path.join(extract_dir, f), arcname=f)

        step(f"Patching finished successfully! Have a good ride :)")

    finally:
        if args.keep_temp:
            info(f"Kept temp dir: {tmp}")
        else:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
