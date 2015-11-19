import dolfin
from ocellaris.utils import timeit

class Hooks(object):
    def __init__(self, simulation):
        """
        This class allows registering functions to run at
        given times during the simulation, e.g. to update
        some values for the next time step, report something
        after each time step or clean up after the simulation
        """
        self.simulation = simulation
        self._pre_simulation_hooks = []
        self._pre_timestep_hooks = []
        self._post_timestep_hooks = []
        self._post_simulation_hooks = []
    
    # ------------------------------------------
    # Hook adders:
    
    def add_pre_simulation_hook(self, hook, description):
        """
        Add a function that will run before the simulation starts
        """
        self._pre_simulation_hooks.append((hook, description))
    
    def add_pre_timestep_hook(self, hook, description):
        """
        Add a function that will run before the solver in each time step
        """
        self._pre_timestep_hooks.append((hook, description))
    
    def add_post_timestep_hook(self, hook, description):
        """
        Add a function that will run after the solver in each time step
        """
        self._post_timestep_hooks.append((hook, description))
    
    def add_post_simulation_hook(self, hook, description):
        """
        Add a function that will run after the simulation is done
        """
        self._post_simulation_hooks.append((hook, description))
        
    # ------------------------------------------
    # Hook runners:
    
    def simulation_started(self):
        """
        Called by the solver when the simulation starts
        
        Will run all pre simulation hooks in the reverse
        order they have been added
        """
        for hook, description in self._pre_simulation_hooks[::-1]:
            try:
                hook()
            except:
                self.simulation.log.error('Got exception in hook: %s' % description)
                raise
    
    @timeit
    def new_timestep(self, timestep_number, t, dt):
        """
        Called by the solver at the beginning of a new time step
        
        Will run all pre timestep hooks in the reverse
        order they have been added 
        """
        self.simulation._at_start_of_timestep(timestep_number, t, dt)
        for hook, description in self._pre_timestep_hooks[::-1]:
            t = dolfin.Timer('Ocellaris hook %s' % description)
            try:
                hook(timestep_number, t, dt)
            except:
                self.simulation.log.error('Got exception in hook: %s' % description)
                raise
            finally:
                t.stop()
    
    @timeit
    def end_timestep(self):
        """
        Called by the solver at the end of a time step
        
        Will run all post timestep hooks in the reverse
        order they have been added
        """
        for hook, description in self._post_timestep_hooks[::-1]:
            t = dolfin.Timer('Ocellaris hook %s' % description)
            try:
                hook()
            except:
                self.simulation.log.error('Got exception in hook: %s' % description)
                raise
            finally:
                t.stop()
        self.simulation._at_end_of_timestep()
    
    def simulation_ended(self, success):
        """
        Called by the solver when the simulation is done
        
        Will run all post simulation hooks in the reverse
        order they have been added
        
        Arguments:
            success: True if nothing went wrong, False for
            diverging solution and other problems
        """
        self.simulation.success = success
        for hook, description in self._post_simulation_hooks[::-1]:
            try:
                hook(success)
            except:
                self.simulation.log.error('Got exception in hook: %s' % description)
                raise
    
    def show_hook_info(self):
        """
        Show all registered hooks
        """
        show = self.simulation.log.info
        show('\nRegistered hooks:')
        for hook_type, hooks in [('Pre-simulation', self._pre_simulation_hooks),
                                 ('Pre-timestep', self._pre_timestep_hooks),
                                 ('Post-timestep:', self._post_timestep_hooks),
                                 ('Post-simulation', self._post_simulation_hooks)]:
            show('    %s:' % hook_type)
            for _hook, description in hooks[::-1]:
                show('        - %s' % description)
