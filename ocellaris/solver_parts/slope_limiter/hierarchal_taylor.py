# encoding: utf8
from __future__ import division
import numpy
import dolfin as df
from ocellaris.cpp import load_module
from ocellaris.utils import ocellaris_error, verify_key, get_dof_neighbours
from ocellaris.utils import lagrange_to_taylor, taylor_to_lagrange
from . import register_slope_limiter, SlopeLimiterBase


@register_slope_limiter('HierarchalTaylor')
class HierarchalTaylorSlopeLimiter(SlopeLimiterBase):
    description = 'Uses a Taylor DG decomposition to limit derivatives at the vertices in a hierarchal manner'
    
    def __init__(self, phi_name, phi, boundary_condition, filter_method='nofilter', use_cpp=True):
        """
        Limit the slope of the given scalar to obtain boundedness
        """
        # Verify input
        V = phi.function_space()
        mesh = V.mesh()
        family = V.ufl_element().family()
        degree = V.ufl_element().degree()
        loc = 'HierarchalTaylor slope limiter'
        verify_key('slope limited function', family, ['Discontinuous Lagrange'], loc)
        verify_key('slope limited degree', degree, (0, 1, 2), loc)
        verify_key('function shape', phi.ufl_shape, [()], loc)
        verify_key('topological dimension', mesh.topology().dim(), [2], loc)
        verify_key('filter', filter_method, ('nofilter',), loc)
        
        # Store input
        self.phi_name = phi_name
        self.phi = phi
        self.degree = degree
        self.mesh = mesh
        self.filter = filter_method
        self.use_cpp = use_cpp
        
        # Alpha factors are secondary outputs
        V0 = df.FunctionSpace(self.mesh, 'DG', 0)
        self.alpha_funcs = []
        for i in range(degree):
            func = df.Function(V0)
            name = 'SlopeLimiterAlpha%d_%s' % (i+1, phi_name)
            func.rename(name, name)
            self.alpha_funcs.append(func)
        self.additional_plot_funcs = self.alpha_funcs
        
        # Intermediate DG Taylor function space
        self.taylor = df.Function(V)
        
        # No limiter needed for piecewice constant functions
        if degree == 0:
            return
        
        # Find the neighbour cells for each dof
        num_neighbours, neighbours = get_dof_neighbours(V)
        self.num_neighbours = num_neighbours
        self.neighbours = neighbours
        
        # Remove boundary dofs from limiter
        num_neighbours[boundary_condition != 0] = 0
        
        # Fast access to cell dofs
        dm, dm0 = V.dofmap(), V0.dofmap()
        indices = range(self.mesh.num_cells())
        self.cell_dofs_V = [tuple(dm.cell_dofs(i)) for i in indices]
        self.cell_dofs_V0 = [int(dm0.cell_dofs(i)) for i in indices]
        
        # Find vertices for each cell
        mesh.init(2, 0)
        connectivity_CV = mesh.topology()(2, 0)
        vertices = []
        for ic in range(self.mesh.num_cells()):
            vnbs = tuple(connectivity_CV(ic))
            vertices.append(vnbs)
        self.vertices = vertices
        self.vertex_coordinates = mesh.coordinates()
    
    def run(self):
        """
        Perform slope limiting of DG Lagrange functions
        """
        # No limiter needed for piecewice constant functions
        if self.degree == 0:
            return
        elif self.degree == 1:
            return self._run_dg1()
        elif self.degree == 2:
            return self._run_dg2()
    
    def _run_dg1(self):
        """
        Perform slope limiting of a DG1 function
        """
        # Update the Taylor function space with the new DG values
        lagrange_to_taylor(self.phi, self.taylor)
        lagrange_vals = self.phi.vector().get_local()
        taylor_vals = self.taylor.vector().get_local()
        alphas = self.alpha_funcs[0].vector().get_local()
        
        V = self.phi.function_space()
        mesh = V.mesh()
        tdim = mesh.topology().dim()
        num_cells_owned = mesh.topology().ghost_offset(tdim)
        
        for icell in xrange(num_cells_owned):
            dofs = self.cell_dofs_V[icell]
            center_value = taylor_vals[dofs[0]]
            
            # Find the minimum slope limiter coefficient alpha 
            alpha = 1.0
            for i in xrange(3):
                dof = dofs[i]
                
                # Find vertex neighbours minimum and maximum values
                minval = maxval = center_value
                for nb in self.neighbours[dof]:
                    nb_center_val_dof = self.cell_dofs_V[nb][0]
                    nb_val = taylor_vals[nb_center_val_dof]
                    minval = min(minval, nb_val)
                    maxval = max(maxval, nb_val)
                
                vertex_value = lagrange_vals[dof]
                if vertex_value > center_value:
                    alpha = min(alpha, (maxval - center_value)/(vertex_value - center_value))
                elif vertex_value < center_value:
                    alpha = min(alpha, (minval - center_value)/(vertex_value - center_value))
            
            alphas[self.cell_dofs_V0[icell]] = alpha
            taylor_vals[dofs[1]] *= alpha
            taylor_vals[dofs[2]] *= alpha
        
        # Update the DG Lagrange function space with the limited DG Taylor values
        self.taylor.vector().set_local(taylor_vals)
        self.taylor.vector().apply('insert')
        taylor_to_lagrange(self.taylor, self.phi)
        
        self.alpha_funcs[0].vector().set_local(alphas)
        self.alpha_funcs[0].vector().apply('insert')

    def _run_dg2(self):
        """
        Perform slope limiting of a DG2 function
        """
        # Update the Taylor function space with the new DG values
        lagrange_to_taylor(self.phi, self.taylor)
        taylor_vals = self.taylor.vector().get_local()
        alphas1 = self.alpha_funcs[0].vector().get_local()
        alphas2 = self.alpha_funcs[1].vector().get_local()
        
        V = self.phi.function_space()
        mesh = V.mesh()
        tdim = mesh.topology().dim()
        num_cells_owned = mesh.topology().ghost_offset(tdim)
        
        # Slope limit one cell at a time
        for icell in xrange(num_cells_owned):
            dofs = self.cell_dofs_V[icell]
            assert len(dofs) == 6
            center_values = [taylor_vals[dof] for dof in dofs]
            (center_phi, center_phix, center_phiy, center_phixx, 
                center_phiyy, center_phixy) = center_values
            
            cell_vertices = [self.vertex_coordinates[iv] for iv in self.vertices[icell]]
            center_pos_x = (cell_vertices[0][0] + cell_vertices[1][0] + cell_vertices[2][0]) / 3
            center_pos_y = (cell_vertices[0][1] + cell_vertices[1][1] + cell_vertices[2][1]) / 3
            assert len(cell_vertices) == 3
            
            # Find the minimum slope limiter coefficient alpha of the φ, dφdx and dφ/dy terms
            alpha = [1.0] * 3
            for taylor_dof in (0, 1, 2): 
                for ivert in xrange(3):
                    dof = dofs[ivert]
                    dx = cell_vertices[ivert][0] - center_pos_x
                    dy = cell_vertices[ivert][1] - center_pos_y
                    
                    # Find vertex neighbours minimum and maximum values
                    base_value = center_values[taylor_dof]
                    minval = maxval = base_value
                    for nb in self.neighbours[dof]:
                        nb_center_val_dof = self.cell_dofs_V[nb][taylor_dof]
                        nb_val = taylor_vals[nb_center_val_dof]
                        minval = min(minval, nb_val)
                        maxval = max(maxval, nb_val)
                    
                    # Compute vertex value
                    if taylor_dof == 0:
                        # Function value at the vertex (linear reconstruction)
                        vertex_value = center_phi + center_phix * dx + center_phiy * dy
                    elif taylor_dof == 1:
                        # Derivative in x direction at the vertex  (linear reconstruction)
                        vertex_value = center_phix + center_phixx * dx + center_phixy * dy
                    else:
                        # Derivative in y direction at the vertex  (linear reconstruction)
                        vertex_value = center_phiy + center_phiyy * dy + center_phixy * dx
                    
                    # Compute alpha
                    if vertex_value > base_value:
                        a = (maxval - base_value) / (vertex_value - base_value)
                    elif vertex_value < base_value:
                        a = (minval - base_value) / (vertex_value - base_value)
                    alpha[taylor_dof] = min(alpha[taylor_dof], a)
            
            alpha2 = min(alpha[1], alpha[2])
            alpha1 = max(alpha[0], alpha2)
            
            taylor_vals[dofs[1]] *= alpha1
            taylor_vals[dofs[2]] *= alpha1
            taylor_vals[dofs[3]] *= alpha2
            taylor_vals[dofs[4]] *= alpha2
            taylor_vals[dofs[5]] *= alpha2
            
            dof_dg0 = self.cell_dofs_V0[icell] 
            alphas1[dof_dg0] = alpha1
            alphas2[dof_dg0] = alpha2
        
        # Update the DG Lagrange function space with the limited DG Taylor values
        self.taylor.vector().set_local(taylor_vals)
        self.taylor.vector().apply('insert')
        taylor_to_lagrange(self.taylor, self.phi)
        
        self.alpha_funcs[0].vector().set_local(alphas1)
        self.alpha_funcs[1].vector().set_local(alphas2)
        self.alpha_funcs[0].vector().apply('insert')
        self.alpha_funcs[1].vector().apply('insert')
