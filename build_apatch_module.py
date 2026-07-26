#!/usr/bin/env python3
# /// script
# requires-python = ">=3.9"
# dependencies = []
# ///
"""
Package a patched .xapk (the output of build_patched_xapk.py) into an
APatch module zip, ready to flash: extracts the patched base APK and
arm64_v8a split, renames the split to what module/service.sh expects, and
bundles them with module/module.prop + module/service.sh into a single zip.

This automates what the README used to describe as a manual copy-rename-zip
process -- see the "APatch module" section there for what the module
actually does at runtime (bind-mounts these APKs over the live install
paths at boot, leaving the original Play Store install/signature intact).

Usage:
  python3 build_apatch_module.py PATCHED.xapk [OUTPUT.zip]

Example:
  python3 build_patched_xapk.py Sygic.xapk Sygic_patched.xapk --all
  python3 build_apatch_module.py Sygic_patched.xapk
  # -> Sygic_patched_apatch.zip, ready to flash via APatch's module manager
"""

import argparse
import json
import os
import sys
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_MODULE_DIR = os.path.join(HERE, "module")
MODULE_PROP = "module.prop"
SERVICE_SH = "service.sh"
BASE_APK_ARCNAME = "com.sygic.aura.apk"
ARM64_SPLIT_ARCNAME = "split_config.arm64_v8a.apk"


def step(msg):
    print(f"\n[+] {msg}")


def ok(msg, indent=1):
    print("    " * indent + f"[+] {msg}")


def info(msg, indent=0):
    print("    " * indent + f"[i] {msg}")


def err(msg):
    raise SystemExit(f"[-] {msg}")


def find_base_and_arm64(xapk_path):
    with zipfile.ZipFile(xapk_path) as zf:
        manifest = json.loads(zf.read("manifest.json"))
        base_file = None
        arm64_file = None
        for entry in manifest["split_apks"]:
            if entry["id"] == "base":
                base_file = entry["file"]
            elif entry["id"] == "config.arm64_v8a":
                arm64_file = entry["file"]
        if not base_file:
            err(f"No base APK entry found in {xapk_path}'s manifest.json")
        if not arm64_file:
            err(f"No arm64_v8a split found in {xapk_path} -- this module only supports arm64-v8a devices")
        return zf.read(base_file), zf.read(arm64_file)


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("patched_xapk", help="output of build_patched_xapk.py")
    p.add_argument("output_zip", nargs="?", default=None,
                    help="default: <patched_xapk name>_apatch.zip")
    p.add_argument("--module-dir", default=DEFAULT_MODULE_DIR,
                    help=f"directory containing module.prop/service.sh (default: {DEFAULT_MODULE_DIR})")
    args = p.parse_args()

    output_zip = args.output_zip or os.path.splitext(args.patched_xapk)[0] + "_apatch.zip"

    module_prop_path = os.path.join(args.module_dir, MODULE_PROP)
    service_sh_path = os.path.join(args.module_dir, SERVICE_SH)
    if not os.path.isfile(module_prop_path):
        err(f"{module_prop_path} not found")
    if not os.path.isfile(service_sh_path):
        err(f"{service_sh_path} not found")

    step(f"Reading {args.patched_xapk}")
    base_bytes, arm64_bytes = find_base_and_arm64(args.patched_xapk)
    ok(f"Base APK: {len(base_bytes):,} bytes")
    ok(f"arm64_v8a split: {len(arm64_bytes):,} bytes")

    step(f"Packaging {output_zip}")
    if os.path.exists(output_zip):
        os.remove(output_zip)
    with zipfile.ZipFile(output_zip, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.write(module_prop_path, arcname=MODULE_PROP)
        zf.write(service_sh_path, arcname=SERVICE_SH)
        zf.writestr(BASE_APK_ARCNAME, base_bytes)
        zf.writestr(ARM64_SPLIT_ARCNAME, arm64_bytes)
    ok(f"{MODULE_PROP}, {SERVICE_SH}, {BASE_APK_ARCNAME}, {ARM64_SPLIT_ARCNAME}")

    info(f"\nDone -> {output_zip}")
    info("Flash this zip via APatch's module manager, then reboot.")


if __name__ == "__main__":
    sys.exit(main())
