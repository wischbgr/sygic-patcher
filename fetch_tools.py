#!/usr/bin/env python3
# /// script
# requires-python = ">=3.9"
# dependencies = []
# ///
"""
Download the external tools build_patched_xapk.py needs (smali/baksmali +
their runtime deps, and Android SDK build-tools' zipalign/apksigner) into
./deps/ -- kept separate from the rest of the repo so it's obvious at a
glance what's tracked source vs. downloaded tooling (and so a single
gitignore entry covers all of it). Lands in the exact locations those
scripts' default tool paths already expect, so after running this once, no
ZIPALIGN/APKSIGNER/SMALI_JAR/BAKSMALI_JAR/SMALI_LIBS_DIR env vars are needed.

Every download is pinned to a specific version and verified against a
checksum hardcoded below before being written into place, so a corrupted or
tampered download is a hard error rather than a silently broken toolchain.

Runs a preflight check first and skips anything already available: zipalign/
apksigner are skipped if the ZIPALIGN/APKSIGNER env vars already point at
existing files, or if both are already resolvable on PATH (e.g. installed
via `sudo apt install zipalign apksigner` on Debian/Ubuntu -- smali/baksmali
have no equivalent apt package, so those are always fetched from Maven
Central unless SMALI_JAR/BAKSMALI_JAR/SMALI_LIBS_DIR already point
somewhere real).

Sources:
  - smali/baksmali 2.5.2 + runtime deps: Maven Central (org.smali:*, plus
    their org.antlr/com.google.guava/com.beust dependencies)
  - Android SDK build-tools 35.0.0: dl.google.com, using the same direct
    per-platform archive URLs and SHA-1 checksums published in Google's own
    repository manifest (repository2-3.xml)

Usage:
  python3 fetch_tools.py [--force]

To bump versions later: update SMALI_VERSION/BUILD_TOOLS_VERSION and the
matching URLs/checksums below (re-derive from Maven Central / Google's
repository2-3.xml the same way).
"""

import argparse
import hashlib
import io
import os
import platform
import shutil
import sys
import urllib.request
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
DEPS_DIR = "deps"
DEPS_ROOT = os.path.join(HERE, DEPS_DIR)

MAVEN_BASE = "https://repo1.maven.org/maven2"

# (maven group path, artifact, version, sha256, destination relative to DEPS_ROOT)
SMALI_VERSION = "2.5.2"
MAVEN_JARS = [
    ("org/smali", "baksmali", SMALI_VERSION,
     "1ed236266d7dc4907aade0b19a34f77efac25342b63c8ace52e579039941b389", "baksmali.jar"),
    ("org/smali", "smali", SMALI_VERSION,
     "136c5c4653d6531bd7b6f10f35f8691cb96432e727d30b4d5579826ee01e9419", "smali.jar"),
    ("org/smali", "dexlib2", SMALI_VERSION,
     "5a5c8982d8bd7d6e3bb1a0713049e3c78b719ec32b20f6b619885cec30a0dd61",
     os.path.join("smalilibs", "dexlib2-2.5.2.jar")),
    ("org/smali", "util", SMALI_VERSION,
     "4f580a9cff3ebb83cb3fd20bec88e37e4f183796ca652732992611620282daea",
     os.path.join("smalilibs", "util-2.5.2.jar")),
    ("org/antlr", "antlr-runtime", "3.5.2",
     "ce3fc8ecb10f39e9a3cddcbb2ce350d272d9cd3d0b1e18e6fe73c3b9389c8734",
     os.path.join("smalilibs", "antlr-runtime-3.5.2.jar")),
    ("com/google/guava", "guava", "27.1-android",
     "686404f2d1d4d221911f96bd627ff60dac2226a5dfa6fb8ba517073eb97ec0ef",
     os.path.join("smalilibs", "guava-27.1-android.jar")),
    ("com/beust", "jcommander", "1.64",
     "156be736199c990321d9ff77090b199629cfc9865e2d6c13f7cd291bb1641817",
     os.path.join("smalilibs", "jcommander-1.64.jar")),
]

# Android SDK build-tools 35.0.0, from Google's repository2-3.xml. The zip's
# internal top-level directory is (for historical reasons) always "android-15"
# regardless of the actual build-tools version -- that's also
# build_patched_xapk.py's default ZIPALIGN/APKSIGNER directory, so extracting
# it as-is lines up with zero extra configuration.
BUILD_TOOLS_VERSION = "35.0.0"
BUILD_TOOLS_ARCHIVES = {
    "linux": ("build-tools_r35_linux.zip", "2cfaa0bbb2336e9ec18ed3ecea84fa2e2af607bc"),
    "windows": ("build-tools_r35_windows.zip", "af059bb67cf7786f45ee0db85e2d24985df1b4b6"),
    "macosx": ("build-tools_r35_macosx.zip", "93ab8ce91230e067b5add4bfa79919c52b27f072"),
}
BUILD_TOOLS_DIR = "android-15"
# Files extracted from the archive, relative to its "android-15/" prefix.
# zipalign on Linux dynamically links against a bundled libc++.so (not the
# system one) via an rpath next to the binary -- macOS/Windows zipalign
# builds link against the OS-provided C++ runtime instead, so they don't
# need an equivalent extra file here (unverified on those platforms, since
# this was only tested on Linux; report an issue if zipalign fails to start
# there and it turns out one is needed).
BUILD_TOOLS_MEMBERS = {
    "linux": ["zipalign", "apksigner", "lib/apksigner.jar", "lib64/libc++.so"],
    "macosx": ["zipalign", "apksigner", "lib/apksigner.jar"],
    "windows": ["zipalign.exe", "apksigner.bat", "lib/apksigner.jar"],
}


def env_path_if_exists(var):
    val = os.environ.get(var)
    return val if val and os.path.exists(val) else None


def host_os():
    system = platform.system()
    if system == "Windows":
        return "windows"
    if system == "Darwin":
        return "macosx"
    return "linux"


def download(url):
    print(f"  downloading {url} ...")
    with urllib.request.urlopen(url) as resp:
        return resp.read()


def sha256_hex(data):
    return hashlib.sha256(data).hexdigest()


def sha1_hex(data):
    return hashlib.sha1(data).hexdigest()


def verify(data, expected_hex, algo, desc):
    actual = algo(data).hexdigest()
    if actual != expected_hex:
        raise SystemExit(f"ERROR: checksum mismatch for {desc}\n"
                          f"  expected: {expected_hex}\n"
                          f"  got:      {actual}\n"
                          f"Download may be corrupted or tampered with -- not writing it.")


# Standalone jars that have their own env var override (the smalilibs/ group
# only has one collective override, SMALI_LIBS_DIR, handled separately below).
MAVEN_JAR_ENV_VARS = {"baksmali.jar": "BAKSMALI_JAR", "smali.jar": "SMALI_JAR"}


def fetch_maven_jars(force):
    smali_libs_override = None if force else env_path_if_exists("SMALI_LIBS_DIR")
    for group, artifact, version, sha256, rel_dest in MAVEN_JARS:
        is_smalilibs = rel_dest.startswith("smalilibs" + os.sep)
        if not force:
            if is_smalilibs and smali_libs_override:
                print(f"  [skip, SMALI_LIBS_DIR={smali_libs_override} already set] {rel_dest}")
                continue
            env_var = MAVEN_JAR_ENV_VARS.get(rel_dest)
            if env_var:
                env_val = env_path_if_exists(env_var)
                if env_val:
                    print(f"  [skip, {env_var}={env_val} already set] {rel_dest}")
                    continue
        dest = os.path.join(DEPS_ROOT, rel_dest)
        rel_print = os.path.join(DEPS_DIR, rel_dest)
        if os.path.exists(dest) and not force:
            print(f"  [skip, exists] {rel_print}")
            continue
        url = f"{MAVEN_BASE}/{group}/{artifact}/{version}/{artifact}-{version}.jar"
        data = download(url)
        verify(data, sha256, hashlib.sha256, rel_print)
        os.makedirs(os.path.dirname(dest) or ".", exist_ok=True)
        with open(dest, "wb") as f:
            f.write(data)
        print(f"  wrote: {rel_print}")


def fetch_build_tools(force):
    if not force:
        zipalign_env = env_path_if_exists("ZIPALIGN")
        apksigner_env = env_path_if_exists("APKSIGNER")
        if zipalign_env and apksigner_env:
            print(f"  [skip, ZIPALIGN={zipalign_env} / APKSIGNER={apksigner_env} already set]")
            return
        zipalign_path = zipalign_env or shutil.which("zipalign")
        apksigner_path = apksigner_env or shutil.which("apksigner")
        if zipalign_path and apksigner_path:
            print(f"  [skip, found on PATH] zipalign ({zipalign_path}), apksigner ({apksigner_path})")
            return

    os_name = host_os()
    archive_name, sha1 = BUILD_TOOLS_ARCHIVES[os_name]
    members = BUILD_TOOLS_MEMBERS[os_name]
    dest_paths = [os.path.join(DEPS_ROOT, BUILD_TOOLS_DIR, m) for m in members]

    if all(os.path.exists(p) for p in dest_paths) and not force:
        print(f"  [skip, exists] {DEPS_DIR}/{BUILD_TOOLS_DIR}/ ({os_name})")
        return

    if os_name == "linux":
        print("  (tip: on Debian/Ubuntu, 'sudo apt install zipalign apksigner' avoids this "
              "download entirely and gets picked up automatically next time)")

    url = f"https://dl.google.com/android/repository/{archive_name}"
    data = download(url)
    verify(data, sha1, hashlib.sha1, archive_name)

    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        for member, dest in zip(members, dest_paths):
            entry = f"{BUILD_TOOLS_DIR}/{member}"
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            with zf.open(entry) as src, open(dest, "wb") as out:
                out.write(src.read())
            if not dest.endswith((".jar", ".bat")):
                os.chmod(dest, 0o755)
            print(f"  wrote: {os.path.relpath(dest, HERE)}")


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--force", action="store_true", help="re-download and overwrite files that already exist")
    args = p.parse_args()

    print(f"smali/baksmali {SMALI_VERSION} (Maven Central) ...")
    fetch_maven_jars(args.force)

    print(f"Android SDK build-tools {BUILD_TOOLS_VERSION} ({host_os()}, dl.google.com) ...")
    fetch_build_tools(args.force)

    print("\ndone -- build_patched_xapk.py's default tool paths should now resolve with no env vars needed.")


if __name__ == "__main__":
    sys.exit(main())
