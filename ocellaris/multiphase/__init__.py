import dolfin
from ocellaris.utils import report_error

_MULTI_PHASE_MODELS = {}

def add_multi_phase_model(name, multi_phase_model_class):
    """
    Register a multi phase scheme
    """
    _MULTI_PHASE_MODELS[name] = multi_phase_model_class

def register_multi_phase_model(name):
    """
    A class decorator to register multi phase schemes
    """
    def register(multi_phase_model_class):
        add_multi_phase_model(name, multi_phase_model_class)
        return multi_phase_model_class
    return register

def get_multi_phase_model(name):
    """
    Return a multi phase model by name
    """
    try:
        return _MULTI_PHASE_MODELS[name]
    except KeyError:
        report_error('Multi phase model "%s" not found' % name,
                     'Available models:\n' +
                     '\n'.join('  %-20s - %s' % (n, s.description) 
                               for n, s in sorted(_MULTI_PHASE_MODELS.items())))
        raise

class MultiPhaseModel(object):
    description = 'No description available'
    
    def update(self, t, dt):
        print 'mpm', t, dt
        pass

from . import single_phase
from . import blended_algebraic_vof
