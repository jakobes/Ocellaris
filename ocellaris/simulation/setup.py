import time
import dolfin
from ocellaris.solvers import get_solver
from ocellaris.postprocess import setup_probes
from ocellaris.utils import debug_console_hook, ocellaris_error, ocellaris_project, log_timings, OcellarisConstant, RunnablePythonString
from ocellaris.solver_parts import BoundaryRegion, get_multi_phase_model, MeshMorpher


def setup_simulation(simulation):
    """
    Prepare an Ocellaris simulation for running
    """
    simulation.log.info('Preparing simulation ...\n')
    t_start = time.time()
    
    # Test for PETSc linear algebra backend
    if not dolfin.has_linear_algebra_backend("PETSc"):
        ocellaris_error('Missing PETSc',
                        'DOLFIN has not been configured with PETSc '
                        'which is needed by Ocellaris.')
    dolfin.parameters["linear_algebra_backend"] = "PETSc"
    
    # Make time and timestep available in expressions for the initial conditions etc
    simulation.log.info('Creating time simulation')
    simulation.time = simulation.input.get_value('time/tstart', 0.0, 'float')
    simulation.dt = simulation.input.get_value('time/dt', required_type='float')
    assert simulation.dt > 0
    
    if not simulation.restarted:
        # Load the mesh. The mesh determines if we are in 2D or 3D
        load_mesh(simulation)
    
    # Mark the boundaries of the domain with separate marks
    # for each regions Creates a new "ds" measure
    mark_boundaries(simulation)
    
    # Load the periodic boundary conditions. This must 
    # be done before creating the function spaces as
    # they depend on the periodic constrained domain
    setup_periodic_domain(simulation)
    
    # Create function spaces. This must be done before
    # creating Dirichlet boundary conditions
    setup_function_spaces(simulation)
    
    # Load the mesh morpher used for prescribed mesh velocities and ALE multiphase solvers
    simulation.mesh_morpher = MeshMorpher(simulation)
    
    # Setup physical constants and multi-phase model (g, rho, nu, mu)
    setup_physical_properties(simulation)
    
    # Load the boundary conditions. This must be done
    # before creating the solver as the solver needs
    # the Neumann conditions to define weak forms
    setup_boundary_conditions(simulation)
    
    # Create the solver
    simulation.log.info('Creating Navier-Stokes solver')
    solver_name = simulation.input.get_value('solver/type', required_type='string')
    simulation.solver = get_solver(solver_name)(simulation)
    
    # Setup postprocessing probes
    setup_probes(simulation)
    
    # Initialise the fields
    if not simulation.restarted:
        setup_initial_conditions(simulation)
    
    # Setup any hooks that may be present on the input file
    setup_hooks(simulation)
    
    # Setup the debug console to optionally run at the end of each timestep
    simulation.hooks.add_post_timestep_hook(lambda: debug_console_hook(simulation), 'Debug console')
    
    # Setup the summary to show after the simulation
    hook = lambda success: summarise_simulation_after_running(simulation, success)
    simulation.hooks.add_post_simulation_hook(hook, 'Summarise simulation')
    
    # Show time spent setting up the solver
    simulation.log.info('\nPreparing simulation done in %.3f seconds' % (time.time() - t_start))
    
    # Show all registered hooks
    simulation.hooks.show_hook_info()


def load_mesh(simulation):
    """
    Get the mesh from the simulation input
    
    Returns the facet regions contained in the mesh data
    or None if these do not exist
    """
    inp = simulation.input
    mesh_type = inp.get_value('mesh/type', required_type='string')
    
    dolfin.parameters['ghost_mode'] = 'shared_vertex'
    if mesh_type == 'Rectangle':
        simulation.log.info('Creating rectangular mesh')
        
        startx = inp.get_value('mesh/startx', 0, 'float')
        starty = inp.get_value('mesh/starty', 0, 'float')
        start = dolfin.Point(startx, starty)
        endx = inp.get_value('mesh/endx', 1, 'float')
        endy = inp.get_value('mesh/endy', 1, 'float')
        end = dolfin.Point(endx, endy)
        Nx = inp.get_value('mesh/Nx', required_type='int')
        Ny = inp.get_value('mesh/Ny', required_type='int')
        diagonal = inp.get_value('mesh/diagonal', 'right', required_type='string')
        
        mesh = dolfin.RectangleMesh(start, end, Nx, Ny, diagonal)
        mesh_facet_regions = None
    
    elif mesh_type == 'XML':
        simulation.log.info('Creating mesh from XML file')
        
        mesh_file = inp.get_value('mesh/mesh_file', required_type='string')
        facet_region_file = inp.get_value('mesh/facet_region_file', None, required_type='string')
        
        # Load the mesh from file
        pth = inp.get_input_file_path(mesh_file)
        mesh = dolfin.Mesh(pth)
        
        # Load the facet regions if available
        if facet_region_file is not None:
            pth = inp.get_input_file_path(facet_region_file)
            mesh_facet_regions = dolfin.MeshFunction('size_t', mesh, pth)
        else:
            mesh_facet_regions = None
    
    simulation.set_mesh(mesh, mesh_facet_regions)


def mark_boundaries(simulation):
    """
    Mark the boundaries of the mesh with different numbers to be able to
    apply different boundary conditions to different regions 
    """
    simulation.log.info('Creating boundary regions')
    
    # Create a function to mark the external facets
    marker = dolfin.FacetFunction("size_t", simulation.data['mesh'])
    mesh_facet_regions = simulation.data['mesh_facet_regions']
    
    # Create boundary regions and let them mark the part of the
    # boundary that they belong to. They also create boundary
    # condition objects that are later used in the eq. solvers
    boundary = []
    for index, _ in enumerate(simulation.input.get_value('boundary_conditions', required_type='list(dict)')):
        part = BoundaryRegion(simulation, marker, index, mesh_facet_regions)
        boundary.append(part)
    simulation.data['boundary'] = boundary
    simulation.data['boundary_marker'] = marker
    
    # Create a boundary measure that is aware of the marked regions
    ds = dolfin.Measure('ds')(subdomain_data=marker)
    simulation.data['ds'] = ds


def setup_periodic_domain(simulation):
    """
    We need to create a constrained domain in case there are periodic 
    boundary conditions.
    """
    simulation.log.info('Creating periodic boundary conditions (if specified)')
    
    # This will be overwritten if there are periodic boundary conditions
    simulation.data['constrained_domain'] = None
    
    for region in simulation.data['boundary']:
        region.create_periodic_boundary_conditions()


def setup_function_spaces(simulation):
    """
    Create function spaces for velocity and pressure
    """
    # Get function space names
    Vu_name = simulation.input.get_value('solver/function_space_velocity', 'Lagrange', 'string')
    Vp_name = simulation.input.get_value('solver/function_space_pressure', 'Lagrange', 'string')
    
    # Get function space polynomial degrees
    Pu = simulation.input.get_value('solver/polynomial_degree_velocity', 1, 'int')
    Pp = simulation.input.get_value('solver/polynomial_degree_pressure', 1, 'int')
    
    # Get the constrained domain
    cd = simulation.data['constrained_domain']
    if cd is None:
        simulation.log.info('Creating function spaces without periodic boundaries (none found)')
    else:
        simulation.log.info('Creating function spaces with periodic boundaries')
    
    # Create the function spaces
    mesh = simulation.data['mesh']
    Vu = dolfin.FunctionSpace(mesh, Vu_name, Pu, constrained_domain=cd)
    Vp = dolfin.FunctionSpace(mesh, Vp_name, Pp, constrained_domain=cd)
    simulation.data['Vu'] = Vu
    simulation.data['Vp'] = Vp
    
    # Setup colour function if it is needed in the simulation
    multiphase_model_name = simulation.input.get_value('multiphase_solver/type', 'SinglePhase', 'string')
    if multiphase_model_name != 'SinglePhase':
        # Get the function space name and polynomial degree
        Vc_name = simulation.input.get_value('multiphase_solver/function_space_colour',
                                             'Discontinuous Lagrange', 'string')
        Pc = simulation.input.get_value('multiphase_solver/polynomial_degree_colour', 0, 'int')
        
        # Create and store function space
        Vc = dolfin.FunctionSpace(mesh, Vc_name, Pc, constrained_domain=cd)
        simulation.data['Vc'] = Vc
        

def setup_physical_properties(simulation):
    """
    Gravity vector and rho/nu/mu fields are created here
    """
    ndim = simulation.ndim
    g = simulation.input.get_value('physical_properties/g', [0]*ndim, required_type='list(float)')
    assert len(g) == simulation.ndim
    simulation.data['g'] = OcellarisConstant(g)
    
    # Get the density and viscosity properties from the multi phase model
    multiphase_model_name = simulation.input.get_value('multiphase_solver/type', 'SinglePhase', 'string')
    multiphase_model = get_multi_phase_model(multiphase_model_name)(simulation)
    simulation.data['rho'] = multiphase_model.get_density(0)
    simulation.data['nu'] = multiphase_model.get_laminar_kinematic_viscosity(0)
    simulation.data['mu'] = multiphase_model.get_laminar_dynamic_viscosity(0)
    simulation.multi_phase_model = multiphase_model
    
    # Previous and forcasted values of the fluid parameters
    simulation.data['rho_old'] = multiphase_model.get_density(-1)
    simulation.data['rho_star'] = multiphase_model.get_density(1)
    simulation.data['nu_old'] = multiphase_model.get_laminar_kinematic_viscosity(-1)
    simulation.data['nu_star'] = multiphase_model.get_laminar_kinematic_viscosity(1)
    simulation.data['mu_old'] = multiphase_model.get_laminar_dynamic_viscosity(-1)
    simulation.data['mu_star'] = multiphase_model.get_laminar_dynamic_viscosity(1)


def setup_boundary_conditions(simulation):
    """
    Setup boundary conditions based on the simulation input
    """
    simulation.log.info('Creating boundary conditions')
    
    # Make dicts to gather Dirichlet and Neumann boundary conditions
    simulation.data['dirichlet_bcs'] = {}
    simulation.data['neumann_bcs'] = {}
    
    for region in simulation.data['boundary']:
        region.create_boundary_conditions()


def setup_initial_conditions(simulation):
    """
    Setup the initial values for the fields
    """
    simulation.log.info('Creating initial conditions')
    
    ic = simulation.input.get_value('initial_conditions', {}, 'dict(string:dict)')
    for name, info in ic.items():
        name = str(name)
        
        if not 'p' in name:
            ocellaris_error('Invalid initial condition',
                            'You have given initial conditions for %r but this does '
                            'not seem to be a previous or pressure field.\n\n'
                            'Valid names: up0, up1, ... , p, cp, ...' % name)
        
        if not 'cpp_code' in info:
            ocellaris_error('Invalid initial condition',
                            'You have not given "cpp_code" for %r' % name)
        
        cpp_code = str(info['cpp_code'])
        
        if not name in simulation.data:
            ocellaris_error('Invalid initial condition',
                            'You have given initial conditions for %r but this does '
                            'not seem to be an existing field.' % name)
        
        func = simulation.data[name]
        V = func.function_space()
        description = 'initial conditions for %r' % name
        simulation.log.info('    C++ %s' % description)
        
        # Project into the function
        ocellaris_project(simulation, cpp_code, description, V, func)


def setup_hooks(simulation):
    """
    Install the hooks that are given on the input file
    """
    simulation.log.info('Registering user-defined hooks')
    
    hooks = simulation.input.get_value('hooks', {}, 'dict(string:list)')
    
    def make_hook_from_code_string(code_string, description):
        runnable = RunnablePythonString(simulation, code_string, description)
        hook_data = {}
        def hook(*args, **kwargs):
            runnable.run(hook_data=hook_data)
        return hook
    
    hook_types = [('pre-simulation', simulation.hooks.add_pre_simulation_hook),
                  ('post-simulation', simulation.hooks.add_post_simulation_hook),
                  ('pre_timestep', simulation.hooks.add_pre_timestep_hook),
                  ('post_timestep', simulation.hooks.add_post_timestep_hook)]
    
    for hook_name, register_hook in hook_types:
        for hook_info in hooks.get(hook_name, []):
            name = hook_info.get('name', 'unnamed')
            enabled = hook_info.get('enabled', True)
            description = '%s hook "%s"' % (hook_name, name)
            
            if not enabled:
                simulation.log.info('    Skipping disabled %s' % description)
                continue
            
            simulation.log.info('    Adding %s' % description)
            code_string = hook_info['code']
            hook = make_hook_from_code_string(code_string, description)
            register_hook(hook, 'User defined hook "%s"' % name)
            simulation.log.info('        ' + description)


def summarise_simulation_after_running(simulation, success):
    """
    Print a summary of the time spent on each part of the simulation
    """
    simulation.log.debug('\nGlobal simulation data at end of simulation:')
    for key, value in sorted(simulation.data.items()):
        simulation.log.debug('%20s = %s' % (key, repr(type(value))[:57]))
    
    # Add details on the time spent in each part of the simulation to the log
    log_timings(simulation)
    
    # Show the total duration
    tottime = time.time() - simulation.t_start
    h = int(tottime/60**2)
    m = int((tottime - h*60**2)/60)
    s = tottime - h*60**2 - m*60
    humantime = '%d hours %d minutes and %d seconds' % (h, m, s)
    simulation.log.info('\nSimulation done in %.3f seconds (%s)' % (tottime, humantime))
    
    simulation.log.info("\nCurrent time: %s" % time.strftime('%Y-%m-%d %H:%M:%S'))