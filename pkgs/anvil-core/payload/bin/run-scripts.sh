#!/bin/sh

# Cartella dove metterai i tuoi script .sh dinamici
SCRIPTS_DIR="/usr/data/anvil/scripts"
LOG="/usr/data/logs/custom-scripts.log"

# Funzione di log interna
log_launcher() {
    echo "[$(date)] $1" >> "$LOG"
}

: > "$LOG"
log_launcher "Avvio esecuzione script dinamici..."

# Controlla se la cartella esiste
if [ -d "$SCRIPTS_DIR" ]; then
    for script in "$SCRIPTS_DIR"/*.sh; do
        
        # Guardia contro il glob vuoto (POSIX sh non ha nullglob)
        if [ -f "$script" ]; then
            script_name=$(basename "$script")

            if [ -x "$script" ]; then
                EXEC_TYPE="has +x"
                # Se ha i permessi +x, possiamo eseguirlo direttamente
                log_launcher "Launched: $script_name ($EXEC_TYPE)"
                ( echo "=== Executed directly ($EXEC_TYPE) ==="; "$script" ) >> "$LOG" 2>&1
            else
                EXEC_TYPE="missing +x"
                # Se manca +x, lo forziamo con sh come facevi tu
                log_launcher "Launched: $script_name ($EXEC_TYPE)"
                ( echo "=== Executed via sh ($EXEC_TYPE) ==="; sh "$script" ) >> "$LOG" 2>&1
            fi

        fi
    done
fi

log_launcher "Tutti gli script dinamici sono stati eseguiti."

