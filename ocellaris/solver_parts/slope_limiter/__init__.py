import numpy
import dolfin
from ocellaris.utils import ocellaris_error
from ocellaris.solver_parts import mark_cell_layers


DEFAULT_LIMITER = 'None'
DEFAULT_FILTER = 'nofilter'
DEFAULT_USE_CPP = True
_SLOPE_LIMITERS = {}


def add_slope_limiter(name, slope_limiter_class):
    """
    Register a slope limiter
    """
    _SLOPE_LIMITERS[name] = slope_limiter_class


def register_slope_limiter(name):
    """
    A class decorator to register slope limiters
    """
    def register(slope_limiter_class):
        add_slope_limiter(name, slope_limiter_class)
        return slope_limiter_class
    return register


def get_slope_limiter(name):
    """
    Return a slope limiter by name
    """
    try:
        return _SLOPE_LIMITERS[name]
    except KeyError:
        ocellaris_error('Slope limiter "%s" not found' % name,
                        'Available slope limiters:\n' +
                        '\n'.join('  %-20s - %s' % (n, s.description)
                                  for n, s in sorted(_SLOPE_LIMITERS.items())))
        raise


class SlopeLimiterBase(object):
    description = 'No description available'
    active = True


@register_slope_limiter('None')
class DoNothingSlopeLimiter(SlopeLimiterBase):
    description = 'No slope limiter'
    active = False
    
    def __init__(self, *argv, **kwargs):
        self.additional_plot_funcs = []
    
    def run(self):
        pass


def SlopeLimiter(simulation, phi_name, phi, output_name=None, method=None, old_value=None):
    """
    Return a slope limiter based on the user provided input or the default
    values if no input is provided by the user
    """
    # Get user provided input (or default values)
    inp = simulation.input.get_value('slope_limiter/%s' % phi_name, {}, 'Input')
    if method is None:
        method = inp.get_value('method', DEFAULT_LIMITER, 'string')
    filter_method = inp.get_value('filter', DEFAULT_FILTER, 'string')
    use_cpp = inp.get_value('use_cpp', DEFAULT_USE_CPP, 'bool')
    plot_exceedance = inp.get_value('plot', False, 'bool')
    skip_boundary = inp.get_value('skip_boundary', True, 'bool')
    
    # Mark boundary cells
    V = phi.function_space()
    if skip_boundary:
        boundary_condition = mark_cell_layers(simulation, V, layers=1)
    else:
        boundary_condition = numpy.zeros(V.dim(), bool)
    
    # Construct the limiter
    name = phi_name if output_name is None else output_name
    simulation.log.info('    Using slope limiter %s with filter %s for %s' % (method, filter_method, name))
    limiter_class = get_slope_limiter(method)
    limiter = limiter_class(phi_name, phi, boundary_condition, filter_method, use_cpp, output_name, old_value)
    
    if plot_exceedance:
        for func in limiter.additional_plot_funcs:
            simulation.io.add_extra_output_function(func)
    
    return limiter


from . import naive_nodal
from . import hierarchical_taylor
