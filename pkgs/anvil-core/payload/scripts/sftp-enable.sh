#!/bin/sh
set -eu

SOURCE=/opt/.sftp-libexec
TARGET=/usr/libexec
SFTP=/opt/libexec/sftp-server

sleep 5

if [ ! -x "$SFTP" ]; then
    echo "SFTP server is unavailable at $SFTP" >&2
    exit 1
fi

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