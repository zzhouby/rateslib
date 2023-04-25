import pytest
from datetime import datetime as dt
from pandas import DataFrame, MultiIndex
from pandas.testing import assert_frame_equal
import numpy as np
from numpy.testing import assert_allclose
from math import log, exp

import context
from rateslib import defaults, default_context
from rateslib.curves import Curve, index_left, LineCurve
from rateslib.solver import Solver
from rateslib.dual import Dual
from rateslib.instruments import IRS, Value, FloatRateBond, Portfolio, XCS
from rateslib.fx import FXRates, FXForwards


@pytest.mark.parametrize("algo", [
    "gauss_newton",
    "levenberg_marquardt",
    "gradient_descent"
])
def test_basic_solver(algo):
    curve = Curve({
        dt(2022, 1, 1): 1.0,
        dt(2023, 1, 1): 1.0,
        dt(2024, 1, 1): 1.0,
        dt(2025, 1, 1): 1.0,
    }, id="v")
    instruments = [
        (IRS(dt(2022, 1, 1), "1Y", "Q"), (curve,), {}),
        (IRS(dt(2022, 1, 1), "2Y", "Q"), (curve,), {}),
        (IRS(dt(2022, 1, 1), "3Y", "Q"), (curve,), {}),
    ]
    s = np.array([1.0, 1.6, 2.0])
    solver = Solver(
        curves=[curve],
        instruments=instruments,
        s=s,
        algorithm=algo,
    )
    assert float(solver.g) < 1e-9
    assert curve.nodes[dt(2022, 1, 1)] == Dual(1.0, "v0", [1])
    expected = [1, 0.9899250357528555, 0.9680433953206192, 0.9407188354823821]
    for i, key in enumerate(curve.nodes.keys()):
        assert abs(float(curve.nodes[key]) - expected[i]) < 1e-6


@pytest.mark.parametrize("algo", [
    "gauss_newton",
    "levenberg_marquardt",
    "gradient_descent"
])
def test_solver_reiterate(algo):
    # test that curves are properly updated by a reiterate
    curve = Curve({
        dt(2022, 1, 1): 1.0,
        dt(2023, 1, 1): 1.0,
        dt(2024, 1, 1): 1.0,
        dt(2025, 1, 1): 1.0,
    }, id="v")
    instruments = [
        IRS(dt(2022, 1, 1), "1Y", "Q", curves="v"),
        IRS(dt(2022, 1, 1), "2Y", "Q", curves="v"),
        IRS(dt(2022, 1, 1), "3Y", "Q", curves="v"),
    ]
    s = np.array([1.0, 1.5, 2.0])
    solver = Solver(
        curves=[curve],
        instruments=instruments,
        s=s,
        algorithm=algo,
    )
    assert float(solver.g) < 1e-9

    solver.s[1] = 1.6
    solver.iterate()

    # now check that a reiteration has resolved the curve
    assert curve.nodes[dt(2022, 1, 1)] == Dual(1.0, "v0", [1])
    expected = [1, 0.9899250357528555, 0.9680433953206192, 0.9407188354823821]
    for i, key in enumerate(curve.nodes.keys()):
        assert abs(float(curve.nodes[key]) - expected[i]) < 1e-6


@pytest.mark.parametrize("algo", [
    "gauss_newton",
    "levenberg_marquardt",
    "gradient_descent"
])
def test_basic_solver_line_curve(algo):
    curve = LineCurve({
        dt(2022, 1, 1): 1.0,
        dt(2023, 1, 1): 1.0,
        dt(2024, 1, 1): 1.0,
    }, id="v")
    instruments = [
        (Value(dt(2022, 1, 1)), (curve,), {}),
        (Value(dt(2023, 1, 1)), (curve,), {}),
        (Value(dt(2024, 1, 1)), (curve,), {}),
    ]
    s = np.array([3.0, 3.6, 4.0])
    solver = Solver(
        curves=[curve],
        instruments=instruments,
        s=s,
        algorithm=algo,
    )
    assert float(solver.g) < 1e-9
    for i, key in enumerate(curve.nodes.keys()):
        assert abs(float(curve.nodes[key]) - s[i]) < 1e-6


def test_basic_spline_solver():
    spline_curve = Curve(
        nodes={
            dt(2022, 1, 1): 1.0,
            dt(2023, 1, 1): 0.99,
            dt(2024, 1, 1): 0.965,
            dt(2025, 1, 1): 0.93,
        },
        interpolation="log_linear",
        t=[dt(2023, 1, 1), dt(2023, 1, 1), dt(2023, 1, 1), dt(2023, 1, 1),
           dt(2024, 1, 1), dt(2025, 1, 1), dt(2025, 1, 1), dt(2025, 1, 1),
           dt(2025, 1, 1)],
        id="v",
    )
    instruments = [
        (IRS(dt(2022, 1, 1), "1Y", "Q"), (spline_curve,), {}),
        (IRS(dt(2022, 1, 1), "2Y", "Q"), (spline_curve,), {}),
        (IRS(dt(2022, 1, 1), "3Y", "Q"), (spline_curve,), {}),
    ]
    s = np.array([1.0, 1.6, 2.0])
    solver = Solver(
        curves=[spline_curve],
        instruments=instruments,
        s=s,
    )
    assert float(solver.g) < 1e-12
    assert spline_curve.nodes[dt(2022, 1, 1)] == Dual(1.0, "v0", [1])
    expected = [1, 0.98992503575307, 0.9680378584288896, 0.9408478640732281]
    for i, key in enumerate(spline_curve.nodes.keys()):
        assert abs(float(spline_curve.nodes[key]) - expected[i]) < 1e-11


def test_solver_raises_len():
    with pytest.raises(ValueError, match="`instrument_rates` must be same length"):
        Solver(
            instruments=[1],
            s=[1, 2],
        )

    with pytest.raises(ValueError, match="`instrument_labels` must have length"):
        Solver(
            instruments=[1],
            s=[1],
            instrument_labels=[1, 2],
        )

    with pytest.raises(ValueError, match="`weights` must be same length"):
        Solver(
            instruments=[1, 2],
            s=[1, 2],
            instrument_labels=[1, 2],
            weights=[1],
        )


def test_basic_solver_weights():
    # This test replicates test_basic_solver with the 3Y rate at two different rates.
    # We vary the weights argument to selectively decide which one to use.
    curve = Curve({
        dt(2022, 1, 1): 1.0,
        dt(2023, 1, 1): 1.0,
        dt(2024, 1, 1): 1.0,
        dt(2025, 1, 1): 1.0,
    }, id="v")
    instruments = [
        (IRS(dt(2022, 1, 1), "1Y", "Q"), (curve,), {}),
        (IRS(dt(2022, 1, 1), "2Y", "Q"), (curve,), {}),
        (IRS(dt(2022, 1, 1), "3Y", "Q"), (curve,), {}),
        (IRS(dt(2022, 1, 1), "3Y", "Q"), (curve,), {}),
    ]
    s = np.array([1.0, 1.6, 2.02, 1.98])  # average 3Y at approximately 2.0%
    solver = Solver(
        curves=[curve],
        instruments=instruments,
        s=s,
        func_tol=0.00085,
    )
    assert float(solver.g) < 0.00085
    assert curve.nodes[dt(2022, 1, 1)] == Dual(1.0, "v0", [1])
    expected = [1, 0.9899250357528555, 0.9680433953206192, 0.9407188354823821]
    for i, key in enumerate(curve.nodes.keys()):
        assert abs(float(curve.nodes[key]) - expected[i]) < 1e-6

    solver = Solver(
        curves=[curve],
        instruments=instruments,
        s=s,
        weights=[1, 1, 1, 1e-6],
        func_tol=1e-7,
    )
    assert abs(float(instruments[2][0].rate(curve)) - 2.02) < 1e-4

    solver = Solver(
        curves=[curve],
        instruments=instruments,
        s=s,
        weights=[1, 1, 1e-6, 1],
        func_tol=1e-7,
    )
    assert abs(float(instruments[2][0].rate(curve)) - 1.98) < 1e-4


def test_solver_independent_curve():
    # Test that a solver can use an independent curve as a static object and solve
    # without mutating that un-referenced object.
    independent_curve = Curve({
        dt(2022, 1, 1): 1.0,
        dt(2023, 1, 1): 0.98,
        dt(2024, 1, 1): 0.96,
        dt(2025, 1, 1): 0.94,
    })
    expected = independent_curve.copy()
    var_curve = Curve({
        dt(2022, 1, 1): 1.0,
        dt(2023, 1, 1): 0.99,
        dt(2024, 1, 1): 0.98,
        dt(2025, 1, 1): 0.97,
    })
    instruments = [
        (IRS(dt(2022, 1, 1), "1Y", "Q"), ([var_curve, independent_curve],), {}),
        (IRS(dt(2022, 1, 1), "2Y", "Q"), ([var_curve, independent_curve],), {}),
        (IRS(dt(2022, 1, 1), "3Y", "Q"), ([var_curve, independent_curve],), {}),
    ]
    s = np.array([2.00, 2.00, 2.00])
    with default_context("curve_not_in_solver", "ignore"):
        solver = Solver(
            curves=[var_curve],
            instruments=instruments,
            s=s,
            func_tol=1e-13,
            conv_tol=1e-13,
        )
    for i, instrument in enumerate(instruments):
        assert abs(
            float(instrument[0].rate(*instrument[1], **instrument[2]) - s[i])
        ) < 1e-7
    assert independent_curve == expected


def test_non_unique_curves():
    curve = Curve({dt(2022, 1, 1): 1.0, dt(2023, 1, 1): 0.98}, id="A")
    curve2 = Curve({dt(2022, 1, 1): 1.0, dt(2023, 1, 1): 0.98}, id="A")
    solver = Solver(
        curves=[curve],
        instruments=[(IRS(dt(2022, 1, 1), "1Y", "Q"), (curve,), {})],
        s=[1]
    )

    with pytest.raises(ValueError, match="`curves` must each have their own unique"):
        solver2 = Solver(
            curves=[curve2],
            instruments=[(IRS(dt(2022, 1, 1), "1Y", "Q"), (curve,), {})],
            s=[2],
            pre_solvers=[solver]
        )

    with pytest.raises(ValueError, match="`curves` must each have their own unique"):
        solver2 = Solver(
            curves=[curve, curve2],
            instruments=[(IRS(dt(2022, 1, 1), "1Y", "Q"), (curve,), {})],
            s=[2],
        )


def test_max_iterations():
    # This test replicates has an oscillatory solution between the different 3y rates.
    curve = Curve({
        dt(2022, 1, 1): 1.0,
        dt(2023, 1, 1): 1.0,
        dt(2024, 1, 1): 1.0,
        dt(2025, 1, 1): 1.0,
    }, id="v")
    instruments = [
        (IRS(dt(2022, 1, 1), "1Y", "Q"), (curve,), {}),
        (IRS(dt(2022, 1, 1), "2Y", "Q"), (curve,), {}),
        (IRS(dt(2022, 1, 1), "3Y", "Q"), (curve,), {}),
        (IRS(dt(2022, 1, 1), "3Y", "Q"), (curve,), {}),
    ]
    s = np.array([1.0, 1.6, 2.02, 1.98])  # average 3Y at approximately 2.0%
    solver = Solver(
        curves=[curve],
        instruments=instruments,
        s=s,
        func_tol=1e-10,
        max_iter=30,
    )
    assert len(solver.g_list) == 30


def test_solver_pre_solver_dependency_generates_same_delta():
    """
    Build an ESTR curve with solver1.
    Build an IBOR curve with solver2 dependent upon solver1.

    Build an ESTR and IBOR curve simultaneously inside the same solver3.

    :return:
    """
    eur_disc_curve = Curve(
        nodes={
            dt(2022, 1, 1): 1.0,
            dt(2023, 1, 1): 1.0,
            dt(2024, 1, 1): 1.0
        },
        id="eur"
    )
    eur_instruments = [
        (IRS(dt(2022, 1, 1), "8M", "A"), (eur_disc_curve,), {}),
        (IRS(dt(2022, 1, 1), "16M", "A"), (eur_disc_curve,), {}),
        (IRS(dt(2022, 1, 1), "2Y", "A"), (eur_disc_curve,), {}),
    ]
    eur_disc_s = [2.01, 2.22, 2.55]
    eur_disc_solver = Solver(
        [eur_disc_curve],
        eur_instruments,
        eur_disc_s,
        id="estr"
    )

    eur_ibor_curve = Curve(
        nodes={
            dt(2022, 1, 1): 1.0,
            dt(2023, 1, 1): 1.0,
            dt(2024, 1, 1): 1.0
        },
        id="eur_ibor"
    )
    eur_ibor_instruments = [
        (IRS(dt(2022, 1, 1), "1Y", "A"), ([eur_ibor_curve, eur_disc_curve],), {}),
        (IRS(dt(2022, 1, 1), "2Y", "A"), ([eur_ibor_curve, eur_disc_curve],), {}),
    ]
    eur_ibor_s = [2.25, 2.65]
    eur_solver2 = Solver(
        [eur_ibor_curve],
        eur_ibor_instruments,
        eur_ibor_s,
        pre_solvers=[eur_disc_solver],
        id="ibor"
    )

    eur_disc_curve2 = Curve(
        {dt(2022, 1, 1): 1.0, dt(2023, 1, 1): 1.0, dt(2024, 1, 1): 1.0}, id="eur")
    eur_ibor_curve2 = Curve(
        {dt(2022, 1, 1): 1.0, dt(2023, 1, 1): 1.0, dt(2024, 1, 1): 1.0}, id="eur_ibor")
    eur_instruments2 = [
        (IRS(dt(2022, 1, 1), "8M", "A"), (eur_disc_curve2,), {}),
        (IRS(dt(2022, 1, 1), "16M", "A"), (eur_disc_curve2,), {}),
        (IRS(dt(2022, 1, 1), "2Y", "A"), (eur_disc_curve2,), {}),
        (IRS(dt(2022, 1, 1), "1Y", "A"), ([eur_ibor_curve2, eur_disc_curve2],), {}),
        (IRS(dt(2022, 1, 1), "2Y", "A"), ([eur_ibor_curve2, eur_disc_curve2],), {}),
    ]
    eur_disc_s2 = [2.01, 2.22, 2.55, 2.25, 2.65]
    eur_solver_sim = Solver(
        [eur_disc_curve2, eur_ibor_curve2],
        eur_instruments2,
        eur_disc_s2,
        id="eur_sol_sim",
        instrument_labels=["estr0", "estr1", "estr2", "ibor0", "ibor1"]
    )

    eur_swap = IRS(dt(2022, 3, 1), "16M", "M", fixed_rate=3.0, )

    delta_sim = eur_swap.delta([eur_ibor_curve2, eur_disc_curve2], eur_solver_sim)
    delta_pre = eur_swap.delta([eur_ibor_curve, eur_disc_curve], eur_solver2)
    delta_pre.index = delta_sim.index
    assert_frame_equal(delta_sim, delta_pre)


def test_delta_gamma_calculation():
    estr_curve = Curve({dt(2022, 1, 1): 1.0, dt(2032, 1, 1): 1.0, dt(2042, 1, 1): 1.0},
                       id="estr_curve")
    estr_instruments = [
        (IRS(dt(2022, 1, 1), "10Y", "A"), (estr_curve,), {}),
        (IRS(dt(2022, 1, 1), "20Y", "A"), (estr_curve,), {}),
    ]
    estr_solver = Solver(
        [estr_curve],
        estr_instruments,
        [2.0, 1.5],
        id="estr",
        instrument_labels=["10Y", "20Y"]
    )

    # Mechanism 1: dynamic
    eur_swap = IRS(dt(2032, 1, 1), "10Y", "A", notional=100e6)
    assert 74430 < float(eur_swap.delta(estr_curve, estr_solver).sum()) < 74432
    assert -229 < float(eur_swap.gamma(estr_curve, estr_solver).sum().sum()) < -228

    # Mechanism 1: dynamic names
    assert 74430 < float(eur_swap.delta("estr_curve", estr_solver).sum()) < 74432
    assert -229 < float(eur_swap.gamma("estr_curve", estr_solver).sum().sum()) < -228

    # Mechanism 1: fails on None curve specification
    with pytest.raises(TypeError, match="`curves` have not been supplied correctly"):
        assert eur_swap.delta(None, estr_solver)
    with pytest.raises(TypeError, match="`curves` have not been supplied correctly"):
        assert eur_swap.gamma(None, estr_solver)

    # Mechanism 2: static specific
    eur_swap = IRS(dt(2032, 1, 1), "10Y", "A", notional=100e6, curves=estr_curve)
    assert 74430 < float(eur_swap.delta(None, estr_solver).sum()) < 74432
    assert -229 < float(eur_swap.gamma(None, estr_solver).sum().sum()) < -228

    # Mechanism 2: static named
    eur_swap = IRS(dt(2032, 1, 1), "10Y", "A", notional=100e6, curves="estr_curve")
    assert 74430 < float(eur_swap.delta(None, estr_solver).sum()) < 74432
    assert -229 < float(eur_swap.gamma(None, estr_solver).sum().sum()) < -228


def test_solver_pre_solver_dependency_generates_same_gamma():
    estr_curve = Curve({dt(2022, 1, 1): 1.0, dt(2032, 1, 1): 1.0, dt(2042, 1, 1): 1.0})
    estr_instruments = [
        (IRS(dt(2022, 1, 1), "7Y", "A"), (estr_curve,), {}),
        (IRS(dt(2022, 1, 1), "15Y", "A"), (estr_curve,), {}),
        (IRS(dt(2022, 1, 1), "20Y", "A"), (estr_curve,), {}),
    ]
    estr_s = [2.0, 1.75, 1.5]
    estr_labels = ["7ye", "15ye", "20ye"]
    estr_solver = Solver(
        [estr_curve],
        estr_instruments,
        estr_s,
        id="estr",
        instrument_labels=estr_labels,
    )

    ibor_curve = Curve({dt(2022, 1, 1): 1.0, dt(2032, 1, 1): 1.0, dt(2042, 1, 1): 1.0})
    ibor_instruments = [
        (IRS(dt(2022, 1, 1), "10Y", "A"), ([ibor_curve, estr_curve],), {}),
        (IRS(dt(2022, 1, 1), "20Y", "A"), ([ibor_curve, estr_curve],), {}),
    ]
    ibor_s = [2.1, 1.65]
    ibor_labels = ["10Yi", "20Yi"]
    ibor_solver = Solver(
        [ibor_curve],
        ibor_instruments,
        ibor_s,
        id="ibor",
        instrument_labels=ibor_labels,
        pre_solvers=[estr_solver],
    )

    eur_swap = IRS(dt(2032, 1, 1), "10Y", "A", notional=100e6)
    gamma_pre = eur_swap.gamma([ibor_curve, estr_curve], ibor_solver)
    delta_pre = eur_swap.delta([ibor_curve, estr_curve], ibor_solver)

    estr_curve2 = Curve({dt(2022, 1, 1): 1.0, dt(2032, 1, 1): 1.0, dt(2042, 1, 1): 1.0})
    ibor_curve2 = Curve({dt(2022, 1, 1): 1.0, dt(2032, 1, 1): 1.0, dt(2042, 1, 1): 1.0})
    sim_instruments = [
        (IRS(dt(2022, 1, 1), "7Y", "A"), (estr_curve2,), {}),
        (IRS(dt(2022, 1, 1), "15Y", "A"), (estr_curve2,), {}),
        (IRS(dt(2022, 1, 1), "20Y", "A"), (estr_curve2,), {}),
        (IRS(dt(2022, 1, 1), "10Y", "A"), ([ibor_curve2, estr_curve2],), {}),
        (IRS(dt(2022, 1, 1), "20Y", "A"), ([ibor_curve2, estr_curve2],), {}),
    ]
    simultaneous_solver = Solver(
        [estr_curve2, ibor_curve2],
        sim_instruments,
        estr_s + ibor_s,
        id="simul",
        instrument_labels=estr_labels + ibor_labels,
    )
    gamma_sim = eur_swap.gamma([ibor_curve2, estr_curve2], simultaneous_solver)
    delta_sim = eur_swap.delta([ibor_curve2, estr_curve2], simultaneous_solver)

    # check arrays in construction of gamma
    grad_s_vT_sim = simultaneous_solver.grad_s_vT_pre
    grad_s_vT_pre = ibor_solver.grad_s_vT_pre
    assert_allclose(grad_s_vT_pre, grad_s_vT_sim, atol=1e-14, rtol=1e-10)

    simultaneous_solver._set_ad_order(2)
    J2_sim = simultaneous_solver.J2_pre
    ibor_solver._set_ad_order(2)
    J2_pre = ibor_solver.J2_pre
    assert_allclose(J2_pre, J2_sim, atol=1e-14, rtol=1e-10)

    grad_s_s_vT_sim = simultaneous_solver.grad_s_s_vT_pre
    grad_s_s_vT_pre = ibor_solver.grad_s_s_vT_pre
    assert_allclose(grad_s_s_vT_pre, grad_s_s_vT_sim, atol=1e-14, rtol=1e-10)

    gamma_pre.index = gamma_sim.index
    gamma_pre.columns = gamma_sim.columns
    delta_pre.index = delta_sim.index
    assert_frame_equal(delta_sim, delta_pre)
    assert_frame_equal(gamma_sim, gamma_pre)


def test_nonmutable_presolver_defaults():
    estr_curve = Curve({dt(2022, 1, 1): 1.0, dt(2032, 1, 1): 1.0})
    estr_instruments = [
        (IRS(dt(2022, 1, 1), "10Y", "A"), (estr_curve,), {}),
    ]
    estr_s = [2.0]
    estr_labels = ["10ye"]
    estr_solver = Solver(
        [estr_curve],
        estr_instruments,
        estr_s,
        id="estr",
        instrument_labels=estr_labels,
    )
    with pytest.raises(AttributeError, match="'tuple' object has no attribute"):
        estr_solver.pre_solvers.extend([1, 2, 3])


def test_solver_grad_s_vT_methods_equivalent():
    curve = Curve(nodes={
        dt(2022, 1, 1): 1.0,
        dt(2023, 1, 1): 1.0,
        dt(2024, 1, 1): 1.0,
        dt(2025, 1, 1): 1.0,
        dt(2026, 1, 1): 1.0,
        dt(2027, 1, 1): 1.0,
    })
    instruments = [
        (IRS(dt(2022, 1, 1), "2Y", "A"), (curve,), {}),
        (IRS(dt(2023, 1, 1), "1Y", "A"), (curve,), {}),
        (IRS(dt(2023, 1, 1), "2Y", "A"), (curve,), {}),
        (IRS(dt(2022, 5, 1), "4Y", "A"), (curve,), {}),
        (IRS(dt(2023, 1, 1), "4Y", "A"), (curve,), {}),
    ]
    s = [1.2, 1.4, 1.6, 1.7, 1.9]
    solver = Solver([curve], instruments, s)

    solver._grad_s_vT_method = "_grad_s_vT_final_iteration_analytical"
    grad_s_vT_final_iter_anal = solver.grad_s_vT

    solver._grad_s_vT_method = "_grad_s_vT_final_iteration_dual"
    solver._grad_s_vT_final_iteration_algo = "gauss_newton_final"
    solver._reset_properties_()
    grad_s_vT_final_iter_dual = solver.grad_s_vT

    solver._grad_s_vT_method = "_grad_s_vT_fixed_point_iteration"
    solver._reset_properties_()
    grad_s_vT_fixed_point_iter = solver.grad_s_vT

    assert_allclose(grad_s_vT_final_iter_dual, grad_s_vT_final_iter_anal, atol=1e-12)
    assert_allclose(grad_s_vT_fixed_point_iter, grad_s_vT_final_iter_anal, atol=1e-12)
    assert_allclose(grad_s_vT_final_iter_dual, grad_s_vT_fixed_point_iter, atol=1e-12)


def test_solver_second_order_vars_raise_on_first_order():
    curve = Curve({dt(2022, 1, 1): 1.0, dt(2023, 1, 1): 0.98}, id="A")
    solver = Solver(
        curves=[curve],
        instruments=[(IRS(dt(2022, 1, 1), "1Y", "Q"), (curve,), {})],
        s=[1]
    )

    with pytest.raises(ValueError, match="Cannot perform second derivative calc"):
        solver.J2

    with pytest.raises(ValueError, match="Cannot perform second derivative calc"):
        solver.grad_s_s_vT


def test_solver_second_order_vars_raise_on_first_order_pre_solvers():
    curve = Curve({dt(2022, 1, 1): 1.0, dt(2023, 1, 1): 0.98}, id="A")
    solver = Solver(
        curves=[curve],
        instruments=[(IRS(dt(2022, 1, 1), "1Y", "Q"), (curve,), {})],
        s=[1]
    )
    curve2 = Curve({dt(2022, 1, 1): 1.0, dt(2023, 1, 1): 0.98}, id="B")
    solver2 = Solver(
        curves=[curve2],
        instruments=[(IRS(dt(2022, 1, 1), "1Y", "Q"), (curve2,), {})],
        s=[1],
        pre_solvers=[solver]
    )

    with pytest.raises(ValueError, match="Cannot perform second derivative calc"):
        solver2.J2_pre

    with pytest.raises(ValueError, match="Cannot perform second derivative calc"):
        solver.grad_s_s_vT_pre


def test_bad_algo_raises():
    curve = Curve({dt(2022, 1, 1): 1.0, dt(2023, 1, 1): 0.98}, id="A")
    with pytest.raises(NotImplementedError, match="`algorithm`: bad_algo"):
        solver = Solver(
            curves=[curve],
            instruments=[(IRS(dt(2022, 1, 1), "1Y", "Q"), (curve,), {})],
            s=[1],
            algorithm="bad_algo"
        )


def test_solver_float_rate_bond():
    """
    This test checks the rate method of FloatRateBond when using complex rate spread
    calculations (which artificially introduces Dual2 and then removes it)
    """
    d_c = Curve({
        dt(2022, 1,  1): 1.0,
        dt(2022, 7, 1): 0.94,
        dt(2023, 1, 1): 0.92,
        dt(2024, 1, 1): 0.9,
    }, id="credit")
    f_c = d_c.copy()
    f_c.id = "rfr"
    instruments = [
        (FloatRateBond(dt(2022, 1, 1), "6M", "Q", spread_compound_method="isda_compounding"),
         ([f_c, d_c],), {"metric": "spread", "settlement": dt(2022, 1, 3)}),
        (FloatRateBond(dt(2022, 1, 1), "1y", "Q", spread_compound_method="isda_compounding"),
         ([f_c, d_c],), {"metric": "spread", "settlement": dt(2022, 1, 3)}),
        (FloatRateBond(dt(2022, 1, 1), "18m", "Q", spread_compound_method="isda_compounding"),
         ([f_c, d_c],), {"metric": "spread", "settlement": dt(2022, 1, 3)}),
    ]
    solver = Solver([d_c], instruments, [25, 25, 25])
    result = d_c.rate(dt(2022, 7, 1), "1D")
    expected = f_c.rate(dt(2022, 7, 1), "1D") + 0.25
    assert abs(result - expected) < 1e-5


def test_solver_grad_s_s_vt_methods_equivalent():
    curve = Curve(
        nodes={
            dt(2022, 1, 1): 1.0,
            dt(2023, 1, 1): 1.0,
            dt(2024, 1, 1): 1.0,
            dt(2025, 1, 1): 1.0,
            dt(2026, 1, 1): 1.0,
            dt(2027, 1, 1): 1.0,
            dt(2028, 1, 1): 1.0,
            dt(2029, 1, 1): 1.0,
        },
        id="curve",
    )
    instruments = [
        IRS(dt(2022, 1, 1), "1y", "A", curves="curve"),
        IRS(dt(2022, 1, 1), "2y", "A", curves="curve"),
        IRS(dt(2022, 1, 1), "3y", "A", curves="curve"),
        IRS(dt(2022, 1, 1), "4y", "A", curves="curve"),
        IRS(dt(2022, 1, 1), "5y", "A", curves="curve"),
        IRS(dt(2022, 1, 1), "6y", "A", curves="curve"),
        IRS(dt(2022, 1, 1), "7y", "A", curves="curve"),
    ]
    solver = Solver(
        curves=[curve],
        instruments=instruments,
        s=[1.1, 1.2, 1.3, 1.4, 1.5, 1.6, 1.7],
    )
    grad_s_s_vt_fwddiff = solver._grad_s_s_vT_fwd_difference_method()
    solver._set_ad_order(order=2)
    grad_s_s_vt_final = solver._grad_s_s_vT_final_iteration_analytical()
    solver._set_ad_order(order=1)
    assert_allclose(grad_s_s_vt_final, grad_s_s_vt_fwddiff, atol=5e-7)


def test_gamma_raises():
    curve = Curve({
        dt(2022, 1, 1): 1.0,
        dt(2023, 1, 1): 1.0,
        dt(2024, 1, 1): 1.0,
        dt(2025, 1, 1): 1.0,
    }, id="v")
    instruments = [
        (IRS(dt(2022, 1, 1), "1Y", "Q"), (curve,), {}),
        (IRS(dt(2022, 1, 1), "2Y", "Q"), (curve,), {}),
        (IRS(dt(2022, 1, 1), "3Y", "Q"), (curve,), {}),
    ]
    s = np.array([1.0, 1.6, 2.0])
    solver = Solver(
        curves=[curve],
        instruments=instruments,
        s=s,
    )
    with pytest.raises(ValueError, match="`Solver` must be in ad order 2"):
        solver.gamma(100)


def test_delta_irs_guide():
    # this mirrors the delta user guide page
    usd_curve = Curve(
        nodes={
            dt(2022, 1, 1): 1.0,
            dt(2022, 2, 1): 1.0,
            dt(2022, 4, 1): 1.0,
            dt(2023, 1, 1): 1.0,
        },
        id="sofr",
    )
    instruments = [
        IRS(dt(2022, 1, 1), "1m", "A", curves="sofr"),
        IRS(dt(2022, 1, 1), "3m", "A", curves="sofr"),
        IRS(dt(2022, 1, 1), "1y", "A", curves="sofr"),
    ]
    usd_solver = Solver(
        curves=[usd_curve],
        id="usd_sofr",
        instruments=instruments,
        s=[2.5, 3.25, 4.0],
        instrument_labels=["1m", "3m", "1y"],
    )
    irs = IRS(
        effective=dt(2022, 1, 1),
        termination="6m",
        frequency="A",
        currency="usd",
        fixed_rate=6.0,
        curves="sofr",
    )
    result = irs.delta(solver=usd_solver)
    expected = DataFrame(
        [[0], [16.77263], [32.60487]],
        index=MultiIndex.from_product([["instruments"], ["usd_sofr"], ["1m", "3m", "1y"]], names=["type", "solver", "label"]),
        columns=MultiIndex.from_tuples([("usd", "usd")], names=["local_ccy", "display_ccy"])
    )
    assert_frame_equal(result, expected)


def test_delta_irs_guide_fx_base():
    # this mirrors the delta user guide page
    usd_curve = Curve(
        nodes={
            dt(2022, 1, 1): 1.0,
            dt(2022, 2, 1): 1.0,
            dt(2022, 4, 1): 1.0,
            dt(2023, 1, 1): 1.0,
        },
        id="sofr",
    )
    instruments = [
        IRS(dt(2022, 1, 1), "1m", "A", curves="sofr"),
        IRS(dt(2022, 1, 1), "3m", "A", curves="sofr"),
        IRS(dt(2022, 1, 1), "1y", "A", curves="sofr"),
    ]
    usd_solver = Solver(
        curves=[usd_curve],
        id="usd_sofr",
        instruments=instruments,
        s=[2.5, 3.25, 4.0],
        instrument_labels=["1m", "3m", "1y"],
    )
    irs = IRS(
        effective=dt(2022, 1, 1),
        termination="6m",
        frequency="A",
        currency="usd",
        fixed_rate=6.0,
        curves="sofr",
    )
    fxr = FXRates({"eurusd": 1.1})
    result = irs.delta(solver=usd_solver, base="eur", fx=fxr)
    expected = DataFrame(
        [[0, 0, 0],
         [15.247847, 15.247847, 16.772632],
         [29.640788, 29.640788, 32.60487],
         [0.926514, 0.926514, 0.0]],
        index=MultiIndex.from_tuples([
                ("instruments", "usd_sofr", "1m"),
                ("instruments", "usd_sofr", "3m"),
                ("instruments", "usd_sofr", "1y"),
                ("fx", "fx", "eurusd"),
            ],
            names=["type", "solver", "label"]),
        columns=MultiIndex.from_tuples([
                ("all", "eur"), ("usd", "eur"), ("usd", "usd"),
            ],
            names=["local_ccy", "display_ccy"])
    )
    assert_frame_equal(result, expected)


# def test_irs_delta_curves_undefined():
#     # the IRS is not constructed under best practice.
#     # The delta solver does not know how to price the irs
#     curve = Curve({dt(2022, 1, 1): 1.0, dt(2027, 1, 1): 0.99, dt(2032, 1, 1): 0.98},
#                   id="sonia")
#     instruments = [
#         IRS(dt(2022, 1, 1), "5y", "A", curves="sonia"),
#         IRS(dt(2027, 1, 1), "5y", "A", curves="sonia"),
#     ]
#     solver = Solver(
#         curves=[curve],
#         instruments=instruments,
#         s=[2.0, 2.5],
#     )
#     irs = IRS(dt(2022, 1, 1), "10y", "S", fixed_rate=2.38)
#     with pytest.raises(TypeError, match="`curves` have not been supplied"):
#         irs.delta(solver=solver)


def test_mechanisms_guide_gamma():
    instruments = [
        IRS(dt(2022, 1, 1), "4m", "Q", curves="sofr"),
        IRS(dt(2022, 1, 1), "8m", "Q", curves="sofr"),
    ]
    s = [1.85, 2.10]
    ll_curve = Curve(
        nodes={
            dt(2022, 1, 1): 1.0,
            dt(2022, 5, 1): 1.0,
            dt(2022, 9, 1): 1.0
        },
        interpolation="log_linear",
        id="sofr"
    )
    ll_solver = Solver(curves=[ll_curve], instruments=instruments, s=s,
                       instrument_labels=["4m", "8m"], id="sofr")

    instruments = [
        IRS(dt(2022, 1, 1), "3m", "Q", curves="estr"),
        IRS(dt(2022, 1, 1), "9m", "Q", curves="estr"),
    ]
    s = [0.75, 1.65]
    ll_curve = Curve(
        nodes={
            dt(2022, 1, 1): 1.0,
            dt(2022, 4, 1): 1.0,
            dt(2022, 10, 1): 1.0
        },
        interpolation="log_linear",
        id="estr",
    )
    combined_solver = Solver(
        curves=[ll_curve],
        instruments=instruments,
        s=s,
        instrument_labels=["3m", "9m"],
        pre_solvers=[ll_solver],
        id="estr"
    )

    irs = IRS(
        effective=dt(2022, 1, 1),
        termination="6m",
        frequency="Q",
        currency="usd",
        notional=500e6,
        fixed_rate=2.0,
        curves="sofr",
    )
    irs2 = IRS(
        effective=dt(2022, 1, 1),
        termination="6m",
        frequency="Q",
        currency="eur",
        notional=-300e6,
        fixed_rate=1.0,
        curves="estr",
    )
    pf = Portfolio([irs, irs2])
    pf.npv(solver=combined_solver)
    pf.delta(solver=combined_solver)
    fxr = FXRates({"eurusd": 1.10})
    fxr._set_ad_order(2)
    result = pf.gamma(solver=combined_solver, fx=fxr, base="eur")

    # TODO define test result
    raise NotImplementedError("this test needs to have a result comparison")


def test_solver_gamma_pnl_explain():
    instruments = [
        IRS(dt(2022, 1, 1), "10y", "A", currency="usd", curves="sofr"),
        IRS(dt(2032, 1, 1), "10y", "A", currency="usd", curves="sofr"),
        IRS(dt(2022, 1, 1), "10y", "A", currency="eur", curves="estr"),
        IRS(dt(2032, 1, 1), "10y", "A", currency="eur", curves="estr"),
        XCS(dt(2022, 1, 1), "10y", "A", currency="usd", leg2_currency="usd", curves=["estr", "eurusd", "sofr", "sofr"]),
        XCS(dt(2032, 1, 1), "10y", "A", currency="usd", leg2_currency="eur", curves=["estr", "eurusd", "sofr", "sofr"]),
    ]
    s_base = np.array([3.45, 2.85, 2.25, 0.9, -15, -10])
    sofr = Curve(
        nodes={dt(2022, 1, 1): 1.0, dt(2032, 1, 1): 1.0, dt(2042, 1, 1): 1.0},
        id="sofr"
    )
    estr = Curve(
        nodes={dt(2022, 1, 1): 1.0, dt(2032, 1, 1): 1.0, dt(2042, 1, 1): 1.0},
        id="estr"
    )
    eurusd = Curve(
        nodes={dt(2022, 1, 1): 1.0, dt(2032, 1, 1): 1.0, dt(2042, 1, 1): 1.0},
        id="eurusd"
    )
    fxr = FXRates({"eurusd": 1.05}, settlement=dt(2022, 1, 3))
    fxf = FXForwards(fxr, {
        "eureur": estr,
        "eurusd": eurusd,
        "usdusd": sofr
    })
    sofr_solver= Solver(
        curves=[sofr],
        instruments=instruments[:2],
        s=[3.45, 2.85],
        instrument_labels=["10y", "10y10y"],
        id="sofr",
        fx=fxf
    )
    estr_solver= Solver(
        curves=[estr],
        instruments=instruments[2:4],
        s=[2.25, 0.90],
        instrument_labels=["10y", "10y10y"],
        id="estr",
        fx=fxf
    )
    solver = Solver(
        curves=[eurusd],
        instruments=instruments[4:],
        s=[-10, -15],
        instrument_labels=["10y", "10y10y"],
        id="xccy",
        fx=fxf,
        pre_solvers=[sofr_solver, estr_solver]
    )

    pf = Portfolio([
        IRS(dt(2022, 1, 1), "20Y", "A", currency="eur", fixed_rate=2.0, notional=1e8, curves="estr"),
    ])
    npv_base = pf.npv(solver=solver)
    delta_base = pf.delta(solver=solver)
    gamma_base = pf.gamma(solver=solver)

    # s_new = np.array([3.65, 2.99, 2.10, 0.6, -25, -20])
    # solver.s = s_new
    # solver.iterate()
    # npv_new = pf.npv(solver=solver)

    # TODO comparison
    raise NotImplementedError("this test needs a result to be defined")


def test_gamma_with_fxrates_ad_order_1_raises():
    # when calculating gamma, AD order 2 is needed, the fx rates object passed
    # must also be converted. TODO
    pass


def test_error_labels():
    solver_with_error = Solver(
        curves=[
            Curve(
                nodes={dt(2022, 1, 1): 1.0, dt(2022, 7, 1): 1.0, dt(2023, 1, 1): 1.0},
                id="curve1"
            )
        ],
        instruments=[
            IRS(dt(2022, 1, 1), "1M", "A", curves="curve1"),
            IRS(dt(2022, 1, 1), "2M", "A", curves="curve1"),
            IRS(dt(2022, 1, 1), "3M", "A", curves="curve1"),
            IRS(dt(2022, 1, 1), "4M", "A", curves="curve1"),
            IRS(dt(2022, 1, 1), "8M", "A", curves="curve1"),
            IRS(dt(2022, 1, 1), "12M", "A", curves="curve1"),
        ],
        s=[2.0, 2.2, 2.3, 2.4, 2.45, 2.55],
        id="rates",
    )
    result = solver_with_error.error
    assert abs(result.loc[("rates", "rates0")] - 22.798) < 1e-2


def test_solver_non_unique_id_raises():
    curve = Curve({dt(2022, 1, 1): 1.0, dt(2023, 1, 1): 0.98}, id="A")
    solver = Solver(
        curves=[curve],
        instruments=[(IRS(dt(2022, 1, 1), "1Y", "Q"), (curve,), {})],
        s=[1],
        id="bad"
    )
    curve2 = Curve({dt(2022, 1, 1): 1.0, dt(2023, 1, 1): 0.98}, id="B")
    with pytest.raises(ValueError, match="Solver `id`s must be unique"):
        solver2 = Solver(
            curves=[curve2],
            instruments=[(IRS(dt(2022, 1, 1), "1Y", "Q"), (curve2,), {})],
            s=[1],
            id="bad",
            pre_solvers=[solver]
        )
