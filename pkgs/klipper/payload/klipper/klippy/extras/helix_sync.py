# Modulo nativo Klipper per sincronizzare i materiali dal JSON di HelixScreen
# e salvarli automaticamente nelle variabili persistenti.

import json
import os

class HelixSync:
    def __init__(self, config):
        self.printer = config.get_printer()
        self.json_path = config.get('json_path', '/usr/data/anvil/helixscreen/config/filament_slot_overrides.json')

        # Registra il comando G-code SYNC_HELIX_MATERIALS
        gcode = self.printer.lookup_object('gcode')
        gcode.register_command('SYNC_HELIX_MATERIALS', self.cmd_SYNC_HELIX_MATERIALS,
                               desc="Sincronizza i materiali dal JSON di HelixScreen")

    def cmd_SYNC_HELIX_MATERIALS(self, gcmd):
        mats = ['PETG', 'PETG', 'PETG', 'PETG']
        try:
            if os.path.exists(self.json_path):
                with open(self.json_path, 'r') as f:
                    data = json.load(f)
                    slots = data.get("toolchanger", {}).get("slots", {})
                    for i in range(4):
                        slot_data = slots.get(str(i), {})
                        mat = slot_data.get("material", "PETG")
                        if mat and mat.strip():
                            mats[i] = mat.strip().upper()
                gcmd.respond_info(f"HelixSync: Letti materiali dal JSON -> {mats}")
            else:
                gcmd.respond_info(f"HelixSync: File JSON non trovato in {self.json_path}, uso i default.")
        except Exception as e:
            gcmd.respond_info(f"HelixSync Errore: {str(e)}")
            return

        gcode = self.printer.lookup_object('gcode')

        # Aggiorna la variabile tool_material dentro la macro _FF_FILAMENT
        # usando SET_GCODE_VARIABLE (l'unico modo affidabile: scrivere
        # direttamente su macro_obj.variables non viene letto correttamente
        # a runtime dalle altre macro)
        try:
            formatted_list = "[" + ", ".join([f"'{m}'" for m in mats]) + "]"
            cmd_macro = (
                f"SET_GCODE_VARIABLE MACRO=_FF_FILAMENT VARIABLE=tool_material "
                f"VALUE=\"{formatted_list}\""
            )
            gcode.run_script_from_command(cmd_macro)
        except Exception as e:
            gcmd.respond_info(f"HelixSync Errore aggiornamento macro: {str(e)}")

        # Salva i dati usando il comando nativo SAVE_VARIABLE, che aggiorna
        # allVariables e riscrive saved_variables.cfg internamente
        try:
            for i, mat in enumerate(mats):
                gcode.run_script_from_command(
                    f"SAVE_VARIABLE VARIABLE=tool_{i}_mat VALUE=\"'{mat}'\""
                )
            gcmd.respond_info(f"HelixSync: Materiali aggiornati e salvati -> {mats}")
        except Exception as e:
            gcmd.respond_info(f"HelixSync Errore salvataggio variabili: {str(e)}")

def load_config(config):
    return HelixSync(config)