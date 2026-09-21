#!/bin/sh
OPT_DIR=/usr/data/anvil/entware/opt

[ -x "$OPT_DIR/bin/opkg" ] || exit 0

mkdir -p /opt
grep -q ' /opt ' /proc/mounts || mount --bind "$OPT_DIR" /opt
grep -q ' /opt ' /proc/mounts || { echo "entware: mount fallito"; exit 1; }

grep -q '/opt/bin' /etc/profile || echo 'export PATH=/opt/bin:/opt/sbin:$PATH' >> /etc/profile

[ -x /opt/etc/init.d/rc.unslung ] && /opt/etc/init.d/rc.unslung start