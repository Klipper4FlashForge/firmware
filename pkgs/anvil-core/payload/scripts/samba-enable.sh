#!/bin/sh
# in background, così non blocca run-scripts
[ "${1:-}" = bg ] || { sh "$0" bg >>/usr/data/logs/samba.log 2>&1 & exit 0; }

CONF=/usr/data/anvil/samba/smb.conf
SHARE_PATH=/usr/data

# aspetta Entware montato: max 120s, ogni 3s
i=0
while [ ! -x /opt/bin/opkg ] && [ $i -lt 40 ]; do
    i=$((i+1)); sleep 3
done

sleep 5

[ -x /opt/bin/opkg ] || { echo "samba: entware non attivo"; exit 1; }

# installa samba solo se manca
if [ ! -x /opt/sbin/smbd ]; then
    /opt/bin/opkg update
    /opt/bin/opkg install samba4-server
fi
[ -x /opt/sbin/smbd ] || { echo "samba: install fallita"; exit 1; }

# già in esecuzione, esci
pidof smbd >/dev/null 2>&1 && exit 0

# config di default, creata solo se manca (poi la modifichi a mano)
if [ ! -f "$CONF" ]; then
    mkdir -p "$(dirname "$CONF")"
    cat > "$CONF" <<EOF
[global]
workgroup = WORKGROUP
server role = standalone server
map to guest = Bad User
min protocol = SMB2
log file = /opt/var/log/samba/log.%m
max log size = 500
lock directory = /opt/var/samba/lock
state directory = /opt/var/samba
cache directory = /opt/var/samba
pid directory = /opt/var/run
private dir = /opt/var/samba/private

[printer]
path = $SHARE_PATH
browseable = yes
read only = no
guest ok = yes
force user = root
EOF
fi

mkdir -p /opt/var/log/samba /opt/var/samba/lock /opt/var/samba/private /opt/var/run/samba
/opt/sbin/smbd -D -s "$CONF"


