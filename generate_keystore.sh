#!/usr/bin/env bash
# Generate a self-signed keystore for signing patched Sygic APKs/xapks with
# build_patched_xapk.py --keystore/--ks-alias/--ks-pass.
#
# Usage:
#   ./generate_keystore.sh [options]
#
# Options (all optional -- sensible defaults below):
#   -o, --out FILE        output keystore path            (default: sygic-patcher.jks)
#   -a, --alias NAME       key alias                        (default: sygic-patcher)
#   -d, --days N           certificate validity in days      (default: 9125, ~25 years)
#   -k, --keysize N        RSA key size in bits              (default: 2048)
#       --cn STR           Common Name                       (default: "Sygic Patcher (personal build)")
#       --ou STR           Organizational Unit                (default: "sygic-patcher")
#       --o STR            Organization                       (default: "sygic-patcher")
#       --l STR            Locality                           (default: "Local")
#       --st STR           State/Province                     (default: "Local")
#       --c STR             2-letter Country Code              (default: "XX")
#   -p, --pass STR         keystore/key password             (default: "changeit")
#   -f, --force            overwrite an existing keystore file
#   -h, --help             show this help
#
# This keystore only needs to be different from key to key, not secret -- it's
# not protecting anything sensitive, just letting Android recognize rebuilds
# as updates to the same locally-signed app. The password is a fixed default
# ("changeit", the standard Java placeholder) rather than prompted for.
set -euo pipefail

OUT="sygic-patcher.jks"
ALIAS="sygic-patcher"
DAYS=9125
KEYSIZE=2048
CN="Sygic Patcher (personal build)"
OU="sygic-patcher"
O="sygic-patcher"
L="Local"
ST="Local"
C="XX"
PASS="changeit"
FORCE=0

usage() {
    sed -n '2,26p' "$0" | sed 's/^# \{0,1\}//'
}

while [ $# -gt 0 ]; do
    case "$1" in
        -o|--out) OUT="$2"; shift 2 ;;
        -a|--alias) ALIAS="$2"; shift 2 ;;
        -d|--days) DAYS="$2"; shift 2 ;;
        -k|--keysize) KEYSIZE="$2"; shift 2 ;;
        --cn) CN="$2"; shift 2 ;;
        --ou) OU="$2"; shift 2 ;;
        --o) O="$2"; shift 2 ;;
        --l) L="$2"; shift 2 ;;
        --st) ST="$2"; shift 2 ;;
        --c) C="$2"; shift 2 ;;
        -p|--pass) PASS="$2"; shift 2 ;;
        -f|--force) FORCE=1; shift ;;
        -h|--help) usage; exit 0 ;;
        *) echo "unknown argument: $1" >&2; usage; exit 1 ;;
    esac
done

if ! command -v keytool >/dev/null 2>&1; then
    echo "ERROR: keytool not found on PATH (it ships with the JDK, not just the JRE)." >&2
    exit 1
fi

if [ -e "$OUT" ] && [ "$FORCE" -ne 1 ]; then
    echo "ERROR: $OUT already exists. Pass -f/--force to overwrite, or pick a different -o." >&2
    exit 1
fi

if [ "${#C}" -ne 2 ]; then
    echo "ERROR: --c must be a 2-letter country code (got '$C')." >&2
    exit 1
fi

# Escape RFC2253-ish special characters in a DN component value for keytool -dname.
escape_dn_value() {
    printf '%s' "$1" | sed -e 's/\\/\\\\/g' -e 's/,/\\,/g' -e 's/+/\\+/g' \
        -e 's/"/\\"/g' -e 's/</\\</g' -e 's/>/\\>/g' -e 's/;/\\;/g' -e 's/=/\\=/g'
}

DNAME="CN=$(escape_dn_value "$CN"), OU=$(escape_dn_value "$OU"), O=$(escape_dn_value "$O"), L=$(escape_dn_value "$L"), ST=$(escape_dn_value "$ST"), C=$(escape_dn_value "$C")"

echo "Keystore:   $OUT"
echo "Alias:      $ALIAS"
echo "Key:        RSA $KEYSIZE, valid $DAYS days (~$((DAYS / 365)) years)"
echo "Subject DN: $DNAME"
echo "Password:   $PASS"
echo

keytool -genkeypair -v \
    -keystore "$OUT" \
    -alias "$ALIAS" \
    -keyalg RSA -keysize "$KEYSIZE" \
    -sigalg SHA256withRSA \
    -validity "$DAYS" \
    -storetype PKCS12 \
    -dname "$DNAME" \
    -storepass "$PASS" -keypass "$PASS"

echo
echo "done -> $OUT"
echo
echo "Use it with build_patched_xapk.py like:"
echo "  python3 build_patched_xapk.py Sygic.xapk Sygic_patched.xapk \\"
echo "      --keystore $OUT --ks-alias $ALIAS --ks-pass pass:$PASS \\"
echo "      --all -S my_skin_edits"
echo
echo "Keep $OUT around -- it's what lets you re-sign future patched builds so"
echo "Android treats them as updates to the same locally-signed app."
