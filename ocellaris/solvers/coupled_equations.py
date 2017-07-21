# encoding: utf8
from __future__ import division
import dolfin
from dolfin import dx, div, grad, dot, jump, avg, dS
from . import UPWIND
from ..solver_parts import navier_stokes_stabilization_penalties
from .coupled_equations_cg import CoupledEquationsCG


class CoupledEquationsDG(object):
    use_strong_bcs = False
    
    def __init__(self, simulation, flux_type, use_stress_divergence_form,
                 use_grad_p_form, use_grad_q_form, use_lagrange_multiplicator, 
                 pressure_continuity_factor, velocity_continuity_factor_D12,
                 include_hydrostatic_pressure, incompressibility_flux_type):
        """
        Weak form of the Navier-Stokes eq. on coupled form with discontinuous elements
        
        :type simulation: ocellaris.Simulation
        """
        self.simulation = simulation
        self.use_stress_divergence_form = use_stress_divergence_form
        self.use_grad_p_form = use_grad_p_form
        self.use_grad_q_form = use_grad_q_form
        self.flux_type = flux_type
        self.use_lagrange_multiplicator = use_lagrange_multiplicator
        self.pressure_continuity_factor =  pressure_continuity_factor
        self.velocity_continuity_factor_D12 = velocity_continuity_factor_D12
        self.include_hydrostatic_pressure = include_hydrostatic_pressure
        self.incompressibility_flux_type = incompressibility_flux_type

        assert self.incompressibility_flux_type in ('central', 'upwind')
        
        # Create UFL forms
        self.define_coupled_equation()
        
    def define_coupled_equation(self):
        """
        Setup the coupled Navier-Stokes equation
        
        This implementation assembles the full LHS and RHS each time they are needed
        """
        Vcoupled = self.simulation.data['Vcoupled']
        
        # Unpack the coupled trial and test functions
        uc = dolfin.TrialFunction(Vcoupled)
        vc = dolfin.TestFunction(Vcoupled)
        ulist = []; vlist = []
        ndim = self.simulation.ndim
        for d in range(ndim):
            ulist.append(uc[d])
            vlist.append(vc[d])
        
        u = dolfin.as_vector(ulist)
        v = dolfin.as_vector(vlist)
        p = uc[ndim]
        q = vc[ndim]
        
        lm_trial = lm_test = None
        if self.use_lagrange_multiplicator:
            lm_trial = uc[ndim+1]
            lm_test = vc[ndim+1]
        
        assert self.flux_type == UPWIND
        eq = define_dg_equations(u, v, p, q, lm_trial, lm_test, self.simulation,
                                 include_hydrostatic_pressure=self.include_hydrostatic_pressure,
                                 incompressibility_flux_type=self.incompressibility_flux_type,
                                 use_grad_q_form=self.use_grad_q_form,
                                 use_grad_p_form=self.use_grad_p_form,
                                 use_stress_divergence_form=self.use_stress_divergence_form,
                                 velocity_continuity_factor_D12=self.velocity_continuity_factor_D12,
                                 pressure_continuity_factor=self.pressure_continuity_factor)
        
        a, L = dolfin.system(eq)
        self.form_lhs = a
        self.form_rhs = L
        self.tensor_lhs = None
        self.tensor_rhs = None
    
    def assemble_lhs(self):
        if self.tensor_lhs is None:
            self.tensor_lhs = dolfin.assemble(self.form_lhs)
        else:
            dolfin.assemble(self.form_lhs, tensor=self.tensor_lhs)
        return self.tensor_lhs

    def assemble_rhs(self):
        if self.tensor_rhs is None:
            self.tensor_rhs = dolfin.assemble(self.form_rhs)
        else:
            dolfin.assemble(self.form_rhs, tensor=self.tensor_rhs)
        return self.tensor_rhs


def define_dg_equations(u, v, p, q, lm_trial, lm_test, simulation,
                        include_hydrostatic_pressure, incompressibility_flux_type,
                        use_grad_q_form, use_grad_p_form, use_stress_divergence_form,
                        velocity_continuity_factor_D12=0, pressure_continuity_factor=0):
    """
    Define the coupled equations. Also in use by the SIMPLE solver
    
    Weak form of the Navier-Stokes eq. with discontinuous elements
    
    :type simulation: ocellaris.Simulation
    """
    sim = simulation
    mpm = sim.multi_phase_model
    mesh = sim.data['mesh']
    u_conv = sim.data['u_conv']
    
    c1, c2, c3 = sim.data['time_coeffs']
    dt = sim.data['dt']
    g = sim.data['g']
    n = dolfin.FacetNormal(mesh)
    
    # Fluid properties
    rho = mpm.get_density(0)
    nu = mpm.get_laminar_kinematic_viscosity(0)
    mu = mpm.get_laminar_dynamic_viscosity(0)
    
    # Hydrostatic pressure correction
    if include_hydrostatic_pressure:
        p += sim.data['p_hydrostatic']
    
    # Start building the coupled equations
    eq = 0
    
    # ALE mesh velocities
    u_mesh = dolfin.Constant([0]*sim.ndim)
    if sim.mesh_morpher.active:
        u_mesh = sim.data['u_mesh']
        
        # Modification of the convective velocity
        #u_conv -= u_mesh
        eq -= dot(div(rho*dolfin.outer(u, u_mesh)), v)*dx
        
        # Divergence of u should balance expansion/contraction of the cell K
        # ∇⋅u = -∂x/∂t       (See below for definition of the ∇⋅u term)
        cvol_new = dolfin.CellVolume(mesh)
        cvol_old = sim.data['cvolp']  
        eq += (cvol_new - cvol_old)/dt*q*dx
    
    # Elliptic penalties
    penalty_dS, penalty_ds, D11, D12 = navier_stokes_stabilization_penalties(sim, nu, 
         velocity_continuity_factor_D12, pressure_continuity_factor)
    
    # Upwind and downwind velocities
    w_nU = (dot(u_conv, n) + abs(dot(u_conv, n)))/2.0
    w_nD = (dot(u_conv, n) - abs(dot(u_conv, n)))/2.0

    # Lagrange multiplicator to remove the pressure null space
    # ∫ p dx = 0
    if lm_trial is not None:
        eq = (p*lm_test + q*lm_trial)*dx
    
    # Momentum equations
    for d in range(sim.ndim):
        up = sim.data['up%d' % d]
        upp = sim.data['upp%d' % d]
        
        # Divergence free criterion
        # ∇⋅u = 0
        if incompressibility_flux_type == 'central':
            u_hat_p = avg(u[d])
        elif incompressibility_flux_type == 'upwind':
            assert use_grad_q_form, 'Upwind only implemented for grad_q_form'
            switch = dolfin.conditional(dolfin.gt(w_nU('+'), 0.0), 1.0, 0.0)
            u_hat_p = switch*u[d]('+') + (1 - switch)*u[d]('-')
        
        if use_grad_q_form:
            eq -= u[d]*q.dx(d)*dx
            eq += (u_hat_p + D12[d]*jump(u, n))*jump(q)*n[d]('+')*dS
        else:
            eq += q*u[d].dx(d)*dx
            eq -= (avg(q) - dot(D12, jump(q, n)))*jump(u[d])*n[d]('+')*dS
        
        # Time derivative
        # ∂(ρu)/∂t
        eq += rho*(c1*u[d] + c2*up + c3*upp)/dt*v[d]*dx
        
        # Convection:
        # -w⋅∇(ρu)
        flux_nU = u[d]*w_nU
        flux = jump(flux_nU)
        eq -= u[d]*dot(grad(rho*v[d]), u_conv)*dx
        eq += flux*jump(rho*v[d])*dS
        
        # Stabilizing term when w is not divergence free
        eq += 1/2*div(u_conv)*u[d]*v[d]*dx
        
        # ALE terms
        if sim.mesh_morpher.active:
            ud = u[d]
            um = -u_mesh
            u_mesh_nU = (dot(um, n) + abs(dot(um, n)))/2.0
            flux_mesh_nU = rho*ud*u_mesh_nU
            flux_mesh = jump(flux_mesh_nU)
            eq -= rho*ud*div(v[d]*um)*dx
            eq += flux_mesh*jump(v[d])*dS
        
        # Diffusion:
        # -∇⋅∇u
        eq += mu*dot(grad(u[d]), grad(v[d]))*dx
        
        # Symmetric Interior Penalty method for -∇⋅μ∇u
        eq -= avg(mu)*dot(n('+'), avg(grad(u[d])))*jump(v[d])*dS
        eq -= avg(mu)*dot(n('+'), avg(grad(v[d])))*jump(u[d])*dS
        
        # Symmetric Interior Penalty coercivity term
        eq += penalty_dS*jump(u[d])*jump(v[d])*dS
        
        # -∇⋅μ(∇u)^T
        if use_stress_divergence_form:
            eq += mu*dot(u.dx(d), grad(v[d]))*dx
            eq -= avg(mu)*dot(n('+'), avg(u.dx(d)))*jump(v[d])*dS
            eq -= avg(mu)*dot(n('+'), avg(v.dx(d)))*jump(u[d])*dS
        
        # Pressure
        # ∇p
        if use_grad_p_form:
            eq += v[d]*p.dx(d)*dx
            eq -= (avg(v[d]) + D12[d]*jump(v, n))*jump(p)*n[d]('+')*dS
        else:
            eq -= p*v[d].dx(d)*dx
            eq += (avg(p) - dot(D12, jump(p, n)))*jump(v[d])*n[d]('+')*dS
        
        # Pressure continuity stabilization. Needed for equal order discretization
        if D11 is not None:
            eq += D11*dot(jump(p, n), jump(q, n))*dS
        
        # Body force (gravity)
        # ρ g
        eq -= rho*g[d]*v[d]*dx
        
        # Other sources
        for f in sim.data['momentum_sources']:
            eq -= f[d]*v[d]*dx
        
        # Dirichlet boundary
        dirichlet_bcs = sim.data['dirichlet_bcs'].get('u%d' % d, [])
        for dbc in dirichlet_bcs:
            u_bc = dbc.func()
            
            # Divergence free criterion
            if use_grad_q_form:
                eq += q*u_bc*n[d]*dbc.ds()
            else:
                eq -= q*u[d]*n[d]*dbc.ds()
                eq += q*u_bc*n[d]*dbc.ds()
            
            # Convection
            eq += rho*u[d]*w_nU*v[d]*dbc.ds()
            eq += rho*u_bc*w_nD*v[d]*dbc.ds()
            
            # SIPG for -∇⋅μ∇u
            eq -= mu*dot(n, grad(u[d]))*v[d]*dbc.ds()
            eq -= mu*dot(n, grad(v[d]))*u[d]*dbc.ds()
            eq += mu*dot(n, grad(v[d]))*u_bc*dbc.ds()
            
            # Weak Dirichlet
            eq += penalty_ds*(u[d] - u_bc)*v[d]*dbc.ds()
            
            # Pressure
            if not use_grad_p_form:
                eq += p*v[d]*n[d]*dbc.ds()
        
        # Neumann boundary
        neumann_bcs = sim.data['neumann_bcs'].get('u%d' % d, [])
        for nbc in neumann_bcs:
            # Divergence free criterion
            if use_grad_q_form:
                eq += q*u[d]*n[d]*nbc.ds()
            else:
                eq -= q*u[d]*n[d]*nbc.ds()
            
            # Convection
            eq += rho*u[d]*w_nU*v[d]*nbc.ds()
            
            # Diffusion
            eq -= mu*nbc.func()*v[d]*nbc.ds()
            
            # Pressure
            if not use_grad_p_form:
                eq += p*v[d]*n[d]*nbc.ds()
        
        # Outlet boundary
        for obc in sim.data['outlet_bcs']:
            # Divergence free criterion
            if use_grad_q_form:
                eq += q*u[d]*n[d]*obc.ds()
            else:
                eq -= q*u[d]*n[d]*obc.ds()
            
            # Convection
            eq += rho*u[d]*w_nU*v[d]*obc.ds()
            
            # Diffusion
            mu_dudn = p*n[d]
            eq -= mu_dudn*v[d]*obc.ds()
            
            # Pressure
            if not use_grad_p_form:
                p_ = mu*dot(dot(grad(u), n), n)
                eq += p_*n[d]*v[d]*obc.ds()
    
    return eq


EQUATION_SUBTYPES = {
    'Default': CoupledEquationsDG,
    'DG': CoupledEquationsDG,
    'CG': CoupledEquationsCG
}
