#!/usr/bin/env python3
# /// script
# requires-python = ">=3.9"
# dependencies = []
# ///
"""
Extract assets/res/skin/* from a Sygic .xapk's base APK into a directory
laid out exactly how build_patched_xapk.py's -S/--skins expects it
(OUTPUT_DIR/assets/res/skin/*), ready to edit and feed straight back in.

OUTPUT_DIR defaults to "skin_override" -- the same default build_patched_xapk.py
uses for -S/--skins, so with no directory arguments at all, extracting and
then building automatically round-trip through the same place.

Usage:
  python3 extract_skins.py INPUT.xapk [OUTPUT_DIR]

Example:
  python3 extract_skins.py Sygic.xapk
  # ... edit files under skin_override/assets/res/skin/ ...
  python3 build_patched_xapk.py Sygic.xapk Sygic_patched.xapk \\
      --keystore mykey.jks --ks-alias myalias --ks-pass pass:mypassword \\
      --all
"""

import argparse
import io
import json
import os
import sys
import zipfile

SKIN_PREFIX = "assets/res/skin/"
DEFAULT_OUTPUT_DIR = "skin_override"


def find_base_apk(xapk_zip):
    with xapk_zip.open("manifest.json") as f:
        manifest = json.load(f)
    for entry in manifest["split_apks"]:
        if entry["id"] == "base":
            return entry["file"]
    raise SystemExit("ERROR: no base APK entry found in manifest.json")


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("input_xapk")
    p.add_argument("output_dir", nargs="?", default=DEFAULT_OUTPUT_DIR,
                    help=f"default: {DEFAULT_OUTPUT_DIR}")
    p.add_argument("--force", action="store_true",
                    help="overwrite files that already exist in output_dir")
    args = p.parse_args()

    with zipfile.ZipFile(args.input_xapk) as xapk_zip:
        base_file = find_base_apk(xapk_zip)
        print(f"base APK: {base_file}")
        base_bytes = xapk_zip.read(base_file)

    with zipfile.ZipFile(io.BytesIO(base_bytes)) as base_zip:
        skin_entries = [n for n in base_zip.namelist()
                         if n.startswith(SKIN_PREFIX) and not n.endswith("/")]
        if not skin_entries:
            raise SystemExit(f"ERROR: no entries found under {SKIN_PREFIX} in {base_file}")

        out_skin_dir = os.path.join(args.output_dir, "assets", "res", "skin")
        os.makedirs(out_skin_dir, exist_ok=True)

        extracted = 0
        skipped = 0
        for entry in sorted(skin_entries):
            rel_name = entry[len(SKIN_PREFIX):]
            out_path = os.path.join(out_skin_dir, rel_name)
            if os.path.exists(out_path) and not args.force:
                print(f"  [skip, exists] {rel_name}  (use --force to overwrite)")
                skipped += 1
                continue
            with base_zip.open(entry) as src, open(out_path, "wb") as dst:
                dst.write(src.read())
            print(f"  extracted: {rel_name}")
            extracted += 1

    print(f"\n{extracted} file(s) extracted, {skipped} skipped -> {out_skin_dir}")
    if args.output_dir == DEFAULT_OUTPUT_DIR:
        print(f"Edit the files under {out_skin_dir}, then just run build_patched_xapk.py "
              f"(it picks up ./{DEFAULT_OUTPUT_DIR} automatically)")
    else:
        print(f"Edit the files under {out_skin_dir}, then pass -S {args.output_dir} to build_patched_xapk.py")


if __name__ == "__main__":
    sys.exit(main())
