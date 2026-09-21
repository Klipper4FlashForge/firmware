#!/bin/sh
#
# start_samba.sh
# Creator 5 Pro - Avvio Samba4
#

INSTALL_DIR=/usr/data/samba4

export LD_LIBRARY_PATH="$INSTALL_DIR/usr/lib:$INSTALL_DIR/usr/lib/samba"

# Attesa attiva per la rete wlan0
while ! ifconfig wlan0 2>/dev/null | grep -q "inet addr:"; do
    sleep 2
done
sleep 5

# Creazione directory necessarie
mkdir -p "$INSTALL_DIR/var/log"
mkdir -p "$INSTALL_DIR/var/lock"
mkdir -p "$INSTALL_DIR/var/private"

# PULIZIA PREVENTIVA: Cancella i file di lock e socket accumulati
rm -rf "$INSTALL_DIR/var/lock/*"
rm -rf "$INSTALL_DIR/var/private/*.sock"

gia_in_esecuzione() {
    ps | grep "$1" | grep -v grep >/dev/null 2>&1
}

# Avvio di smbd
if gia_in_esecuzione "$INSTALL_DIR/usr/sbin/smbd"; then
    echo "smbd e' gia' in esecuzione."
else
    (
        while true; do
            "$INSTALL_DIR/usr/sbin/smbd" -i -S -s "$INSTALL_DIR/etc/smb.conf" \
                >> "$INSTALL_DIR/var/log/smbd.log" 2>&1
            echo "=== smbd uscito, riavvio tra 3s: $(date) ===" >> "$INSTALL_DIR/var/log/smbd.log"
            sleep 3
        done
    ) &
    echo "smbd avviato."
fi