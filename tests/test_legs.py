import pytest
from datetime import datetime as dt
from pandas import DataFrame, Series, date_range, Index
from pandas.testing import assert_frame_equal, assert_series_equal
import numpy as np

import context
from rateslib import default_context
from rateslib.legs import (
    FixedLeg,
    FloatLeg,
    FloatPeriod,
    FixedPeriod,
    CustomLeg,
    FloatLegExchange,
    FixedLegExchange,
    FloatLegExchangeMtm,
    FixedLegExchangeMtm,
    Cashflow,
)
from rateslib.fx import FXRates, FXForwards
from rateslib.defaults import Defaults
from rateslib.curves import Curve


@pytest.fixture()
def curve():
    nodes = {
        dt(2022, 1, 1): 1.00,
        dt(2022, 4, 1): 0.99,
        dt(2022, 7, 1): 0.98,
        dt(2022, 10, 1): 0.97
    }
    return Curve(nodes=nodes, interpolation="log_linear")


class TestFloatLeg:

    @pytest.mark.parametrize("obj", [
        (FloatLeg(
            effective=dt(2022, 1, 1),
            termination=dt(2022, 6, 1),
            payment_lag=0,
            notional=1e9,
            convention="Act360",
            frequency="Q",
            fixing_method="rfr_payment_delay",
            spread_compound_method="none_simple",
            currency="nok",
        )),
        (FloatLegExchange(
            effective=dt(2022, 1, 1),
            termination=dt(2022, 6, 1),
            payment_lag=0,
            payment_lag_exchange=0,
            notional=1e9,
            convention="Act360",
            frequency="Q",
            fixing_method="rfr_payment_delay",
            spread_compound_method="none_simple",
            currency="nok",
        )),
        (FloatLegExchangeMtm(
            effective=dt(2022, 1, 1),
            termination=dt(2022, 6, 1),
            payment_lag=0,
            payment_lag_exchange=0,
            convention="Act360",
            frequency="Q",
            fixing_method="rfr_payment_delay",
            spread_compound_method="none_simple",
            currency="nok",
            alt_currency="usd",
            alt_notional=1e8,
        ))
    ])
    def test_float_leg_analytic_delta_with_npv(self, curve, obj):
        result = 5 * obj.analytic_delta(curve, curve)
        before_npv = -obj.npv(curve, curve)
        obj.float_spread = 5
        after_npv = -obj.npv(curve, curve)
        expected = after_npv - before_npv
        assert abs(result - expected) < 1e-7

    def test_float_leg_analytic_delta(self, curve):
        float_leg = FloatLeg(
            effective=dt(2022, 1, 1),
            termination=dt(2022, 6, 1),
            payment_lag=2,
            notional=1e9,
            convention="Act360",
            frequency="Q",
        )
        result = float_leg.analytic_delta(curve)
        assert abs(result - 41400.42965267) < 1e-7

    def test_float_leg_cashflows(self, curve):
        float_leg = FloatLeg(
            float_spread=None,
            effective=dt(2022, 1, 1),
            termination=dt(2022, 6, 1),
            payment_lag=2,
            notional=1e9,
            convention="Act360",
            frequency="Q",
        )
        result = float_leg.cashflows(curve)
        # test a couple of return elements
        assert abs(result.loc[0, Defaults.headers["cashflow"]] + 6610305.76834) < 1e-4
        assert abs(result.loc[1, Defaults.headers["df"]] - 0.98307) < 1e-4
        assert abs(result.loc[1, Defaults.headers["notional"]] - 1e9) < 1e-7

    def test_float_leg_npv(self, curve):
        float_leg = FloatLeg(
            float_spread=None,
            effective=dt(2022, 1, 1),
            termination=dt(2022, 6, 1),
            payment_lag=2,
            notional=1e9,
            convention="Act360",
            frequency="Q",
        )
        result = float_leg.npv(curve)
        assert abs(result + 16710777.50089434) < 1e-7

    def test_float_leg_fixings(self, curve):
        float_leg = FloatLeg(dt(2022, 2, 1), "9M", "Q", payment_lag=0, fixings=[10, 20])
        assert float_leg.periods[0].fixings == 10
        assert float_leg.periods[1].fixings == 20
        assert float_leg.periods[2].fixings is None

    def test_float_leg_fixings_series(self, curve):
        fixings = Series(0.5, index=date_range(dt(2021, 11, 1), dt(2022, 2, 15)))
        float_leg = FloatLeg(dt(2021, 12, 1), "9M", "M", payment_lag=0, fixings=fixings)
        assert_series_equal(float_leg.periods[0].fixings, fixings)  # december fixings
        assert_series_equal(float_leg.periods[1].fixings, fixings)  # january fixings
        assert_series_equal(float_leg.periods[2].fixings, fixings)  # february fixings
        assert float_leg.periods[4].fixings is None  # no march fixings

    def test_float_leg_fixings_scalar(self, curve):
        float_leg = FloatLeg(dt(2022, 2, 1), "9M", "Q", payment_lag=0, fixings=5.0)
        assert float_leg.periods[0].fixings == 5.0
        assert float_leg.periods[1].fixings is None
        assert float_leg.periods[2].fixings is None

    @pytest.mark.parametrize("method, param", [
        ("rfr_payment_delay", None),
        ("rfr_lockout", 1),
        ("rfr_observation_shift", 0),
    ])
    @pytest.mark.parametrize("fixings", [
        [[1.19, 1.19, -8.81]],
        Series([1.19, 1.19, -8.81], index=[
            dt(2022, 12, 28), dt(2022, 12, 29), dt(2022, 12, 30)
        ])
    ])
    def test_float_leg_rfr_fixings_table(self, method, param, fixings, curve):
        curve._set_ad_order(order=1)
        float_leg = FloatLeg(
            effective=dt(2022, 12, 28),
            termination="2M",
            frequency="M",
            fixings=fixings,
            currency="SEK",
            fixing_method=method,
            method_param=param,
        )
        float_leg.cashflows(curve)
        result = float_leg.fixings_table(curve)[dt(2022, 12, 28):dt(2023, 1, 1)]
        expected = DataFrame({
            "obs_dates": [
                dt(2022, 12, 28),
                dt(2022, 12, 29),
                dt(2022, 12, 30),
                dt(2022, 12, 31),
                dt(2023, 1, 1),
            ],
            "notional": [
                -1002914.0840790921,
                -1002914.0840790921,
                -1003192.739517848,
                -1002835.4299274746,
                -1002835.4299274748,
            ],
            "dcf": [0.0027777777777777778] * 5,
            "rates": [1.19, 1.19, -8.81, 4.01364, 4.01364]
        }).set_index("obs_dates")
        assert_frame_equal(result, expected)

    def test_float_leg_set_float_spread(self, curve):
        float_leg = FloatLeg(
            effective=dt(2022, 1, 1),
            termination=dt(2022, 6, 1),
            payment_lag=2,
            notional=-1e9,
            convention="Act360",
            frequency="Q",
        )
        assert float_leg.float_spread is None
        assert float_leg.periods[0].float_spread is 0

        float_leg.float_spread = 2.0
        assert float_leg.float_spread == 2.0
        assert float_leg.periods[0].float_spread == 2.0

    @pytest.mark.parametrize("method, spread_method, expected", [
        ("ibor", None, True),
        ("rfr_payment_delay", "none_simple", True),
        ("rfr_payment_delay", "isda_compounding", False),
        ("rfr_payment_delay", "isda_flat_compounding", False),
    ])
    def test_is_linear(self, method, spread_method, expected):
        float_leg = FloatLeg(
            effective=dt(2022, 1, 1),
            termination=dt(2022, 6, 1),
            payment_lag=2,
            notional=-1e9,
            convention="Act360",
            frequency="Q",
            fixing_method=method,
            spread_compound_method=spread_method,
        )
        assert float_leg._is_linear is expected

    @pytest.mark.parametrize("method, expected", [
        ("ISDA_compounding", 2.88250579),
        ("NONE_Simple", 4.637779609),
    ])
    def test_float_leg_spread_calculation(self, method, expected, curve):
        leg = FloatLeg(
            effective=dt(2022, 1, 1),
            termination=dt(2022, 6, 1),
            payment_lag=0,
            notional=1e9,
            convention="Act360",
            frequency="Q",
            fixing_method="rfr_payment_delay",
            spread_compound_method=method,
            currency="nok",
            float_spread=-399,
        )
        npv = leg.npv(curve, curve)
        result = leg._spread(-npv, curve, curve)
        assert abs(result + expected) < 1e-4
        leg.float_spread = result - 399
        assert abs(leg.npv(curve, curve)) < 1e-3

    def test_fixing_method_raises(self):
        with pytest.raises(ValueError, match="`fixing_method`"):
            FloatLeg(dt(2022, 2, 1), "9M", "Q", fixing_method="bad")

    @pytest.mark.parametrize("eff, term, freq, stub, expected", [
        (dt(2022, 1, 1), dt(2022, 6, 15), "Q", "ShortFront",
         [dt(2022, 1, 1), dt(2022, 3, 15), dt(2022, 6, 15)]),
        (dt(2022, 1, 1), dt(2022, 6, 15), "Q", "ShortBack",
         [dt(2022, 1, 1), dt(2022, 4, 1), dt(2022, 6, 15)]),
        (dt(2022, 1, 1), dt(2022, 9, 15), "Q", "LongFront",
         [dt(2022, 1, 1), dt(2022, 6, 15), dt(2022, 9, 15)]),
        (dt(2022, 1, 1), dt(2022, 9, 15), "Q", "LongBack",
         [dt(2022, 1, 1), dt(2022, 4, 1), dt(2022, 9, 15)]),
    ])
    def test_leg_periods_unadj_dates(self, eff, term, freq, stub, expected):
        leg = FloatLeg(effective=eff, termination=term, frequency=freq, stub=stub)
        assert leg.schedule.uschedule == expected

    @pytest.mark.parametrize("eff, term, freq, stub, expected", [
        (dt(2022, 1, 1), dt(2022, 6, 15), "Q", "ShortFront",
         [dt(2022, 1, 3), dt(2022, 3, 15), dt(2022, 6, 15)]),
        (dt(2022, 1, 1), dt(2022, 6, 15), "Q", "ShortBack",
         [dt(2022, 1, 3), dt(2022, 4, 1), dt(2022, 6, 15)]),
        (dt(2022, 1, 1), dt(2022, 9, 15), "Q", "LongFront",
         [dt(2022, 1, 3), dt(2022, 6, 15), dt(2022, 9, 15)]),
        (dt(2022, 1, 1), dt(2022, 9, 15), "Q", "LongBack",
         [dt(2022, 1, 3), dt(2022, 4, 1), dt(2022, 9, 15)]),
    ])
    def test_leg_periods_adj_dates(self, eff, term, freq, stub, expected):
        leg = FloatLeg(
            effective=eff, termination=term, frequency=freq, stub=stub, calendar="bus"
        )
        assert leg.schedule.aschedule == expected

    @pytest.mark.parametrize("eff, term, freq, stub, expected", [
        (dt(2022, 1, 1), dt(2022, 6, 15), "Q", "ShortFront",
         [FloatPeriod(
             start=dt(2022, 1, 3),
             end=dt(2022, 3, 15),
             payment=dt(2022, 3, 17),
             frequency="Q",
             notional=Defaults.notional,
             convention=Defaults.convention,
             termination=dt(2022, 6, 15),
         ),
             FloatPeriod(
                 start=dt(2022, 3, 15),
                 end=dt(2022, 6, 15),
                 payment=dt(2022, 6, 17),
                 frequency="Q",
                 notional=Defaults.notional,
                 convention=Defaults.convention,
                 termination=dt(2022, 6, 15),
             )]),
    ])
    def test_leg_periods_adj_dates2(self, eff, term, freq, stub, expected):
        leg = FloatLeg(
            effective=eff,
            termination=term,
            frequency=freq,
            stub=stub,
            payment_lag=2,
            calendar="bus"
        )
        for i in range(2):
            assert leg.periods[i].__repr__() == expected[i].__repr__()

    def test_spread_compound_method_raises(self):
        with pytest.raises(ValueError, match="`spread_compound_method`"):
            FloatLeg(dt(2022, 2, 1), "9M", "Q", spread_compound_method="bad")


class TestFloatLegExchange:

    def test_float_leg_exchange_notional_setter(self):
        float_leg_exc = FloatLegExchange(
            effective=dt(2022, 1, 1),
            termination=dt(2022, 6, 1),
            payment_lag=2,
            notional=-1e9,
            convention="Act360",
            frequency="Q",
        )
        float_leg_exc.notional = 200
        assert float_leg_exc.notional == 200

    def test_float_leg_exchange_amortization_setter(self):
        float_leg_exc = FloatLegExchange(
            effective=dt(2022, 1, 1),
            termination=dt(2022, 10, 1),
            payment_lag=2,
            notional=-1000,
            convention="Act360",
            frequency="Q",
        )
        float_leg_exc.amortization = -200

        cashflows = [2, 4, 6]
        cash_notionals = [None, -200, None, -200, None, -600]
        fixed_notionals = [None, -1000, None, -800, None, -600]
        for i in cashflows:
            assert isinstance(float_leg_exc.periods[i], Cashflow)
            assert float_leg_exc.periods[i].notional == cash_notionals[i - 1]

            assert isinstance(float_leg_exc.periods[i - 1], FloatPeriod)
            assert float_leg_exc.periods[i - 1].notional == fixed_notionals[i - 1]

    def test_float_leg_exchange_set_float_spread(self):
        float_leg_exc = FloatLegExchange(
            effective=dt(2022, 1, 1),
            termination=dt(2022, 10, 1),
            payment_lag=2,
            notional=-1000,
            convention="Act360",
            frequency="Q",
        )
        assert float_leg_exc.float_spread is None
        float_leg_exc.float_spread = 2.0
        assert float_leg_exc.float_spread == 2.0
        for period in float_leg_exc.periods:
            if isinstance(period, FloatPeriod):
                period.float_spread == 2.0

    def test_float_leg_exchange_amortization(self, curve):
        leg = FloatLegExchange(
            dt(2022, 1, 1),
            dt(2023, 1, 1),
            "Q",
            notional=5e6,
            amortization=1e6,
            payment_lag=0
        )
        assert len(leg.periods) == 9
        for i in [0, 2, 4, 6, 8]:
            assert type(leg.periods[i]) == Cashflow
        for i in [1, 3, 5, 7]:
            assert type(leg.periods[i]) == FloatPeriod
        assert leg.periods[1].notional == 5e6
        assert leg.periods[7].notional == 2e6
        assert leg.periods[8].notional == 2e6
        assert abs(leg.npv(curve).real) < 1e-9

    def test_float_leg_exchange_npv(self, curve):
        fle = FloatLegExchange(dt(2022, 2, 1), "6M", "Q", payment_lag=0)
        result = fle.npv(curve)
        assert abs(result) < 1e-9

    def test_float_leg_exchange_fixings_table(self, curve):
        fle = FloatLegExchange(dt(2022, 2, 1), "6M", "Q", payment_lag=0)
        result = fle.fixings_table(curve)
        expected = DataFrame({
            "notional": [-1009872.33778, -1010201.55052],
            "dcf": [0.002777777777777778, 0.002777777777777778],
            "rates": [4.01655, 4.01655]
        }, index=Index([dt(2022, 4, 30), dt(2022, 5, 1)], name="obs_dates"))
        assert_frame_equal(result[dt(2022, 4, 30): dt(2022, 5, 1)], expected)


class TestFixedLeg:

    def test_fixed_leg_analytic_delta(self, curve):
        fixed_leg = FixedLeg(
            effective=dt(2022, 1, 1),
            termination=dt(2022, 6, 1),
            payment_lag=2,
            notional=1e9,
            convention="Act360",
            frequency="Q",
        )
        result = fixed_leg.analytic_delta(curve)
        assert abs(result - 41400.42965267) < 1e-7

    def test_fixed_leg_npv(self, curve):
        fixed_leg = FixedLeg(
            effective=dt(2022, 1, 1),
            termination=dt(2022, 6, 1),
            payment_lag=2,
            notional=1e9,
            convention="Act360",
            frequency="Q",
            fixed_rate=4.00
        )
        result = fixed_leg.npv(curve)
        assert abs(result + 400 * fixed_leg.analytic_delta(curve)) < 1e-7

    def test_fixed_leg_cashflows(self, curve):
        fixed_leg = FixedLeg(
            fixed_rate=4.00,
            effective=dt(2022, 1, 1),
            termination=dt(2022, 6, 1),
            payment_lag=2,
            notional=-1e9,
            convention="Act360",
            frequency="Q",
        )
        result = fixed_leg.cashflows(curve)
        # test a couple of return elements
        assert abs(result.loc[0, Defaults.headers["cashflow"]] - 6555555.55555) < 1e-4
        assert abs(result.loc[1, Defaults.headers["df"]] - 0.98307) < 1e-4
        assert abs(result.loc[1, Defaults.headers["notional"]] + 1e9) < 1e-7

    def test_fixed_leg_set_fixed(self, curve):
        fixed_leg = FixedLeg(
            effective=dt(2022, 1, 1),
            termination=dt(2022, 6, 1),
            payment_lag=2,
            notional=-1e9,
            convention="Act360",
            frequency="Q",
        )
        assert fixed_leg.fixed_rate is None
        assert fixed_leg.periods[0].fixed_rate is None

        fixed_leg.fixed_rate = 2.0
        assert fixed_leg.fixed_rate == 2.0
        assert fixed_leg.periods[0].fixed_rate == 2.0


class TestFixedLegExchange:

    def test_fixed_leg_exchange_notional_setter(self):
        fixed_leg_exc = FixedLegExchange(
            effective=dt(2022, 1, 1),
            termination=dt(2022, 6, 1),
            payment_lag=2,
            notional=-1e9,
            convention="Act360",
            frequency="Q",
        )
        fixed_leg_exc.notional = 200
        assert fixed_leg_exc.notional == 200

    def test_fixed_leg_exchange_amortization_setter(self):
        fixed_leg_exc = FixedLegExchange(
            effective=dt(2022, 1, 1),
            termination=dt(2022, 10, 1),
            payment_lag=2,
            notional=-1000,
            convention="Act360",
            frequency="Q",
        )
        fixed_leg_exc.amortization = -200

        cashflows = [2, 4, 6]
        cash_notionals = [None, -200, None, -200, None, -600]
        fixed_notionals = [None, -1000, None, -800, None, -600]
        for i in cashflows:
            assert isinstance(fixed_leg_exc.periods[i], Cashflow)
            assert fixed_leg_exc.periods[i].notional == cash_notionals[i - 1]

            assert isinstance(fixed_leg_exc.periods[i - 1], FixedPeriod)
            assert fixed_leg_exc.periods[i - 1].notional == fixed_notionals[i - 1]

    def test_fixed_leg_exchange_set_fixed_rate(self):
        fixed_leg_exc = FixedLegExchange(
            effective=dt(2022, 1, 1),
            termination=dt(2022, 10, 1),
            payment_lag=2,
            notional=-1000,
            convention="Act360",
            frequency="Q",
        )
        assert fixed_leg_exc.fixed_rate is None
        fixed_leg_exc.fixed_rate = 2.0
        assert fixed_leg_exc.fixed_rate == 2.0
        for period in fixed_leg_exc.periods:
            if isinstance(period, FixedPeriod):
                period.fixed_rate == 2.0


class TestFloatLegExchangeMtm:

    @pytest.mark.parametrize("fx_fixings, exp", [
        (None, [None, None, None]),
        ([1.5], [1.5, None, None]),
        (1.25, [1.25, None, None]),
    ])
    def test_float_leg_exchange_mtm(self, fx_fixings, exp):
        float_leg_exch = FloatLegExchangeMtm(
            effective=dt(2022, 1, 3),
            termination=dt(2022, 7, 3),
            frequency="Q",
            notional=265,
            float_spread=5.0,
            currency="usd",
            alt_currency="eur",
            alt_notional=10e6,
            payment_lag_exchange=3,
            fx_fixings=fx_fixings,
        )
        fxr = FXRates({"eurusd": 1.05}, settlement=dt(2022, 1, 3))
        fxf = FXForwards(fxr, {
            "usdusd": Curve({dt(2022, 1, 1): 1.0, dt(2023, 1, 1): 0.965}),
            "eureur": Curve({dt(2022, 1, 1): 1.0, dt(2023, 1, 1): 0.985}),
            "eurusd": Curve({dt(2022, 1, 1): 1.0, dt(2023, 1, 1): 0.987}),
        })

        d = [dt(2022, 1, 6), dt(2022, 4, 6),
             dt(2022, 7, 6)]  # payment_lag_exchange is 3 days.
        rate = [_ if _ is not None else fxf.rate("eurusd", d[i]) for i, _ in
                enumerate(exp)]

        float_leg_exch.cashflows(fxf.curve("usd", "usd"), fxf.curve("usd", "usd"), fxf)
        assert float(float_leg_exch.periods[0].cashflow - 10e6 * rate[0]) < 1e-6
        assert float(
            float_leg_exch.periods[2].cashflow - 10e6 * (rate[1] - rate[0])) < 1e-6
        assert float(
            float_leg_exch.periods[4].cashflow - 10e6 * (rate[2] - rate[1])) < 1e-6
        assert float_leg_exch.periods[4].payment == d[-1]

        assert float_leg_exch.periods[1].notional == 10e6 * rate[0]
        assert type(float_leg_exch.periods[1]) is FloatPeriod
        assert float_leg_exch.periods[3].notional == 10e6 * rate[1]
        assert type(float_leg_exch.periods[3]) is FloatPeriod

        assert float_leg_exch.periods[-1].notional == 10e6 * rate[1]

    def test_mtm_leg_exchange_spread(self):
        expected = [522.324262, 522.324262]
        leg = FloatLegExchangeMtm(
            effective=dt(2022, 1, 3),
            termination=dt(2022, 7, 3),
            frequency="Q",
            notional=265,
            currency="usd",
            alt_currency="eur",
            alt_notional=10e6,
            fixing_method="rfr_payment_delay",
            spread_compound_method="isda_compounding",
            payment_lag_exchange=0,
            payment_lag=0,
            float_spread=0.0,
        )
        fxr = FXRates({"eurusd": 1.05}, settlement=dt(2022, 1, 3))
        fxf = FXForwards(fxr, {
            "usdusd": Curve({dt(2022, 1, 1): 1.0, dt(2023, 1, 1): 0.965}),
            "eureur": Curve({dt(2022, 1, 1): 1.0, dt(2023, 1, 1): 0.985}),
            "eurusd": Curve({dt(2022, 1, 1): 1.0, dt(2023, 1, 1): 0.987}),
        })

        npv = leg.npv(fxf.curve("usd", "usd"), fxf.curve("usd", "usd"), fxf)
        a_delta = leg.analytic_delta(fxf.curve("usd", "usd"), fxf.curve("usd", "usd"), fxf)
        result = leg._spread(100, fxf.curve("usd", "usd"), fxf.curve("usd", "usd"), fxf)
        leg.float_spread = result
        npv2 = leg.npv(fxf.curve("usd", "usd"), fxf.curve("usd", "usd"), fxf)
        assert abs(npv2 - npv - 100) < 1e-5

    @pytest.mark.parametrize("fx_fixings, exp", [
        (None, [None, None, None]),
        ([1.5], [1.5, None, None]),
        (1.25, [1.25, None, None]),
    ])
    def test_mtm_leg_fx_fixings_warn_raise(self, curve, fx_fixings, exp):
        float_leg_exch = FloatLegExchangeMtm(
            effective=dt(2022, 1, 3),
            termination=dt(2022, 7, 3),
            frequency="Q",
            notional=265,
            float_spread=5.0,
            currency="usd",
            alt_currency="eur",
            alt_notional=10e6,
            payment_lag_exchange=3,
            fx_fixings=fx_fixings,
        )

        with pytest.warns(UserWarning):
            with default_context("no_fx_fixings_for_xcs", "warn"):
                float_leg_exch.npv(curve)

        with pytest.raises(ValueError, match="`fx` is required when `fx_fixings` are"):
            with default_context("no_fx_fixings_for_xcs", "raise"):
                float_leg_exch.npv(curve)


def test_leg_amortization():
    fixed_leg = FixedLeg(dt(2022, 1, 1), dt(2022, 10, 1), frequency="Q",
                         notional=1e6, amortization=250e3, fixed_rate=2.0)
    for i, period in enumerate(fixed_leg.periods):
        assert period.notional == 1e6 - 250e3*i

    float_leg = FloatLeg(dt(2022, 1, 1), dt(2022, 10, 1), frequency="Q",
                         notional=1e6, amortization=250e3, float_spread=2.0)
    for i, period in enumerate(float_leg.periods):
        assert period.notional == 1e6 - 250e3 * i


def test_custom_leg_raises():
    with pytest.raises(ValueError):
        _ = CustomLeg(periods=["bad_period"])


def test_custom_leg():
    float_leg = FloatLeg(
        effective=dt(2022, 1, 1), termination=dt(2023, 1, 1), frequency="S"
    )
    custom_leg = CustomLeg(periods=float_leg.periods)
    for i, period in enumerate(custom_leg.periods):
        assert period == float_leg.periods[i]


@pytest.mark.parametrize("fx_fixings, exp", [
    (None, [None, None, None]),
    ([1.5], [1.5, None, None]),
    (1.25, [1.25, None, None]),
])
def test_fixed_leg_exchange_mtm(fx_fixings, exp):
    fixed_leg_exch = FixedLegExchangeMtm(
        effective=dt(2022, 1, 3),
        termination=dt(2022, 7, 3),
        frequency="Q",
        notional=265,
        fixed_rate=5.0,
        currency="usd",
        alt_currency="eur",
        alt_notional=10e6,
        payment_lag_exchange=3,
        fx_fixings=fx_fixings
    )
    fxr = FXRates({"eurusd": 1.05}, settlement=dt(2022, 1, 3))
    fxf = FXForwards(fxr, {
        "usdusd": Curve({dt(2022, 1, 1): 1.0, dt(2023, 1, 1): 0.965}),
        "eureur": Curve({dt(2022, 1, 1): 1.0, dt(2023, 1, 1): 0.985}),
        "eurusd": Curve({dt(2022, 1, 1): 1.0, dt(2023, 1, 1): 0.987}),
    })

    d = [dt(2022, 1, 6), dt(2022, 4, 6), dt(2022, 7, 6)]  # payment_lag_exchange is 3 days.
    rate = [_ if _ is not None else fxf.rate("eurusd", d[i]) for i, _ in enumerate(exp)]

    fixed_leg_exch.cashflows(fxf.curve("usd", "usd"), fxf.curve("usd", "usd"), fxf)
    assert float(fixed_leg_exch.periods[0].cashflow - 10e6 * rate[0]) < 1e-6
    assert float(fixed_leg_exch.periods[2].cashflow - 10e6 * (rate[1]-rate[0])) < 1e-6
    assert float(fixed_leg_exch.periods[4].cashflow - 10e6 * (rate[2]-rate[1])) < 1e-6
    assert fixed_leg_exch.periods[4].payment == dt(2022, 7, 6)

    assert fixed_leg_exch.periods[1].notional == 10e6 * rate[0]
    assert type(fixed_leg_exch.periods[1]) is FixedPeriod
    assert fixed_leg_exch.periods[3].notional == 10e6 * rate[1]
    assert type(fixed_leg_exch.periods[3]) is FixedPeriod

    assert fixed_leg_exch.periods[-1].notional == 10e6 * rate[1]


@pytest.mark.parametrize("type_", (FloatLegExchangeMtm, FixedLegExchangeMtm))
def test_mtm_leg_raises(type_):
    with pytest.raises(ValueError, match="`amortization`"):
        type_(
            effective=dt(2022, 1, 3),
            termination=dt(2022, 7, 3),
            frequency="Q",
            notional=265,
            currency="usd",
            alt_currency="eur",
            alt_notional=10e6,
            payment_lag_exchange=3,
            amortization=1000,
        )

    with pytest.raises(TypeError, match="`fx_fixings` should be scalar"):
        type_(
            effective=dt(2022, 1, 3),
            termination=dt(2022, 7, 3),
            frequency="Q",
            notional=265,
            currency="usd",
            alt_currency="eur",
            alt_notional=10e6,
            payment_lag_exchange=3,
            fx_fixings="bad_type"
        )


@pytest.mark.parametrize("type_, expected, kw", [
    (FloatLegExchangeMtm, [522.324262, 522.324262], {"float_spread": 1.0}),
    (FixedLegExchangeMtm, [522.324262, 53772.226595], {"fixed_rate": 2.5}),
])
def test_mtm_leg_exchange_metrics(type_, expected, kw):
    leg = type_(
        effective=dt(2022, 1, 3),
        termination=dt(2022, 7, 3),
        frequency="Q",
        notional=265,
        currency="usd",
        alt_currency="eur",
        alt_notional=10e6,
        payment_lag_exchange=0,
        payment_lag=0,
        **kw
    )
    fxr = FXRates({"eurusd": 1.05}, settlement=dt(2022, 1, 3))
    fxf = FXForwards(fxr, {
        "usdusd": Curve({dt(2022, 1, 1): 1.0, dt(2023, 1, 1): 0.965}),
        "eureur": Curve({dt(2022, 1, 1): 1.0, dt(2023, 1, 1): 0.985}),
        "eurusd": Curve({dt(2022, 1, 1): 1.0, dt(2023, 1, 1): 0.987}),
    })

    d = [dt(2022, 1, 6), dt(2022, 4, 6), dt(2022, 7, 6)]  # payment_lag_exchange is 3 days.
    rate = [fxf.rate("eurusd", d[i]) for i in range(3)]

    result = leg.analytic_delta(fxf.curve("usd", "usd"), fxf.curve("usd", "usd"), fxf)
    assert float(result - expected[0]) < 1e-6

    result = leg.npv(fxf.curve("usd", "usd"), fxf.curve("usd", "usd"), fxf)
    assert float(result - expected[1]) < 1e-6