#!/bin/sh

# Ottiene la cartella esatta in cui risiede questo script (es. /usr/data/anvil/play)
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PLAY_BIN="$SCRIPT_DIR/play"

INPUT="$1"

if [ -z "$INPUT" ]; then
    echo "Uso: $0 <indice_0-7 | nome_suono>"
    echo "Esempi: $0 1  oppure  $0 play_print_done"
    exit 1
fi

# Converti l'input in minuscolo per sicurezza
INPUT=$(echo "$INPUT" | tr '[:upper:]' '[:lower:]')

# Risoluzione dell'ID in base al numero o al nome
case "$INPUT" in
    0|play_error) TUNE_ID=0 ;;
    1|play_print_done) TUNE_ID=1 ;;
    2|play_startup) TUNE_ID=2 ;;
    3|play_makerbot_tv) TUNE_ID=3 ;;
    4|play_beethoven_5th) TUNE_ID=4 ;;
    5|play_filament_start) TUNE_ID=5 ;;
    6|play_pause) TUNE_ID=6 ;;
    7|play_sailfish_startup) TUNE_ID=7 ;;
    *)
        echo "Errore: Suono o indice '$INPUT' non valido."
        exit 1
        ;;
esac

# Verifica presenza dell'eseguibile nella sua cartella corretta
if [ ! -f "$PLAY_BIN" ]; then
    echo "Errore: File 'play' non trovato in $SCRIPT_DIR."
    exit 1
fi

# --- PULIZIA MIRATA: SOLO IL PIN PC12 E PROCESSI ATTIVI ---
# 1. Killa qualsiasi istanza precedente di play in esecuzione
pkill -f "$PLAY_BIN" 2>/dev/null
pkill -f play 2>/dev/null

# 2. Sblocca esclusivamente il pin pc12 se è rimasto esportato nel sysfs
for gpiodir in /sys/class/gpio/gpio*; do
    if [ -d "$gpiodir" ] && [ -f "$gpiodir/label" ]; then
        LABEL=$(cat "$gpiodir/label" 2>/dev/null | tr '[:upper:]' '[:lower:]')
        if [ "$LABEL" = "pc12" ]; then
            GPIO_NUM=${gpiodir##*/gpio}
            echo "$GPIO_NUM" > /sys/class/gpio/unexport 2>/dev/null
        fi
    fi
done
# ---------------------------------------------------------

# Patch diretta sul file "play" usando il percorso assoluto
printf "\\x$(printf %02x $TUNE_ID)" | dd of="$PLAY_BIN" bs=1 seek=$((0x47DC)) conv=notrunc 2>/dev/null
chmod +x "$PLAY_BIN"

# Esecuzione diretta e salvataggio del PID
"$PLAY_BIN" &
PID=$!

# Attendi 10 secondi per consentire la riproduzione
sleep 10

# Controlla se il processo è ancora attivo e killa per liberare il pin
if kill -0 "$PID" 2>/dev/null; then
    kill "$PID" 2>/dev/null
    sleep 1
    kill -9 "$PID" 2>/dev/null
fi

exit 0