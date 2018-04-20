import dolfin
from ocellaris import Simulation
from ocellaris.solver_parts.fields.vector_field import VectorField
from ocellaris.solver_parts.fields.scalar_field import ScalarField

INP = """
ocellaris:
    type: input
    version: 1.0

user_code:
    constants:
        A: 1

fields:
-   name: velocity
    type: VectorField
    variable_name: u
    cpp_code: ['t+A', 't*A', 't*A + A']

-   name: density
    type: ScalarField
    variable_name: rho
    cpp_code: |
        [&]() {
            return t * A + A;
        }()

"""


def test_scalar_field():
    sim = Simulation()
    sim.input.read_yaml(yaml_string=INP)
    mesh = dolfin.UnitCubeMesh(2, 2, 2)
    sim.set_mesh(mesh)
    
    field_inp = sim.input.get_value('fields/1', required_type='Input')
    field = ScalarField(sim, field_inp)
    
    # t = 0
    t, A = 0, 1
    f = field.get_variable('rho')
    check_vector_value_histogram(f.vector(), {t * A + A: 125})
    
    # t = 1
    t, A = 1, 1
    sim.time = t
    field.update(1, t, 1.0)
    check_vector_value_histogram(f.vector(), {t * A + A: 125})
    
    # t = 2
    t, A = 2, 10
    sim.time = t
    sim.input.set_value('user_code/constants/A', A)
    field.update(2, t, 1.0)
    check_vector_value_histogram(f.vector(), {t * A + A: 125})


def test_vector_field():
    sim = Simulation()
    sim.input.read_yaml(yaml_string=INP)
    mesh = dolfin.UnitCubeMesh(2, 2, 2)
    sim.set_mesh(mesh)
    
    field_inp = sim.input.get_value('fields/0', required_type='Input')
    field = VectorField(sim, field_inp)
    
    def verify(f, t, A):
        print(t, A)
        check_vector_value_histogram(f[0].vector(), {t + A: 125})
        check_vector_value_histogram(f[1].vector(), {t * A: 125})
        check_vector_value_histogram(f[2].vector(), {t * A + A: 125})
    
    # t = 0
    t, A = 0, 1
    f = field.get_variable('u')
    verify(f, t, A)
    
    # t = 1
    t, A = 1, 1
    sim.time = t
    sim.input.set_value('user_code/constants/A', A)
    field.update(1, t, 1.0)
    verify(f, t, A)
    
    # t = 2
    t, A = 2, 10
    sim.time = t
    sim.input.set_value('user_code/constants/A', A)
    field.update(2, t, 1.0)
    verify(f, t, A)


def check_vector_value_histogram(vec, expected):
    hist = get_vector_value_histogram(vec)
    print(hist, expected)
    assert len(hist) == len(expected)
    for k, v in expected.items():
        assert k in hist
        assert hist[k] == v


def get_vector_value_histogram(vec):
    hist = {}
    for v in vec.get_local():
        if v in hist:
            hist[v] += 1
        else:
            hist[v] = 1
    
    comm = dolfin.MPI.comm_world
    all_hist = comm.gather(hist)
    hist2 = None
    if comm.rank == 0:
        hist2 = {}
        for hist in all_hist:
            for k, v in hist.items():
                if k in hist2:
                    hist2[k] += v
                else:
                    hist2[k] = v
    return comm.bcast(hist2)
