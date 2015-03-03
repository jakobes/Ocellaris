from ocellaris.utils import report_error

_PROBES = {}

def add_probe(name, probe_class):
    """
    Register a postprocessing probe
    """
    _PROBES[name] = probe_class

def register_probe(name):
    """
    A class decorator to register postprocessing probes
    """
    def register(probe_class):
        add_probe(name, probe_class)
        return probe_class
    return register

def get_probe(name):
    """
    Return a postprocessing probe by name
    """
    try:
        return _PROBES[name]
    except KeyError:
        report_error('Postprocessing probe "%s" not found' % name,
                     'Available probe:\n' +
                     '\n'.join('  %-20s - %s' % (n, s.description) 
                               for n, s in sorted(_PROBES.items())),
                     stop=True)
        raise

def setup_probes(simulation):
    """
    Install probes from a simulation input
    """
    def hook(probe):
        return lambda report: probe.end_of_timestep()
    
    probe_inputs = simulation.input.get('probes', [])
    for inp in probe_inputs:
        probe_type = inp['type']
        probe_class = get_probe(probe_type)
        probe = probe_class(simulation, inp)
        simulation.add_post_timestep_hook(hook(probe))

class Probe(object):
    def __init__(self, simulation, probe_input):
        """
        A base class for post-processing probes
        """
        self.simulation = simulation
        self.input = probe_input
    
    def end_of_timestep(self):
        pass

from .line_probe import LineProbe