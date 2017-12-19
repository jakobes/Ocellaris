import numpy
import dolfin
import contextlib
from .small_helpers import dolfin_log_level


def linear_solver_from_input(simulation, path,
                             default_solver='default',
                             default_preconditioner='default',
                             default_lu_method='default',
                             default_parameters=None):
    """
    From specifications in the input at the given path create a linear solver
    
    The path (e.g "solver/u") must point to a dictionary in the input file that
    can contain optional fields specifying the solver.
    
    Example::
    
        solver:
            u:
                solver: gmres
                preconditioner: additive_schwarz
            coupled:
                solver: lu
                lu_method: mumps
                parameters:
                    same_nonzero_pattern: True
    
    The default values are used if the keys are not found in the input
    """
    # Get values from input dictionary
    solver_method = simulation.input.get_value('%s/solver' % path, default_solver, 'string')
    preconditioner = simulation.input.get_value('%s/preconditioner' % path, default_preconditioner, 'string')
    lu_method = simulation.input.get_value('%s/lu_method' % path, default_lu_method, 'string')
    solver_parameters = simulation.input.get_value('%s/parameters' % path, {}, 'dict(string:any)')
    
    if default_parameters:
        params = [default_parameters, solver_parameters]
    else:
        params = [solver_parameters]
    
    simulation.log.info('    Creating linear equation solver from input "%s"' % path)
    simulation.log.info('        Method:         %s' % solver_method)
    simulation.log.info('        Preconditioner: %s' % preconditioner)
    simulation.log.info('        LU-method:      %s' % lu_method)
    
    return LinearSolverWrapper(solver_method, preconditioner, lu_method, params)


class LinearSolverWrapper(object):
    def __init__(self, solver_method, preconditioner=None, lu_method=None, parameters=None):
        """
        Wrap a Krylov or LU solver
        
        You must either specify solver_method = 'lu' and give the name
        of the solver, e.g lu_solver='mumps' or give a valid Krylov
        solver name, eg. solver_method='minres' and give the name of a
        preconditioner, eg. preconditioner_name='hypre_amg'.
        
        The parameters argument is a *list* of dictionaries which are
        to be used as parameters to the Krylov solver. Settings in the
        first dictionary in this list will be (potentially) overwritten
        by settings in later dictionaries. The use case is to provide
        sane defaults as well as allow the user to override the defaults
        in the input file
        
        The reason for this wrapper is to provide easy querying of
        iterative/direct and not crash when set_reuse_preconditioner is
        run before the first solve. This simplifies usage
        """
        self.solver_method = solver_method
        self.preconditioner = preconditioner
        self.lu_method = lu_method
        self.input_parameters = parameters
        
        self.is_first_solve = True
        self.is_iterative = False
        self.is_direct = False
    
        if solver_method.lower() == 'lu':
            solver = dolfin.PETScLUSolver(lu_method)
            self.is_direct = True
        else:
            precon = dolfin.PETScPreconditioner(preconditioner)
            solver = dolfin.PETScKrylovSolver(solver_method, precon)
            self._pre_obj = precon # Keep from going out of scope
            self.is_iterative = True
        
        for parameter_set in parameters:
            apply_settings(solver_method, solver.parameters, parameter_set)
        
        self._solver = solver
    
    def solve(self, *argv, **kwargs):
        ret = self._solver.solve(*argv, **kwargs)
        self.is_first_solve = False
        return ret
    
    @property
    def parameters(self):
        return self._solver.parameters
    
    def set_operator(self, A):
        return self._solver.set_operator(A)
    
    def set_reuse_preconditioner(self, *argv, **kwargs):
        if self.is_iterative and self.is_first_solve:
            return  # Nov 2016: this segfaults if running before the first solve
        else:
            return self._solver.set_reuse_preconditioner(*argv, **kwargs)
    
    def ksp(self):
        return self._solver.ksp()
    
    def __repr__(self):
        return ('<LinearSolverWrapper iterative=%r ' % self.is_iterative +
                                     'direct=%r ' % self.is_direct +
                                     'method=%r ' % self.solver_method +
                                     'preconditioner=%r ' % self.preconditioner +
                                     'LU-method=%r ' % self.lu_method +
                                     'parameters=%r>' % self.input_parameters)
    
    def ksp_inner_solve(self, inp, A, x, b, in_iter, co_iter):
        """
        This solver routine optionally uses the PETSc KSP interface
        to solve the given equation system. This gives more control
        over the number of iterations and the convergence criteria
        
        When used in IPCS, SIMPLE etc then in_iter is the inner
        iteration in the splitting scheme and co_iter is the number
        of iterations left in the time step
        
            in_iter + co_iter == num_inner_iter
        """
        use_ksp = inp.get_value('use_ksp', False, 'bool')
        
        if not use_ksp:
            with dolfin_log_level(dolfin.LogLevel.ERROR):
                return self.solve(A, x, b)
        
        firstN, lastN = inp.get('ksp_control', [3, 3])
        rtol_beg, rtol_mid, rtol_end = inp.get_value('ksp_rtol', [1e-6, 1e-8, 1e-10], 'list(float)')
        atol_beg, atol_mid, atol_end = inp.get_value('ksp_atol', [1e-8, 1e-10, 1e-15], 'list(float)')
        nitk_beg, nitk_mid, nitk_end = inp.get_value('ksp_max_it', [10, 40, 100], 'list(int)')
        
        # Solver setup with petsc4py
        ksp = self._solver.ksp()
        pc = ksp.getPC()
        
        # Special treatment of first inner iteration
        reuse_pc = True
        if in_iter == 1:
            reuse_pc = False
            ksp.setOperators(A.mat())
        
        if co_iter < lastN:
            # This is one of the last iterations
            rtol = rtol_end
            atol = atol_end
            max_it = nitk_end
        elif in_iter <= firstN:
            # This is one of the first iterations
            rtol = rtol_beg
            atol = atol_beg
            max_it = nitk_beg
        else:
            # This iteration is in the middle of the range
            rtol = rtol_mid
            atol = atol_mid
            max_it = nitk_mid
        
        pc.setReusePreconditioner(reuse_pc)
        ksp.setTolerances(rtol=rtol, atol=atol, max_it=max_it)
        ksp.solve(b.vec(), x.vec())
        x.update_ghost_values()
        return ksp.getIterationNumber()


def apply_settings(solver_method, parameters, new_values):
    """
    This function does almost the same as::
    
        parameters.update(new_values)
    
    The difference is that subdictionaries are handled
    recursively and not replaced outright
    """
    skip = set()
    if solver_method == 'lu':
        skip.update(['nonzero_initial_guess',
                     'relative_tolerance',
                     'absolute_tolerance'])
    
    for key, value in new_values.items():
        if key in skip:
            continue
        elif isinstance(value, dict):
            apply_settings(solver_method, parameters[key], value)
        else:
            parameters[key] = value


@contextlib.contextmanager
def petsc_options(opts):
    """
    A context manager to set PETSc options for a limited amount of code.
    The parameter opts is a dictionary of PETSc/SLEPc options
    """
    from petsc4py import PETSc
    orig_opts = PETSc.Options().getAll()
    for key, val in opts.items():
        PETSc.Options().setValue(key, val)
    
    yield # run the code
    
    for key in opts.keys():
        if key in orig_opts:
            PETSc.Options().setValue(key, orig_opts[key])
        else:
            PETSc.Options().delValue(key)


def create_block_matrix(V, blocks):
    """
    Create a sparse matrix to hold dense blocks that are larger than
    the normal DG block diagonal mass matrices (super-cell dense blocks)
    
    The argument ``blocks`` should be a list of lists/arrays containing
    the dofs in each block. The dofs are assumed to be the same for
    both rows and columns. If blocks == 'diag' then a diagonal matrix is
    returned
    """
    comm = V.mesh().mpi_comm()
    dm = V.dofmap()
    im = dm.index_map()
    
    # Create a tensor layout for the matrix
    ROW_MAJOR = 0
    tl = dolfin.TensorLayout(comm, ROW_MAJOR, dolfin.TensorLayout.Sparsity.SPARSE)
    tl.init([im, im], dolfin.TensorLayout.Ghosts.GHOSTED)
    
    # Setup the tensor layout's sparsity pattern
    sp = tl.sparsity_pattern()
    sp.init([im, im])
    if blocks == 'diag':
        Ndofs = im.size(im.MapSize.OWNED)
        entries = numpy.empty((2, 1), dtype=numpy.intc)
        for dof in range(Ndofs):
            entries[:] = dof
            sp.insert_local(entries)
    else:
        entries = None
        for block in blocks:
            N = len(block)
            if entries is None or entries.shape[1] != N:
                entries = numpy.empty((2, N), dtype=numpy.intc)
                entries[0,:] = block
                entries[1,:] = entries[0,:]
                sp.insert_local(entries)
    sp.apply()
    
    # Create a matrix with the newly created tensor layout
    A = dolfin.PETScMatrix(comm)
    A.init(tl)
    
    return A


def matmul(A, B, out=None):
    """
    A B (and potentially out) must be PETScMatrix
    The matrix out must be the result of a prior matmul
    call with the same sparsity patterns in A and B
    """
    assert A is not None and B is not None
    
    A = A.mat()
    B = B.mat()
    if out is not None:
        A.matMult(B, out.mat())
        C = out
    else:
        Cmat = A.matMult(B)
        C = dolfin.PETScMatrix(Cmat)
        C.apply('insert')
    
    return C


def condition_number(A, method='simplified'):
    """
    Estimate the condition number of the matrix A
    """
    if method == 'simplified':
        # Calculate max(abs(A))/min(abs(A))
        amin, amax = 1e10, -1e10
        for irow in range(A.size(0)):
            _indices, values = A.getrow(irow)
            aa = abs(values)
            amax = max(amax, aa.max())
            aa[aa==0] = amax
            amin = min(amin, aa.min())
        amin = dolfin.MPI.min(dolfin.mpi_comm_world(), float(amin))
        amax = dolfin.MPI.max(dolfin.mpi_comm_world(), float(amax))
        return amax/amin
    
    elif method == 'numpy':
        from numpy.linalg import cond
        A = mat_to_scipy_csr(A).todense()
        return cond(A)
    
    elif method == 'SLEPc':
        from petsc4py import PETSc
        from slepc4py import SLEPc
        
        # Get the petc4py matrix
        PA = dolfin.as_backend_type(A).mat()
        
        # Calculate the largest and smallest singular value
        opts = {
            'svd_type': 'cross',
            'svd_eps_type': 'gd',
            #'help': 'svd_type'
        }
        with petsc_options(opts):
            S = SLEPc.SVD()
            S.create()
            S.setOperator(PA)
            S.setFromOptions()
            S.setDimensions(1, PETSc.DEFAULT, PETSc.DEFAULT)
            S.setWhichSingularTriplets(SLEPc.SVD.Which.LARGEST)
            S.solve()
            if S.getConverged() == 1:
                sigma_1 = S.getSingularTriplet(0)
            else:
                raise ValueError('Could not find the highest singular value (%d)'
                                 % S.getConvergedReason())
            print('Highest singular value:', sigma_1)
            
            S.setWhichSingularTriplets(SLEPc.SVD.Which.SMALLEST)
            S.solve()
            if S.getConverged() == 1:
                sigma_n = S.getSingularTriplet(0)
            else:
                raise ValueError('Could not find the lowest singular value (%d)'
                                 % S.getConvergedReason())
            print('Lowest singular value:', sigma_n)
            print(PETSc.Options().getAll())
        print(PETSc.Options().getAll())
        
        return sigma_1/sigma_n


def mat_to_scipy_csr(dolfin_matrix):
    """
    Convert any dolfin.Matrix to csr matrix in scipy.
    Based on code by Miroslav Kuchta
    """
    assert dolfin.MPI.size(dolfin.mpi_comm_world()) == 1, 'mat_to_csr assumes single process'
    import scipy.sparse
    
    rows = [0]
    cols = []
    values = []
    for irow in range(dolfin_matrix.size(0)):
        indices, values_ = dolfin_matrix.getrow(irow)
        rows.append(len(indices)+rows[-1])
        cols.extend(indices)
        values.extend(values_)

    shape = dolfin_matrix.size(0), dolfin_matrix.size(1)
        
    return scipy.sparse.csr_matrix((numpy.array(values, dtype='float'),
                                    numpy.array(cols, dtype='int'),
                                    numpy.array(rows, dtype='int')),
                                    shape)
