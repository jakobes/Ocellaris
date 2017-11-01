import sys
from ocellaris import Simulation, setup_simulation


# Dummy values to make setup_simulation() run without errors
DUMMY = """
solver: {type: AnalyticalSolution}
mesh: {type: Rectangle, Nx: 4, Ny: 4}
boundary_conditions: []
time: {dt: 1.0}
physical_properties: {nu0: 1.0}
"""


INPUT_USER_CONSTANTS = """
ocellaris:
    type: input
    version: 1.0

user_code:
    constants:
        A: 21

ref: py$ A*2.0
""" + DUMMY
def test_user_constants():
    sim = Simulation()
    sim.input.read_yaml(yaml_string=INPUT_USER_CONSTANTS)
    success = setup_simulation(sim)
    assert success
    
    assert sim.input.get_value('ref') == 42.0


# TODO: this depends on the CWD of the test runner
INPUT_MODULE_IMPORT = """
ocellaris:
    type: input
    version: 1.0

user_code:
    python_path:
    -   scripts
    modules:
    -   plot_reports
""" + DUMMY
def test_import_module():
    # A randomly selected script that does nothing at import time
    dummy_mod = 'plot_reports'
    assert dummy_mod not in sys.modules
    
    sim = Simulation()
    sim.input.read_yaml(yaml_string=INPUT_MODULE_IMPORT)
    success = setup_simulation(sim)
    assert success
    
    assert dummy_mod in sys.modules
    sys.modules.pop(dummy_mod)


INPUT_USER_CODE = """
ocellaris:
    type: input
    version: 1.0

user_code:
    constants:
        Q: 1.0
    code: |
        import sys
        sys.modules['__DUMMY__'] = 1
        assert simulation is not None
        assert Q == 1.0
""" + DUMMY
def test_use_code():
    dummy_mod = '__DUMMY__'
    assert dummy_mod not in sys.modules
    
    sim = Simulation()
    sim.input.read_yaml(yaml_string=INPUT_USER_CODE)
    success = setup_simulation(sim)
    assert success
    
    assert dummy_mod in sys.modules
    sys.modules.pop(dummy_mod)
