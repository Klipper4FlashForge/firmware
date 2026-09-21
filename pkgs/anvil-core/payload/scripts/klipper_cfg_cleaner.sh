#!/bin/sh

WAIT_COUNT=0
PYTHON_BIN="/opt/bin/python3"

# Controllo con timeout a 30 secondi sul binario in /opt/bin/
while ! "$PYTHON_BIN" -c "pass" >/dev/null 2>&1; do
    if [ "$WAIT_COUNT" -ge 60 ]; then
        echo "Error: Python3 non disponibile dopo 30 secondi. Aborto!" >&2
        exit 1
    fi
    echo "Attendo che l'ambiente python3 sia pronto ($WAIT_COUNT/30s)..."
    sleep 1
    WAIT_COUNT=$((WAIT_COUNT + 1))
done

# Esecuzione pulizia configurazione
"$PYTHON_BIN" /usr/prog/scripts/config_cleaner.py