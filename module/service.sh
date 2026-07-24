#!/system/bin/sh
# Runs late in boot (pm/PackageManager guaranteed up), unlike post-fs-data.sh
MODDIR=${0%/*}
PKG="com.sygic.aura"
LOG="$MODDIR/patch.log"

echo "$(date) service.sh start" > "$LOG"

# wait for package service to actually answer, up to ~60s
i=0
while [ $i -lt 30 ]; do
    PATHS=$(pm path "$PKG" 2>/dev/null)
    [ -n "$PATHS" ] && break
    sleep 2
    i=$((i+1))
done

if [ -z "$PATHS" ]; then
    echo "could not resolve any installed path for $PKG (app not installed / pm not ready)" >> "$LOG"
    exit 0
fi

mount_over() {
    GREP_PATTERN="$1"
    PATCHED="$MODDIR/$2"
    TARGET=$(echo "$PATHS" | grep "$GREP_PATTERN" | head -n1 | sed 's/^package://')

    if [ -z "$TARGET" ]; then
        echo "could not resolve installed path matching '$GREP_PATTERN'" >> "$LOG"
        return
    fi
    if [ ! -f "$TARGET" ]; then
        echo "resolved path does not exist: $TARGET" >> "$LOG"
        return
    fi
    if [ ! -f "$PATCHED" ]; then
        echo "bundled patched apk missing: $PATCHED" >> "$LOG"
        return
    fi

    chcon u:object_r:apk_data_file:s0 "$PATCHED" 2>>"$LOG"
    chown 1000:1000 "$PATCHED" 2>>"$LOG"
    chmod 0644 "$PATCHED" 2>>"$LOG"

    if mount -o bind "$PATCHED" "$TARGET" 2>>"$LOG"; then
        echo "bind-mounted $PATCHED over $TARGET" >> "$LOG"
    else
        echo "bind mount FAILED for $TARGET" >> "$LOG"
    fi
}

# arm64_v8a split: minimum_fps==maximum_fps patch never applied (dropped as redundant),
# CSDKMapView fps-override default -10.0->+120.0 (BALANCED -> PERFORMANCE)
mount_over "arm64_v8a" "split_config.arm64_v8a.apk"

# base apk: SettingItemsManager.E()'s FEATURE_DEBUG_MENU.isActive() check nop'd out,
# unconditionally exposes the internal Debug/DevSettings/DevActions/Features/UiKit menu
mount_over "/base\.apk" "com.sygic.aura.apk"
