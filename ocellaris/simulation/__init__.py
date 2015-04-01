import time
from ocellaris.utils.geometry import init_connectivity, precompute_cell_data, precompute_facet_data
from .hooks import Hooks
from .input import Input
from .plotting import Plotting
from .reporting import Reporting
from .log import Log

class Simulation(object):
    def __init__(self):
        """
        Represents one Ocellaris simulation. The Simulation class 
        connects the input file, geometry, mesh and more with the
        solver and the result plotting and reporting tools     
        """
        self.hooks = Hooks(self)
        self.input = Input(self)
        self.data = {}        
        self.plotting = Plotting(self)
        self.reporting = Reporting(self)
        self.log = Log(self)
        
        # Several parts of the code wants to know these things,
        # so we keep them in a central place
        self.ndim = 0
        self.timestep = 0
        self.time = 0.0
        self.dt = 0.0
        
        # These will be filled out when ocellaris.run is setting up
        # the solver. Included here for documentation purposes only
        self.solver = None
        self.multi_phase_model = None
        
        # For timing the analysis
        self.prevtime = self.starttime = time.time()
    
    def set_mesh(self, mesh):
        """
        Set the computational domain
        """
        self.data['mesh'] = mesh
        self.ndim = mesh.topology().dim()
        self.update_mesh_data()
    
    def update_mesh_data(self):
        """
        Some precomputed values must be calculated before the timestepping
        and updated every time the mesh changes
        """
        init_connectivity(self)
        precompute_cell_data(self)
        precompute_facet_data(self)
        
    def _at_start_of_timestep(self, timestep_number, t, dt):
        self.timestep = timestep_number
        self.time = t
        self.dt = dt
    
    def _at_end_of_timestep(self):
        # Report the time spent in this time step
        newtime = time.time()
        self.reporting.report_timestep_value('tstime', newtime-self.prevtime)
        self.reporting.report_timestep_value('tottime', newtime-self.starttime)
        self.prevtime = newtime
        
        # Report the maximum velocity
        vels = 0
        for d in range(self.ndim):
            vels += self.data['u'][d].vector().array()**2
        vel_max = vels.max()**0.5
        self.reporting.report_timestep_value('umax', vel_max)
        
        # Write timestep report
        self.reporting.log_timestep_reports()