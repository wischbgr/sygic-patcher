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

# ---------------------------------------------------------------------------
# Output helpers -- [+] a step/action, [i] informational, [-] error
# ---------------------------------------------------------------------------
_pad() { local i; for ((i = 0; i < "$1"; i++)); do printf '    '; done; }
step() { echo "[+] $1"; }                                  # top-level step
ok()   { echo "$(_pad "${2:-1}")[+] $1"; }                  # sub-action, default indent 1
info() { echo "$(_pad "${2:-0}")[i] $1"; }                  # informational, default indent 0
err()  { echo "[-] $1" >&2; }                               # error, to stderr

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
        *) err "Unknown argument: $1"; usage; exit 1 ;;
    esac
done

if ! command -v keytool >/dev/null 2>&1; then
    err "No keytool on PATH (it ships with the JDK, not just the JRE)."
    exit 1
fi

if [ -e "$OUT" ] && [ "$FORCE" -ne 1 ]; then
    err "Keystore $OUT already exists. Pass -f/--force to overwrite, or pick a different -o."
    exit 1
fi

if [ "${#C}" -ne 2 ]; then
    err "Flag --c must be a 2-letter country code (got '$C')."
    exit 1
fi

# Escape RFC2253-ish special characters in a DN component value for keytool -dname.
escape_dn_value() {
    printf '%s' "$1" | sed -e 's/\\/\\\\/g' -e 's/,/\\,/g' -e 's/+/\\+/g' \
        -e 's/"/\\"/g' -e 's/</\\</g' -e 's/>/\\>/g' -e 's/;/\\;/g' -e 's/=/\\=/g'
}

DNAME="CN=$(escape_dn_value "$CN"), OU=$(escape_dn_value "$OU"), O=$(escape_dn_value "$O"), L=$(escape_dn_value "$L"), ST=$(escape_dn_value "$ST"), C=$(escape_dn_value "$C")"

step "Generating keystore"
info "Keystore:   $OUT" 1
info "Alias:      $ALIAS" 1
info "Key:        RSA $KEYSIZE, valid $DAYS days (~$((DAYS / 365)) years)" 1
info "Subject DN: $DNAME" 1
info "Password:   $PASS" 1
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
info "Done -> $OUT"
echo
info "Use it with build_patched_xapk.py like:"
echo "        python3 build_patched_xapk.py Sygic.xapk Sygic_patched.xapk \\"
echo "            --keystore $OUT --ks-alias $ALIAS --ks-pass pass:$PASS \\"
echo "            --all -S my_skin_edits"
echo
info "Keep $OUT around -- it's what lets you re-sign future patched builds so Android treats them as updates to the same locally-signed app."
