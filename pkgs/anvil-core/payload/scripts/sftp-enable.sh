#!/bin/sh
# in background, così non blocca run-scripts
[ "${1:-}" = bg ] || { sh "$0" bg >>/usr/data/logs/sftp.log 2>&1 & exit 0; }

set -eu

SOURCE=/opt/.sftp-libexec
TARGET=/usr/libexec
SFTP=/opt/libexec/sftp-server

# aspetta Entware montato: max 120s, ogni 3s
i=0
while [ ! -x /opt/bin/opkg ] && [ $i -lt 40 ]; do
    i=$((i+1)); sleep 3
done
[ -x /opt/bin/opkg ] || { echo "sftp: entware non attivo"; exit 1; }

# installa sftp-server solo se manca
if [ ! -x "$SFTP" ]; then
    /opt/bin/opkg update
    /opt/bin/opkg install openssh-sftp-server
fi

if [ ! -x "$SFTP" ]; then
    echo "SFTP server is unavailable at $SFTP" >&2
    exit 1
fi

# già montato, esci
if grep -q " $TARGET " /proc/mounts; then
    exit 0
fi

mkdir -p "$SOURCE"
if [ ! -e "$SOURCE/dbus-daemon-launch-helper" ]; then
    cp -a "$TARGET/." "$SOURCE/"
fi
ln -snf "$SFTP" "$SOURCE/sftp-server"
mount --bind "$SOURCE" "$TARGET"
test -x "$TARGET/sftp-server"


### opkg update
### opkg install openssh-sftp-server