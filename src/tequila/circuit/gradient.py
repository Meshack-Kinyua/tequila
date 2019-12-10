from tequila.circuit import QCircuit
from tequila.circuit.compiler import compile_controlled_rotation
from tequila.circuit._gates_impl import ParametrizedGateImpl, RotationGateImpl
from tequila.circuit.compiler import compile_trotterized_gate
from tequila.objective import Objective, ExpectationValue
from tequila import TequilaException
from tequila.circuit.variable import Variable, Transform, has_variable

import numpy as np
import copy
import operator

import jax


def __weight_chain(par, variable):
    '''
    recursive function for getting and evaluating partial derivatives of transforn and variable objects with respect to the aforementioned.
    :param par: a transform or variable object, to be differentiated
    :param variable: the Variable with respect to which par should be differentiated.

    :ivar var: the string representation of variable
    :ivar expan: the list of terms in the expansion of the derivative of a transform
    '''
    if type(variable) is Variable:
        var = variable.name
    if type(variable) is str:
        var = variable

    if type(par) is Variable:
        if par.name == var:
            return 1.0
        else:
            return 0.0

    elif (type(par) is Transform or (hasattr(par, 'args') and hasattr(par, 'f'))):
        t = par
        la = len(t.args)
        expan = np.zeros(la)

        if t.has_var(var):
            for i in range(la):
                if has_variable(t.args[i], var):
                    floats = [complex(arg).real for arg in t.args]
                    expan[i] = tgrad(t.f, argnum=i)(*floats) * __weight_chain(t.args[i], var)
                else:
                    expan[i] = 0.0

        return np.sum(expan)

    else:
        s = 'Object of type {} passed to weight_chain; only strings, Variables and Transforms are allowed.'.format(
            str(type(par)))
        raise TequilaException(s)


def tgrad(f, argnum):
    '''
    function to be replaced entirely by the use of jax.grad(); completely identical thereto but restricted to our toy functions.
    :param f: a function (must be one of those defined in circuit.variable) to be differentiated.
    :param argnum (int): which of the arguments of the function with respect toward which it should be differentiated.
    :returns lambda function representing the partial of f with respect to the argument numberd by argnum
    '''
    assert callable(f)

    if argnum == 0:
        if f.op == operator.add:
            return lambda x, y: 1.0

        elif f.op == operator.mul:
            return lambda x, y: float(y)

        elif f.op == operator.sub:
            return lambda x, y: 1.0

        elif f.op == operator.truediv:
            return lambda x, y: 1 / float(y)

        elif f.op == operator.pow:
            return lambda x, y: float(y) * float(x) ** (float(y) - 1)

        else:
            raise TequilaException('Sorry, only pre-built tequila functions supported for tgrad at the moment.')


    elif argnum == 1:

        if f.op == operator.add:
            return lambda x, y: 1.0

        elif f.op == operator.mul:
            return lambda x, y: float(x)

        elif f.op == operator.sub:
            return lambda x, y: -1.0

        elif f.op == operator.truediv:
            return lambda x, y: -float(x) / (float(y) ** 2)

        elif f.op == operator.pow:
            return lambda x, y: (float(x) ** float(y)) * np.log(float(x))

        else:
            raise TequilaException('Sorry, only pre-built tequila functions supported for tgrad at the moment.')


def grad(obj, variable: str = None, no_compile=False):
    '''
    wrapper function for getting the gradients of Objectives or Unitaries (including single gates).
    :param obj (QCircuit,ParametrizedGateImpl,Objective): structure to be differentiated
    :param variables (list of Variable): parameters with respect to which obj should be differentiated.
        default None: total gradient.
    return: dictionary of Objectives
    '''
    if not no_compile:
        compiled = compile_trotterized_gate(gate=obj)
        compiled = compile_controlled_rotation(gate=compiled)
    else:
        compiled = obj

    if variable is None:
        variable = compiled.extract_variables()
    if not isinstance(variable, str):
        return [grad(obj=compiled, variable=v, no_compile=True) for v in variable]

    if isinstance(obj, ExpectationValue):
        return __grad_expectationvalue(E=compiled, variable=variable)
    elif isinstance(obj, Objective):
        return __grad_objective(objective=compiled, variable=variable)
    else:
        raise TequilaException("Gradient not implemented for other types than ExpectationValue and Objective.")


def __grad_objective(objective: Objective, variable: str = None):
    expectationvalues = objective.expectationvalues
    transformation = objective.transformation
    dO = None
    for i, E in enumerate(expectationvalues):
        if variable not in E.extract_variables():
            continue
        df = jax.jit(jax.grad(transformation, argnums=i))
        outer = Objective(expectationvalues=expectationvalues, transformation=df) # this seems to be a problem
        inner = grad(E, variable=variable)
        if dO is None:
            dO = outer * inner
        else:
            dO = dO + outer * inner
    return dO


def __grad_expectationvalue(E: ExpectationValue, variable: str):
    '''
    implements the analytic partial derivative of a unitary as it would appear in an expectation value. See the paper.
    :param unitary: the unitary whose gradient should be obtained
    :param variables (list, dict, str): the variables with respect to which differentiation should be performed.
    :return: vector (as dict) of dU/dpi as Objective (without hamiltonian)
    '''
    hamiltonian = E.H
    unitary = E.U

    pi = np.pi
    dO = None
    for i, g in enumerate(unitary.gates):
        if g.is_parametrized() and not g.is_frozen():
            if hasattr(g, 'angle'):
                neo_a = copy.deepcopy(g)
                neo_a.frozen = True

                neo_a.angle = g.angle + pi / 2
                U1 = unitary.replace_gate(position=i, gates=[neo_a])
                w1 = 0.5 * __weight_chain(g.parameter, variable)

                neo_b = copy.deepcopy(g)
                neo_b.frozen = True
                neo_b.angle = g.angle - pi / 2
                U2 = unitary.replace_gate(position=i, gates=[neo_b])
                w2 = -0.5 * __weight_chain(g.parameter, variable)

                Oplus = ExpectationValue(U=U1, H=hamiltonian)
                Ominus = ExpectationValue(U=U2, H=hamiltonian)
                dOinc  = w1 * Objective(expectationvalues=[Oplus]) + w2 * Objective(expectationvalues=[Ominus])
                if dO is None:
                    dO = dOinc
                else:
                    dO = dO + dOinc

            elif hasattr(g, 'power'):
                raise NotImplementedError("broken on this branch")
            else:
                print(type(g))
                raise TequilaException("Automatic differentiation is implemented only for Rotational Gates")
    return dO