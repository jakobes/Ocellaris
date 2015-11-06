from __future__ import division
import dolfin
from ocellaris.utils import report_error, timeit, linear_solver_from_input
from . import Solver, register_solver, BDF, CRANK_NICOLSON, BDM, UPWIND, SolverError
from .ipcs_equations import EQUATION_SUBTYPES
from .dg_helpers import VelocityBDMProjection


# Solvers - default values, can be changed in the input file
SOLVER_U = 'gmres'
PRECONDITIONER_U = 'additive_schwarz'
SOLVER_P = 'gmres'
PRECONDITIONER_P = 'hypre_amg'
KRYLOV_PARAMETERS = {'nonzero_initial_guess': True,
                     'relative_tolerance': 1e-10,
                     'absolute_tolerance': 1e-15}
MAX_ITER_MOMENTUM = 1000

# Equations - default values, can be changed in the input file
TIMESTEPPING_METHODS = (BDF,)
EQUATION_SUBTYPE = 'Default'
USE_STRESS_DIVERGENCE = False
USE_LAGRANGE_MULTIPLICATOR = False
USE_GRAD_P_FORM = False


@register_solver('IPCS')
class SolverIPCS(Solver):
    def __init__(self, simulation):
        """
        A Navier-Stokes solver based on the pressure-velocity splitting
        scheme IPCS (Incremental Pressure Correction Scheme)
        """
        self.simulation = sim = simulation
        self.read_input()
        self.create_functions()
        
        # First time step timestepping coefficients
        sim.data['time_coeffs'] = dolfin.Constant([1, -1, 0])
        self.is_first_timestep = True
        
        # Solver control parameters
        sim.data['dt'] = dolfin.Constant(simulation.dt)
        
        # Hydrostatic pressure
        rho_min, rho_max = sim.multi_phase_model.get_density_range()
        self.hydrostatic_pressure_correction = rho_min != rho_max
        if self.hydrostatic_pressure_correction:
            rho = sim.data['rho_star']
            g = sim.data['g']
            ph = sim.data['p_hydrostatic']
            default_vertical_direction = sim.ndim - 1
            vertical_direction = sim.input.get_value('multiphase_solver/vertical_direction',
                                                     default_vertical_direction, 'float')
            sky_location = sim.input.get_value('multiphase_solver/sky_location', required_type='float')
            self.hydrostatic_pressure = HydrostaticPressure(rho, g, ph, vertical_direction, sky_location)
        
        # Get equations
        MomentumPredictionEquation, PressureCorrectionEquation, \
            VelocityUpdateEquation = EQUATION_SUBTYPES[self.equation_subtype]
        
        # Define the momentum prediction equations
        self.eqs_mom_pred = []
        for d in range(sim.ndim):
            eq = MomentumPredictionEquation(simulation, d,
                                            timestepping_method=self.timestepping_method,
                                            flux_type=self.flux_type,
                                            use_stress_divergence_form=self.use_stress_divergence_form,
                                            use_grad_p_form=self.use_grad_p_form,
                                            include_hydrostatic_pressure=self.hydrostatic_pressure_correction)
            self.eqs_mom_pred.append(eq)
        
        # Define the pressure correction equation
        self.eq_pressure = PressureCorrectionEquation(simulation, self.use_lagrange_multiplicator)
        
        # Define the velocity update equations
        self.eqs_vel_upd = []
        for d in range(sim.ndim):
            eq = VelocityUpdateEquation(simulation, d)
            self.eqs_vel_upd.append(eq)
        
        # Projection for the velocity
        self.velocity_postprocessor = None
        if self.velocity_postprocessing == BDM:
            self.velocity_postprocessor = VelocityBDMProjection(sim.data['u'])
        
        # Storage for preassembled matrices
        self.Au = [None]*sim.ndim
        self.Ap = None
        self.Au_upd = None
        self.pressure_null_space = None
        
        # Store number of iterations
        self.niters_u = [None] * sim.ndim
        self.niters_p = None
        self.niters_u_upd = [None] * sim.ndim
    
    def read_input(self):
        """
        Read the simulation input
        """
        sim = self.simulation
        
        # Representation of velocity
        Vu_family = sim.data['Vu'].ufl_element().family()
        self.vel_is_discontinuous = (Vu_family == 'Discontinuous Lagrange')
        
        # Create linear solvers
        self.velocity_solver = linear_solver_from_input(self.simulation, 'solver/u', SOLVER_U,
                                                        PRECONDITIONER_U, None, KRYLOV_PARAMETERS)
        self.pressure_solver = linear_solver_from_input(self.simulation, 'solver/p', SOLVER_P,
                                                        PRECONDITIONER_P, None, KRYLOV_PARAMETERS)
        self.pressure_solver.parameters['preconditioner']['structure'] = 'same'
        
        # Velocity update can be performed with local solver for DG velocities
        self.use_local_solver_for_update = sim.input.get_value('solver/u_upd', self.vel_is_discontinuous, 'bool')
        if self.use_local_solver_for_update:
            self.u_upd_solver = None # Will be set when LHS is ready
        else:
            self.u_upd_solver = linear_solver_from_input(self.simulation, 'solver/u_upd', SOLVER_U,
                                                         PRECONDITIONER_U, None, KRYLOV_PARAMETERS)
        
        # Get the class to be used for the equation system assembly
        self.equation_subtype = sim.input.get_value('solver/equation_subtype', EQUATION_SUBTYPE, 'string')
        if not self.equation_subtype in EQUATION_SUBTYPES:
            available_methods = '\n'.join(' - %s' % m for m in EQUATION_SUBTYPES)
            report_error('Unknown equation sub-type', 
                         'Equation sub-type %s not available for ipcs solver, please use one of:\n%s' %
                         (self.equation_subtype, EQUATION_SUBTYPES))
        
        # Coefficients for u, up and upp
        self.timestepping_method = sim.input.get_value('solver/timestepping_method', BDF, 'string')
        if not self.timestepping_method in TIMESTEPPING_METHODS:
            available_methods = '\n'.join(' - %s' % m for m in TIMESTEPPING_METHODS)
            report_error('Unknown timestepping method', 
                         'Timestepping method %s not recognised, please use one of:\n%s' %
                         (self.timestepping_method, available_methods))
        
        # Lagrange multiplicator or remove null space via PETSc
        self.remove_null_space = True
        self.pressure_null_space = None
        self.use_lagrange_multiplicator = sim.input.get_value('solver/use_lagrange_multiplicator',
                                                              USE_LAGRANGE_MULTIPLICATOR, 'bool')
        if self.use_lagrange_multiplicator or self.simulation.data['dirichlet_bcs'].get('p', []):
            self.remove_null_space = False
        
        # No need for special treatment if the pressure is set via Dirichlet conditions somewhere
        if self.simulation.data['dirichlet_bcs'].get('p', []):
            self.use_lagrange_multiplicator = False
            self.remove_null_space = False
        
        # Control the form of the governing equations 
        self.flux_type = sim.input.get_value('convection/u/flux_type', UPWIND, 'string')
        self.use_stress_divergence_form = sim.input.get_value('solver/use_stress_divergence_form',
                                                              USE_STRESS_DIVERGENCE, 'bool')
        self.use_grad_p_form = sim.input.get_value('solver/use_grad_p_form', USE_GRAD_P_FORM, 'bool')
        
        # Velocity post_processing
        default_postprocessing = BDM if self.vel_is_discontinuous else None
        self.velocity_postprocessing = sim.input.get_value('solver/velocity_postprocessing', default_postprocessing, 'string')
        
        # Quasi-steady simulation input
        self.steady_velocity_eps = sim.input.get_value('solver/steady_velocity_stopping_criterion',
                                                       None, 'float')
        self.is_steady = self.steady_velocity_eps is not None
    
    def create_functions(self):
        """
        Create functions to hold solutions
        """
        sim = self.simulation
        
        # Function spaces
        Vu = sim.data['Vu']
        Vp = sim.data['Vp']
        
        # Create velocity functions. Keep both component and vector forms
        u_list, up_list, upp_list, u_conv, u_star = [], [], [], [], []
        for d in range(sim.ndim):
            sim.data['u%d' % d] = u = dolfin.Function(Vu)
            sim.data['up%d' % d] = up = dolfin.Function(Vu)
            sim.data['upp%d' % d] = upp = dolfin.Function(Vu)
            sim.data['uppp%d' % d] = dolfin.Function(Vu)
            sim.data['u_conv%d' % d] = uc = dolfin.Function(Vu)
            sim.data['u_star%d' % d] = us = dolfin.Function(Vu)
            u_list.append(u)
            up_list.append(up)
            upp_list.append(upp)
            u_conv.append(uc)
            u_star.append(us)
        sim.data['u'] = dolfin.as_vector(u_list)
        sim.data['up'] = dolfin.as_vector(up_list)
        sim.data['upp'] = dolfin.as_vector(upp_list)
        sim.data['u_conv'] = dolfin.as_vector(u_conv)
        sim.data['u_star'] = dolfin.as_vector(u_star)
        self.u_tmp = dolfin.Function(Vu)
        
        # Create pressure function
        sim.data['p'] = dolfin.Function(Vp)
        sim.data['p_hat'] = dolfin.Function(Vp)
        
        # Hydrostatic pressure is always CG
        Pp = Vp.ufl_element().degree()
        Vph = dolfin.FunctionSpace(sim.data['mesh'], 'CG', Pp)
        sim.data['p_hydrostatic'] = dolfin.Function(Vph)
    
    @timeit
    def update_convection(self, t, dt):
        """
        Update terms used to linearise and discretise the convective term
        """
        ndim = self.simulation.ndim
        data = self.simulation.data
        
        # Update convective velocity field components
        for d in range(ndim):
            uic = data['u_conv%d' % d]
            uip =  data['up%d' % d]
            uipp = data['upp%d' % d]
            
            if self.is_first_timestep:
                uic.vector()[:] = uip.vector()[:]
            elif self.timestepping_method == BDF:
                uic.vector()[:] = 2.0*uip.vector()[:] - 1.0*uipp.vector()[:]
            elif self.timestepping_method == CRANK_NICOLSON:
                # These two methods seem to give exactly the same results
                # Ingram (2013) claims that the first has better stability properties
                # in "A new linearly extrapolated Crank-Nicolson time-stepping scheme 
                # for the Navier-Stokes equations" 
                
                # Ingram's Crank-Nicolson extrapolation method
                #uippp = data['uppp%d' % d]
                #uic.vector()[:] = uip.vector()[:] + 0.5*uipp.vector()[:] - 0.5*uippp.vector()[:]
                
                # Standard Crank-Nicolson linear extrapolation method
                uic.vector()[:] = 1.5*uip.vector()[:] - 0.5*uipp.vector()[:]
        
        self.is_first_timestep = False
    
    @timeit
    def momentum_prediction(self, t, dt):
        """
        Solve the momentum prediction equation
        """
        solver = self.velocity_solver
        
        err = 0.0
        for d in range(self.simulation.ndim):
            us = self.simulation.data['u_star%d' % d]
            self.u_tmp.assign(us)
            
            dirichlet_bcs = self.simulation.data['dirichlet_bcs'].get('u%d' % d, [])
            eq = self.eqs_mom_pred[d]
            
            if self.inner_iteration == 1:
                # Assemble the A matrix only the first inner iteration
                self.Au[d] = eq.assemble_lhs()
                self.velocity_solver.parameters['preconditioner']['structure'] = 'same_nonzero_pattern'
            else:
                self.velocity_solver.parameters['preconditioner']['structure'] = 'same'
            
            A = self.Au[d]
            b = eq.assemble_rhs()
            
            if not self.vel_is_discontinuous:
                for dbc in dirichlet_bcs:
                    dbc.apply(A, b)
            
            solver.parameters['maximum_iterations'] = MAX_ITER_MOMENTUM
            self.niters_u[d] = solver.solve(A, us.vector(), b)
            
            self.u_tmp.vector().axpy(-1, us.vector())
            err += self.u_tmp.vector().norm('l2')
        return err
    
    @timeit
    def pressure_correction(self):
        """
        Solve the pressure correction equation
        
        We handle the case where only Neumann conditions are given
        for the pressure by taking out the nullspace, a constant shift
        of the pressure, by providing the nullspace to the solver
        """
        p = self.simulation.data['p']
        dirichlet_bcs = self.simulation.data['dirichlet_bcs'].get('p', [])
        
        # Assemble the A matrix only the first inner iteration
        if self.inner_iteration == 1:
            self.Ap = self.eq_pressure.assemble_lhs()
        
        # The equation system to solve
        A = self.Ap
        b = self.eq_pressure.assemble_rhs()
        
        # Apply strong boundary conditions
        if not self.vel_is_discontinuous:
            for dbc in dirichlet_bcs:
                dbc.apply(A, b)
        
        if self.remove_null_space:
            if self.pressure_null_space is None:
                # Create vector that spans the null space
                null_vec = dolfin.Vector(p.vector())
                null_vec[:] = 1
                null_vec *= 1/null_vec.norm("l2")
                
                # Create null space basis object
                self.pressure_null_space = dolfin.VectorSpaceBasis([null_vec])
            
            # Make sure the null space is set on the matrix
            dolfin.as_backend_type(A).set_nullspace(self.pressure_null_space)
            
            # Orthogonalize b with respect to the null space
            self.pressure_null_space.orthogonalize(b)
        
        # Temporarily store the old pressure
        p_hat = self.simulation.data['p_hat']
        p_hat.vector().zero()
        p_hat.vector().axpy(-1, p.vector())
        
        # Solve for new pressure
        self.niters_p = self.pressure_solver.solve(A, p.vector(), b)
        
        # Removing the null space of the matrix system is not strictly the same as removing
        # the null space of the equation, so we correct for this here 
        if self.remove_null_space:
            dx2 = dolfin.dx(domain=p.function_space().mesh())
            vol = dolfin.assemble(dolfin.Constant(1)*dx2)
            pavg = dolfin.assemble(p*dx2)/vol
            p.vector()[:] -= pavg
        
        # Calculate p_hat = p_new - p_old 
        p_hat.vector().axpy(1, p.vector())
        
        return p_hat.vector().norm('l2')
    
    @timeit
    def velocity_update(self):
        """
        Update the velocity predictions with the updated pressure
        field from the pressure correction equation
        """
        if self.use_local_solver_for_update:
            # Element-wise projection
            if self.u_upd_solver is None:
                self.u_upd_solver = dolfin.LocalSolver(self.eqs_vel_upd[0].form_lhs)
                self.u_upd_solver.factorize()
            
            Vu = self.simulation.data['Vu']
            for d in range(self.simulation.ndim):
                eq = self.eqs_vel_upd[d]
                b = eq.assemble_rhs()
                u_new = self.simulation.data['u%d' % d]
                self.u_upd_solver.solve_local(u_new.vector(), b, Vu.dofmap())
                self.niters_u_upd[d] = 0
        
        else:
            # Global projection
            for d in range(self.simulation.ndim):
                eq = self.eqs_vel_upd[d]
                
                if self.Au_upd is None:
                    self.Au_upd = eq.assemble_lhs()
                
                A = self.Au_upd
                b = eq.assemble_rhs()
                u_new = self.simulation.data['u%d' % d]
                
                self.niters_u_upd[d] = self.u_upd_solver.solve(A, u_new.vector(), b)
    
    @timeit
    def postprocess_velocity(self):
        """
        Apply a post-processing operator to the given velocity field
        """
        if self.velocity_postprocessor:
            self.velocity_postprocessor.run()
    
    def run(self):
        """
        Run the simulation
        """
        sim = self.simulation        
        sim.hooks.simulation_started()
        t = sim.time
        it = sim.timestep
        
        # Check if there are non-zero values in the upp vectors
        maxabs = 0
        for d in range(sim.ndim):
            this_maxabs = abs(sim.data['upp%d' % d].vector().array()).max()
            maxabs = max(maxabs, this_maxabs)
        has_upp_start_values = maxabs > 0
        
        # Previous-previous values are provided so we can start up with second order time stepping 
        if has_upp_start_values:
            sim.log.info('Initial values for upp are found and used')
            self.is_first_timestep = False
            if self.timestepping_method == BDF:
                self.simulation.data['time_coeffs'].assign(dolfin.Constant([3/2, -2, 1/2]))
        
        # Give reasonable starting guesses for the solvers
        for d in range(sim.ndim):
            up = self.simulation.data['up%d' % d]
            u_new = self.simulation.data['u%d' % d]
            u_star = self.simulation.data['u_star%d' % d]
            u_new.assign(up)
            u_star.assign(up)
        
        while True:
            # Get input values, these can possibly change over time
            dt = sim.input.get_value('time/dt', required_type='float')
            tmax = sim.input.get_value('time/tmax', required_type='float')
            num_inner_iter = sim.input.get_value('solver/num_inner_iter', 1, 'int')
            allowable_error_inner = sim.input.get_value('solver/allowable_error_inner', 0, 'float')
            
            # Check if the simulation is done
            if t+dt > tmax + 1e-6:
                break
            
            # Advance one time step
            it += 1
            t += dt
            self.simulation.data['dt'].assign(dt)
            self.simulation.hooks.new_timestep(it, t, dt)
            
            # Extrapolate the convecting velocity to the new time step
            self.update_convection(t, dt)
            
            # Calculate the hydrostatic pressure when the density is not constant
            if self.hydrostatic_pressure_correction:
                self.hydrostatic_pressure.update()
            
            # Run inner iterations
            self.inner_iteration = 1
            while self.inner_iteration <= num_inner_iter:
                err_u_star = self.momentum_prediction(t, dt)
                err_p = self.pressure_correction()
                
                # Information from solvers regarding number of iterations needed to solve linear system
                niters = ['%3d u%d' % (ni, d) for d, ni in enumerate(self.niters_u)]
                niters.append('%3d p' % self.niters_p)
                solver_info = ' - iters: %s' % ' '.join(niters)
                
                # Convergence estimates
                sim.log.info('  Inner iteration %3d - Diff u* %10.3e - Diff p %10.3e%s'
                             % (self.inner_iteration, err_u_star, err_p, solver_info) + 
                             '  u0*max %10.3e' % abs(sim.data['u_star0'].vector().array()).max())
                
                if err_u_star < allowable_error_inner:
                    break
                
                self.inner_iteration += 1
            
            self.velocity_update()
            self.postprocess_velocity()
            
            # Move u -> up, up -> upp and prepare for the next time step
            vel_diff = 0
            for d in range(self.simulation.ndim):
                u_new = self.simulation.data['u%d' % d]
                up = self.simulation.data['up%d' % d]
                upp = self.simulation.data['upp%d' % d]
                uppp = self.simulation.data['uppp%d' % d]
                
                if self.is_steady:
                    diff = abs(u_new.vector().get_local() - up.vector().get_local()).max() 
                    vel_diff = max(vel_diff, diff)
                
                uppp.assign(upp)
                upp.assign(up)
                up.assign(u_new)
            
            # Change time coefficient to second order
            if self.timestepping_method == BDF:
                self.simulation.data['time_coeffs'].assign(dolfin.Constant([3/2, -2, 1/2]))
            
            # Stop steady state simulation if convergence has been reached
            if self.is_steady:
                sim.reporting.report_timestep_value('max(ui_new-ui_prev)', vel_diff)
                if vel_diff < self.steady_velocity_eps:
                    sim.log.info('Stopping simulation, steady state achieved')
                    sim.input.set_value('time/tmax', t)
            
            # Postprocess this time step
            sim.hooks.end_timestep()
        
        # We are done
        sim.hooks.simulation_ended(success=True)


class HydrostaticPressure(object):
    def __init__(self, rho, g, ph, vertical_direction, zero_level, eps=1e-8):
        """
        Calculate the hydrostatic pressure

        The gravity vector g must be parallel to one of th axes
        """
        Vp = ph.function_space()
        p = dolfin.TrialFunction(Vp)
        q = dolfin.TestFunction(Vp)
        d = vertical_direction
        
        a = p.dx(d)*q.dx(d)*dolfin.dx
        L = g[d]*rho*q.dx(d)*dolfin.dx
        
        inside = lambda  x, on_boundary: zero_level - eps <= x[d] <= zero_level + eps
        self.zero_bc = dolfin.DirichletBC(Vp, 0.0, inside)
        self.func = ph
        self.tensor_lhs = dolfin.assemble(a)
        self.form_rhs = L
    
    def update(self):
        t = dolfin.Timer('Ocellaris update hydrostatic pressure')
        
        A = self.tensor_lhs
        b = dolfin.assemble(self.form_rhs)
        self.zero_bc.apply(A, b)
        dolfin.solve(A, self.func.vector(), b)
        
        t.stop()
