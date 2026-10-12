"""One catalog for all selectable Creator 5 movement scenarios."""

SCENARIOS = {
    'swap': {
        "label": 'Warm tool swaps',
        "settings": {},
        "gcode": """; A warm, loaded four-head printer, with AFC owning the slicer's T commands.
PREP
SET_KINEMATIC_POSITION X=150 Y=150 Z=10
SIM_BEGIN
T0
SIM_CHECK TOOL=0
G1 X150 Y150 F30000
M400
T1
M400
SIM_CHECK TOOL=1 X=150 Y=150 Z=10
T2
M400
SIM_CHECK TOOL=2 X=150 Y=150 Z=10
T3
M400
SIM_CHECK TOOL=3 X=150 Y=150 Z=10
T0
M400
SIM_CHECK TOOL=0 X=150 Y=150 Z=10
SIM_REPORT
""",
    },
    'all-pairs': {
        "label": 'All 12 tool pairs',
        "settings": {},
        "gcode": """; An Euler circuit visits every directed pair of distinct physical heads.
PREP
SET_KINEMATIC_POSITION X=150 Y=150 Z=10
SIM_BEGIN
T0
M400
SIM_CHECK TOOL=0
G1 X150 Y150 F30000
M400
T1
M400
SIM_CHECK TOOL=1 X=150 Y=150 Z=10
T0
M400
SIM_CHECK TOOL=0 X=150 Y=150 Z=10
T2
M400
SIM_CHECK TOOL=2 X=150 Y=150 Z=10
T0
M400
SIM_CHECK TOOL=0 X=150 Y=150 Z=10
T3
M400
SIM_CHECK TOOL=3 X=150 Y=150 Z=10
T1
M400
SIM_CHECK TOOL=1 X=150 Y=150 Z=10
T2
M400
SIM_CHECK TOOL=2 X=150 Y=150 Z=10
T1
M400
SIM_CHECK TOOL=1 X=150 Y=150 Z=10
T3
M400
SIM_CHECK TOOL=3 X=150 Y=150 Z=10
T2
M400
SIM_CHECK TOOL=2 X=150 Y=150 Z=10
T3
M400
SIM_CHECK TOOL=3 X=150 Y=150 Z=10
T0
M400
SIM_CHECK TOOL=0 X=150 Y=150 Z=10
SIM_REPORT
""",
    },
    'pickup-e2': {
        "label": 'Empty carriage → T2',
        "settings": {},
        "gcode": """; Matches the AFC log's None -> e2 selection, with an empty carriage.
PREP
SET_KINEMATIC_POSITION X=150 Y=150 Z=10
SIM_BEGIN
T2
M400
SIM_CHECK TOOL=2 X=150 Y=150 Z=10
SIM_REPORT
""",
    },
    'mapping': {
        "label": 'AFC tool mapping',
        "settings": {},
        "gcode": """PREP
SET_KINEMATIC_POSITION X=150 Y=150 Z=10
SIM_BEGIN
SET_MAP LANE=e2 MAP=T0
T0
M400
SIM_CHECK TOOL=2
; Selecting the same mapped head should avoid a second mechanical swap.
T0
M400
SIM_CHECK TOOL=2
G1 X150 Y150 F30000
M400
T1
M400
SIM_CHECK TOOL=1 X=150 Y=150 Z=10
SIM_REPORT
""",
    },
    'heating': {
        "label": 'Cold-head heating',
        "settings": {'temperature': 25.0},
        "gcode": """PREP
SET_KINEMATIC_POSITION X=150 Y=150 Z=10
M104 T0 S220
M104 T1 S220
M104 T2 S220
M104 T3 S220
SIM_BEGIN
T0
M400
SIM_CHECK TOOL=0
G1 X150 Y150 F30000
M400
T1
M400
SIM_CHECK TOOL=1 X=150 Y=150 Z=10
SIM_REPORT
""",
    },
    'brush': {
        "label": 'Brush and resume',
        "settings": {},
        "gcode": """PREP
SET_KINEMATIC_POSITION X=150 Y=150 Z=10
T0
G90
G1 X280 Y150 Z10 F12000
M400
SIM_BEGIN
AFC_BRUSH
SIM_CHECK TOOL=0 X=280 Y=150 Z=10
; The brush now returns XYZ before restoring the saved modes.
G1 X150 Y150 Z10 F12000
M400
SIM_CHECK TOOL=0 X=150 Y=150 Z=10
; A subsequent swap must also return to the recovered print position.
T2
M400
SIM_CHECK TOOL=2 X=150 Y=150 Z=10
SIM_REPORT
""",
    },
}


def brush(x=280, relative=False, tool=0, z=10):
    continuation = 'G1 X1 Y2 Z1' if relative else 'G1 X150 Y150 Z10 F12000'
    destination = f'X={x + 1} Y=152 Z={z + 1}' if relative else 'X=150 Y=150 Z=10'
    return f"""PREP
SET_KINEMATIC_POSITION X=150 Y=150 Z=10
T{tool}
G90
G1 X{x} Y150 Z{z} F1234
M83
{'G91' if relative else 'G90'}
M400
SIM_BEGIN
AFC_BRUSH
SIM_CHECK TOOL={tool} X={x} Y=150 Z={z}
{continuation}
M400
SIM_CHECK TOOL={tool} {destination}
T2
M400
SIM_CHECK TOOL=2 {destination}
SIM_REPORT
"""

for x in (240, 245, 280):
    for relative in (False, True):
        name = f'brush-{x}-{relative}'
        SCENARIOS[name] = {'label': f'Brush from X{x} · {"relative" if relative else "absolute"}',
                           'gcode': brush(x, relative), 'settings': {}}
SCENARIOS['brush-offset'] = {'label': 'Brush with T3 offset',
                              'gcode': brush(tool=3), 'settings': {}}
SCENARIOS['faster'] = dict(SCENARIOS['swap'], label='Swaps with faster retreat',
                           settings={'retreat_feed': 4800})
SCENARIOS['missing-grab'] = dict(SCENARIOS['swap'], label='Failed T1 grab',
                                 settings={'fail_grab': 1}, expected_error='grab sensor never activated')
SCENARIOS['failed-unlock'] = dict(SCENARIOS['swap'], label='Failed unlock',
                                  settings={'fail_unlock': True}, expected_error='release failed')
SCENARIOS['brush-too-high'] = {
    'label': 'Brush exceeding Z limit', 'settings': {},
    'expected_error': 'Move out of range',
    'gcode': 'PREP\nSET_KINEMATIC_POSITION X=150 Y=150 Z=268\nSIM_BEGIN\nAFC_BRUSH\nSIM_REPORT\n',
}

for z in (1, 100):
    SCENARIOS[f'brush-z{z}'] = {'label': f'Brush and return to Z{z}',
                               'gcode': brush(z=z), 'settings': {}}

SCENARIOS['brush-below-zero'] = {'label': 'Brush starting below Z0',
                                'gcode': brush(z=-1), 'settings': {}}

def swap_t0_t3(x=150):
    return f"""PREP
SET_KINEMATIC_POSITION X=150 Y=150 Z=10
T0
G90
G1 X{x} Y150 Z10 F12000
M400
SIM_BEGIN
T3
M400
SIM_CHECK TOOL=3 X={x} Y=150 Z=10
SIM_REPORT
"""

SCENARIOS['t0-t3-baseline'] = {
    'label': 'T0 → T3 · original trajectory',
    'gcode': swap_t0_t3(), 'settings': {},
}
SCENARIOS['t0-t3-trajectory'] = {
    'label': 'T0 → T3 · gentle seat and clear approach',
    'gcode': swap_t0_t3(), 'settings': {'dock_shortcuts': 1},
}
SCENARIOS['t0-t3-outside'] = {
    'label': 'T0 → T3 · outside start, guarded approach',
    'gcode': swap_t0_t3(x=280), 'settings': {'dock_shortcuts': 1},
}
SCENARIOS['all-pairs-trajectory'] = dict(
    SCENARIOS['all-pairs'], label='All 12 tool pairs · gentle seating',
    settings={'dock_shortcuts': 1})
SCENARIOS['dock-offset-boundary'] = {
    'label': 'Dock approach · tool offset puts X outside clearance',
    'gcode': swap_t0_t3(x=249.9).replace('T0\n', 'T3\n', 1)
              .replace('SIM_BEGIN\nT3\n', 'SIM_BEGIN\nT0\n')
              .replace('SIM_CHECK TOOL=3', 'SIM_CHECK TOOL=0'),
    'settings': {'dock_shortcuts': 1},
}

for x in (150, 280):
    gcode = brush(x=x)
    SCENARIOS[f'brush-{x}-baseline'] = {
        'label': f'Brush X{x} · original clear travel', 'settings': {},
        'gcode': gcode.replace('PREP\n', 'PREP\nSET_GCODE_VARIABLE MACRO=AFC_BRUSH VARIABLE=travel_speed VALUE=100\nSET_GCODE_VARIABLE MACRO=AFC_BRUSH VARIABLE=shortcuts VALUE=0\n', 1),
    }
    SCENARIOS[f'brush-{x}-optimized'] = {
        'label': f'Brush X{x} · optimized clear travel', 'settings': {}, 'gcode': gcode,
    }

# Reference fixtures remain callable for regression comparisons, but the
# dropdown contains only current trajectories and useful safety cases.
CURRENT_REPLAYS = (
    'swap', 'all-pairs-trajectory', 't0-t3-trajectory', 't0-t3-outside',
    'brush', 'brush-offset', 'brush-z1', 'brush-z100', 'brush-below-zero',
    'dock-offset-boundary', 'mapping', 'heating', 'pickup-e2',
    'missing-grab', 'failed-unlock', 'brush-too-high',
)
for name, scene in SCENARIOS.items():
    scene['visible'] = name in CURRENT_REPLAYS
    if scene['visible']:
        scene['settings'] = dict(scene['settings'], dock_shortcuts=1, soft_docking=1, contact_speed=70.,
                                 withdrawal_speed=35., contact_accel=150.,
                                 async_unlock=0, unlock_duration=None)
