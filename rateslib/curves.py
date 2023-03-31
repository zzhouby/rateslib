"""
.. ipython:: python
   :suppress:

   from rateslib.curves import *
   from datetime import datetime as dt
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional, Union, Callable, Any
from pandas.tseries.offsets import CustomBusinessDay
from uuid import uuid4
import numpy as np
import json
from math import floor
from rateslib import defaults
from rateslib.dual import Dual, Dual2, dual_log, dual_exp
from rateslib.splines import PPSpline
from rateslib.defaults import plot
from rateslib.calendars import create_calendar, get_calendar, add_tenor, dcf


# Licence: Creative Commons - Attribution-NonCommercial-NoDerivatives 4.0 International
# Commercial use of this code, and/or copying and redistribution is prohibited.
# Contact rateslib at gmail.com if this code is observed outside its intended sphere.

class Serialize:
    """
    Methods mixin for serializing and solving :class:`Curve` or :class:`LineCurve` s.
    """
    def to_json(self):
        """
        Convert the parameters of the curve to JSON format.

        Returns
        -------
        str
        """
        if self.t is None:
            t = None
        else:
            t = [t.strftime("%Y-%m-%d") for t in self.t]

        container = {
            "nodes": {dt.strftime("%Y-%m-%d"): v.real for dt, v in self.nodes.items()},
            "interpolation": self.interpolation if isinstance(self.interpolation, str) else None,
            "t": t,
            "c": self.spline.c if self.c_init else None,
            "id": self.id,
            "convention": self.convention,
            "endpoints": self.spline_endpoints,
            "modifier": self.modifier,
            "calendar_type": self.calendar_type,
            "ad": self.ad,
        }

        if self.calendar_type == "null":
            container.update({"calendar": None})
        elif "named: " in self.calendar_type:
            container.update({"calendar": self.calendar_type[7:]})
        else:  # calendar type is custom
            container.update({
                "calendar": {
                    "weekmask": self.calendar.weekmask,
                    "holidays": [
                        d.item().strftime("%Y-%m-%d")  # numpy/pandas timestamp to py
                        for d in self.calendar.calendar.holidays
                    ]
                }
            })

        return json.dumps(container, default=str)

    @classmethod
    def from_json(cls, curve, **kwargs):
        """
        Reconstitute a curve from JSON.

        Parameters
        ----------
        curve : str
            The JSON string representation of the curve.

        Returns
        -------
        Curve or LineCurve
        """
        serial = json.loads(curve)

        serial["nodes"] = {
            datetime.strptime(dt, "%Y-%m-%d"): v for dt, v in serial["nodes"].items()
        }

        if serial["calendar_type"] == "custom":
            # must load and construct a custom holiday calendar from serial dates
            serial["calendar"] = create_calendar(
                rules=[
                    datetime.strptime(d, "%Y-%m-%d")
                    for d in serial["calendar"]["holidays"]
                ],
                weekmask=serial["calendar"]["weekmask"]
            )

        if serial["t"] is not None:
            serial["t"] = [datetime.strptime(t, "%Y-%m-%d") for t in serial["t"]]
        return cls(**{**serial, **kwargs})

    def copy(self):
        """
        Create an identical copy of the curve object.

        Returns
        -------
        Curve or LineCurve
        """
        return self.from_json(self.to_json())

    def __eq__(self, other):
        """Test two curves are identical"""
        if type(self) != type(other):
            return False
        attrs = [attr for attr in dir(self) if attr[:1] != "_"]
        for attr in attrs:
            if callable(getattr(self, attr, None)):
                continue
            elif getattr(self, attr, None) != getattr(other, attr, None):
                return False
        return True

    def _set_ad_order(self, order):
        """
        Change the node values to float, Dual or Dual2 based on input parameter.
        """
        if order == getattr(self, "ad", None):
            return None
        if order == 0:
            self.ad = 0
            self.nodes = {k: float(v) for i, (k, v) in enumerate(self.nodes.items())}
            self.csolve()
            return None
        elif order == 1:
            self.ad, DualType = 1, Dual
        elif order == 2:
            self.ad, DualType = 2, Dual2
        else:
            raise ValueError("`order` can only be in {0, 1, 2} for auto diff calcs.")
        self.nodes = {k: DualType(float(v), f"{self.id}{i}")
                      for i, (k, v) in enumerate(self.nodes.items())}
        self.csolve()
        return None


class PlotCurve:
    """
    Methods mixin for plotting :class:`Curve` or :class:`LineCurve` s.
    """
    def plot(
        self,
        tenor: str,
        right: Optional[Union[datetime, str]] = None,
        left: Optional[Union[datetime, str]] = None,
        comparators: list[Curve] = [],
        difference: bool = False,
        labels: list[str] = [],
    ):
        """
        Plot given forward tenor rates from the curve.

        Parameters
        ----------
        tenor : str
            The tenor of the forward rates to plot, e.g. "1D", "3M".
        right : datetime or str, optional
            The right bound of the graph. If given as str should be a tenor format
            defining a point measured from the initial node date of the curve.
            Defaults to the final node of the curve minus the ``tenor``.
        left : datetime or str, optional
            The left bound of the graph. If given as str should be a tenor format
            defining a point measured from the initial node date of the curve.
            Defaults to the initial node of the curve.
        comparators: list[Curve]
            A list of curves which to include on the same plot as comparators.
        difference : bool
            Whether to plot as comparator minus base curve or outright curve levels in
            plot. Default is `False`.
        labels : list[str]
            A list of strings associated with the plot and comparators. Must be same
            length as number of plots.

        Returns
        -------
        (fig, ax, line) : Matplotlib.Figure, Matplotplib.Axes, Matplotlib.Lines2D
        """
        if left is None:
            left_ : datetime = self.node_dates[0]
        elif isinstance(left, str):
            left_ = add_tenor(self.node_dates[0], left, None, None)
        elif isinstance(left, datetime):
            left_ = left
        else:
            raise ValueError("`left` must be supplied as datetime or tenor string.")

        if right is None:
            right_ : datetime = add_tenor(self.node_dates[-1], "-" + tenor, None, None)
        elif isinstance(right, str):
            right_ = add_tenor(self.node_dates[0], right, None, None)
        elif isinstance(right, datetime):
            right_ = right
        else:
            raise ValueError("`right` must be supplied as datetime or tenor string.")

        points : int = (right_ - left_).days
        x = [left_ + timedelta(days=i) for i in range(points)]
        rates = [self.rate(_, tenor) for _ in x]
        if not difference:
            y = [rates]
            if comparators is not None:
                for comparator in comparators:
                    y.append([comparator.rate(_, tenor) for _ in x])
        elif difference and len(comparators) > 0:
            y = []
            for comparator in comparators:
                diff = [comparator.rate(_, tenor) - rates[i] for i, _ in enumerate(x)]
                y.append(diff)
        return plot(x, y, labels)

    def _plot_fx(
        self,
        curve_foreign: Curve,
        fx_rate: Union[float, Dual],
        fx_settlement: Optional[datetime] = None,
        left: datetime = None,
        right: datetime = None,
        points: int = None,
    ):  # pragma: no cover
        """
        Debugging method?
        """
        def forward_fx(date, curve_domestic, curve_foreign, fx_rate, fx_settlement):
            _ = self[date] / curve_foreign[date]
            if fx_settlement is not None:
                _ *= curve_foreign[fx_settlement] / curve_domestic[fx_settlement]
            _ *= fx_rate
            return _

        left, right = self.node_dates[0], self.node_dates[-1]
        points = (right - left).days
        x = [left + timedelta(days=i) for i in range(points)]
        rates = [forward_fx(_, self, curve_foreign, fx_rate, fx_settlement) for _ in x]
        return plot(x, [rates])


class Curve(Serialize, PlotCurve):
    """
    Object to return DFs parametrised by DFs at given node dates and interpolation.

    Parameters
    ----------
    nodes : dict[datetime: float]
        Degrees of freedom of the curve denoted by a node date and a corresponding
        DF at that point.
    interpolation : str in {"log_linear", "linear", "linear_zero_rate"} or callable
        The interpolation used in the non-spline section of the curve. That is the part
        of the curve between the first node in ``nodes`` and the first knot in ``t``.
        If a callable, this allows a user-defined interpolation scheme, and this must
        have the signature ``method(date, nodes)``, where ``date`` is the datetime
        whose DF will be returned and ``nodes`` is as above and is passed to the
        callable.
    t : list[datetime], optional
        The knot locations for the B-spline log-cubic interpolation section of the
        curve. If *None* all interpolation will be done by the local method specified in
        ``interpolation``.
    c : list[float], optional
        The B-spline coefficients used to define the log-cubic spline. If not given
        will use :meth:`csolve`.
    endpoints : str or list, optional
        The left and right endpoint constraint for the spline solution. Valid values are
        in {"natural", "not_a_knot"}. If a list, supply the left endpoint then the
        right endpoint.
    id : str, optional, set by Default
        The unique identifier to distinguish between curves in a multicurve framework.
    convention : str, optional, set by Default
        The convention of the curve for determining rates.
    modifier : str, optional
        The modification rule, in {"F", "MF", "P", "MP"}, for determining rates.
    calendar : calendar or str, optional
        The holiday calendar object to use. If str, looks up named calendar from
        static data. Used for determining rates.
    ad : int in {0, 1, 2}, optional
        Sets the automatic differentiation order. Defines whether to convert node
        values to float, :class:`~rateslib.dual.Dual` or
        :class:`~rateslib.dual.Dual2`. It is advised against
        using this setting directly. It is mainly used internally.
    """
    _op_exp = staticmethod(dual_exp)  # Curve is DF based: log-cubic spline is exp'ed
    _op_log = staticmethod(dual_log)  # Curve is DF based: spline is applied over log
    _ini_solve = 1  # Curve is assumed to have initial DF node at 1.0 as constraint

    def __init__(
        self,
        nodes: dict,
        interpolation: Optional[Union[str, Callable]] = None,
        t: Optional[list[datetime]] = None,
        c: Optional[list[float]] = None,
        endpoints: Optional[str] = None,
        id: Optional[str] = None,
        convention: Optional[str] = None,
        modifier: Optional[Union[str, bool]] = False,
        calendar: Optional[Union[CustomBusinessDay, str]] = None,
        ad: int = 0,
        **kwargs
    ):
        self.id = id or uuid4().hex[:5] + "_"  # 1 in a million clash
        self.nodes = nodes  # nodes.copy()
        self.node_dates = list(self.nodes.keys())
        self.n = len(self.node_dates)
        self.interpolation = interpolation or defaults.interpolation[type(self).__name__]

        # Parameters for the rate derivation
        self.convention = convention or defaults.convention
        self.modifier = defaults.modifier if modifier is False else modifier
        self.calendar, self.calendar_type = get_calendar(calendar, kind=True)
        if self.calendar_type == "named":
            self.calendar_type = f"named: {calendar.lower()}"

        # Parameters for PPSpline
        if endpoints is None:
            self.spline_endpoints = [defaults.endpoints, defaults.endpoints]
        elif isinstance(endpoints, str):
            self.spline_endpoints = [endpoints.lower(), endpoints.lower()]
        else:
            self.spline_endpoints = [_.lower() for _ in endpoints]

        self.t = t
        self.c_init = False if c is None else True
        if t is not None:
            self.spline = PPSpline(4, t, c)
            if len(self.t) < 10 and "not_a_knot" in self.spline_endpoints:
                raise ValueError(
                    "`endpoints` cannot be 'not_a_knot' with only 1 interior breakpoint"
                )
        else:
            self.spline = None

        self._set_ad_order(order=ad)

    def __getitem__(self, date: datetime):
        if self.spline is None or date <= self.t[0]:
            if isinstance(self.interpolation, Callable):
                return self.interpolation(date, self.nodes.copy())
            return self._local_interp_(date)
        else:
            return self._op_exp(self.spline.ppev_single(date))

# Licence: Creative Commons - Attribution-NonCommercial-NoDerivatives 4.0 International
# Commercial use of this code, and/or copying and redistribution is prohibited.
# Contact rateslib at gmail.com if this code is observed outside its intended sphere.

    def _local_interp_(self, date: datetime):
        if date < self.node_dates[0]:
            return 0  # then date is in the past and DF is zero
        l_index = index_left(self.node_dates, self.n, date)
        node_left, node_right = self.node_dates[l_index], self.node_dates[l_index+1]
        return interpolate(
            date,
            node_left,
            self.nodes[node_left],
            node_right,
            self.nodes[node_right],
            self.interpolation,
            self.node_dates[0]
        )

    # def plot(self, *args, **kwargs):
    #     return super().plot(*args, **kwargs)

    def rate(
        self,
        effective: datetime,
        termination: Union[datetime, str],
        modifier: Optional[Union[str, bool]] = False,
        # calendar: Optional[Union[CustomBusinessDay, str, bool]] = False,
        # convention: Optional[str] = None,
    ):
        """
        Calculate the rate on the `Curve` using DFs.

        If rates are sought for dates prior to the initial node of the curve `None`
        will be returned.

        Parameters
        ----------
        effective : datetime
            The start date of the period for which to calculate the rate.
        termination : datetime or str
            The end date of the period for which to calculate the rate.
        modifier : str, optional
            The day rule if determining the termination from tenor. If `False` is
            determined from the `Curve` modifier.
        # calendar : CustomBusinessDay, str, None, optional
        #     The business day calendar to determine valid business days from which to
        #     determine rates. If `False` is determined from the `Curve` calendar.
        # convention : str, optional
        #     The day count convention used for calculating rates. If `None` is
        #     determined from the `Curve` convention.

        Returns
        -------
        Dual, Dual2 or float

        Notes
        -----
        Calculating rates from a curve implies that the conventions attached to the
        specific index, e.g. USD SOFR, or GBP SONIA, are applicable and these should
        be set at initialisation of the ``Curve``.

        ``modifier`` is only used if a tenor is given as the termination.

        Major indexes, such as legacy IBORs, and modern RFRs typically use a
        ``convention`` which is either `"Act365F"` or `"Act360"`. These conventions
        do not need additional parameters, such as the `termination` of a leg,
        the `frequency` or a leg or whether it is a `stub` to calculate a DCF.
        """
        modifier = self.modifier if modifier is False else modifier
        # calendar = self.calendar if calendar is False else calendar
        # convention = self.convention if convention is None else convention

        if isinstance(termination, str):
            termination = add_tenor(effective, termination, modifier, self.calendar)
        try:
            df_ratio = self[effective] / self[termination]
        except ZeroDivisionError:
            return None
        rate = (df_ratio - 1) / dcf(effective, termination, self.convention)
        return rate * 100

    def csolve(self):
        """
        Solve the coefficients, ``c``, of the :class:`PPSpline`.

        Only impacts curves which have a knot sequence, ``t``, and a ``PPSpline``.
        Only solves if ``c`` not given at ``Curve`` initialisation.

        Returns
        -------
        Set the coefficients for the PPSpline : None

        Notes
        -----
        Uses the ``spline_endpoints`` attribute on the class to determine the solving
        method.
        """
        if self.spline is None or self.c_init:
            return None

        self.spline = PPSpline(4, self.t, None)
        tau = [k for k in self.nodes.keys() if k >= self.t[0]]
        y = [self._op_log(v) for k, v in self.nodes.items() if k >= self.t[0]]

        # Left side constraint
        if self.spline_endpoints[0].lower() == "natural":
            tau.insert(0, self.t[0])
            y.insert(0, 0)
            left_n = 2
        elif self.spline_endpoints[0].lower() == "not_a_knot":
            self.spline.t.pop(4)
            self.spline.n -= 1
            left_n = 0
        else:
            raise NotImplementedError(
                f"Endpoint method '{self.spline_endpoints[0]}' not implemented."
            )

        # Right side constraint
        if self.spline_endpoints[1].lower() == "natural":
            tau.append(self.t[-1])
            y.append(0)
            right_n = 2
        elif self.spline_endpoints[1].lower() == "not_a_knot":
            self.spline.t.pop(-5)
            self.spline.n -= 1
            right_n = 0
        else:
            raise NotImplementedError(
                f"Endpoint method '{self.spline_endpoints[0]}' not implemented."
            )

        self.spline.csolve(np.array(tau), np.array(y), left_n, right_n)
        return None

    def shift(self, spread: float):
        """
        Create a new curve by vertically adjusting the curve by a set number of basis
        points.

        This curve adjustment preserves the shape of the curve but moves it up or
        down as a translation.
        This method is suitable as a way to assess value changes of instruments when
        a parallel move higher or lower in yields is predicted.

        Parameters
        ----------
        spread : float, Dual, Dual2
            The number of basis points added to the existing curve.

        Returns
        -------
        Curve

        Examples
        --------
        .. ipython:: python

           from rateslib.curves import Curve

        .. ipython:: python

           curve = Curve(
               nodes = {
                   dt(2022, 1, 1): 1.0,
                   dt(2023, 1, 1): 0.988,
                   dt(2024, 1, 1): 0.975,
                   dt(2025, 1, 1): 0.965,
                   dt(2026, 1, 1): 0.955,
                   dt(2027, 1, 1): 0.9475
               },
               t = [
                   dt(2024, 1, 1), dt(2024, 1, 1), dt(2024, 1, 1), dt(2024, 1, 1),
                   dt(2025, 1, 1),
                   dt(2026, 1, 1),
                   dt(2027, 1, 1), dt(2027, 1, 1), dt(2027, 1, 1), dt(2027, 1, 1),
               ],
           )
           shifted_curve = curve.shift(25)
           curve.plot("1d", comparators=[shifted_curve], labels=["orig", "shift"])

        .. plot::

           from rateslib.curves import *
           import matplotlib.pyplot as plt
           from datetime import datetime as dt
           curve = Curve(
               nodes = {
                   dt(2022, 1, 1): 1.0,
                   dt(2023, 1, 1): 0.988,
                   dt(2024, 1, 1): 0.975,
                   dt(2025, 1, 1): 0.965,
                   dt(2026, 1, 1): 0.955,
                   dt(2027, 1, 1): 0.9475
               },
               t = [
                   dt(2024, 1, 1), dt(2024, 1, 1), dt(2024, 1, 1), dt(2024, 1, 1),
                   dt(2025, 1, 1),
                   dt(2026, 1, 1),
                   dt(2027, 1, 1), dt(2027, 1, 1), dt(2027, 1, 1), dt(2027, 1, 1),
               ],
           )
           spread_curve = curve.shift(25)
           fig, ax, line = curve.plot("1d", comparators=[spread_curve], labels=["orig", "shift"])
           plt.show()

        """

        # TODO test whether need to lower AD order before adding spread

        v1v2 = [1.0] * (self.n - 1)
        n = [0] * (self.n - 1)
        d = 1/365 if self.convention.upper() != "ACT360" else 1/360
        v_new = [1.0] * (self.n)
        for i, (k, v) in enumerate(self.nodes.items()):
            if i == 0:
                continue
            n[i-1] = (k - self.node_dates[i-1]).days
            v1v2[i-1] = (self.nodes[self.node_dates[i-1]] / v) ** (1 / n[i-1])
            v_new[i] = v_new[i-1] / (v1v2[i-1] + d * spread / 10000) ** n[i-1]

        nodes = self.nodes.copy()
        for i, (k, v) in enumerate(nodes.items()):
            nodes[k] = v_new[i]

        _ = Curve(
            nodes=nodes,
            interpolation=self.interpolation,
            t=self.t,
            c=None,
            endpoints=self.spline_endpoints,
            id=None,
            convention=self.convention,
            modifier=self.modifier,
            calendar=self.calendar,
            ad=self.ad
        )
        return _

    def _translate_nodes(self, start: datetime):
        scalar = 1 / self[start]
        new_nodes = {
            k: scalar * v for k, v in self.nodes.items()
        }

        # re-organise the nodes on the new curve
        if start == self.node_dates[1]:
            del new_nodes[self.node_dates[1]]
        del new_nodes[self.node_dates[0]]
        new_nodes = {start: 1.0, **new_nodes}
        return new_nodes

    def translate(self, start: datetime, t: bool = False):
        """
        Create a new curve with an initial node date moved forward keeping all else
        constant.

        This curve adjustment preserves forward curve expectations as time evolves.
        This method is suitable as a way to create a subsequent *opening* curve from a
        previous day's *closing* curve.

        Parameters
        ----------
        start : datetime
            The new initial node date for the curve, must be in the domain:
            (node_date[0], node_date[1]]
        t : bool
            Set to *True* if the initial knots of the knot sequence should be
            translated forward.

        Returns
        -------
        Curve

        Examples
        ---------
        The basic use of this function translates a curve forward in time and the
        plot demonstrates its rates are exactly the same as initially forecast.

        .. ipython:: python

           curve = Curve(
               nodes = {
                   dt(2022, 1, 1): 1.0,
                   dt(2023, 1, 1): 0.988,
                   dt(2024, 1, 1): 0.975,
                   dt(2025, 1, 1): 0.965,
                   dt(2026, 1, 1): 0.955,
                   dt(2027, 1, 1): 0.9475
               },
               t = [
                   dt(2024, 1, 1), dt(2024, 1, 1), dt(2024, 1, 1), dt(2024, 1, 1),
                   dt(2025, 1, 1),
                   dt(2026, 1, 1),
                   dt(2027, 1, 1), dt(2027, 1, 1), dt(2027, 1, 1), dt(2027, 1, 1),
               ],
           )
           translated_curve = curve.translate(dt(2022, 12, 1))
           curve.plot(
               "1d",
               comparators=[translated_curve],
               labels=["orig", "translated"],
               left=dt(2022, 12, 1),
           )

        .. plot::

           from rateslib.curves import *
           import matplotlib.pyplot as plt
           from datetime import datetime as dt
           curve = Curve(
               nodes = {
                   dt(2022, 1, 1): 1.0,
                   dt(2023, 1, 1): 0.988,
                   dt(2024, 1, 1): 0.975,
                   dt(2025, 1, 1): 0.965,
                   dt(2026, 1, 1): 0.955,
                   dt(2027, 1, 1): 0.9475
               },
               t = [
                   dt(2024, 1, 1), dt(2024, 1, 1), dt(2024, 1, 1), dt(2024, 1, 1),
                   dt(2025, 1, 1),
                   dt(2026, 1, 1),
                   dt(2027, 1, 1), dt(2027, 1, 1), dt(2027, 1, 1), dt(2027, 1, 1),
               ],
           )
           translated_curve = curve.translate(dt(2022, 12, 1))
           fig, ax, line = curve.plot("1d", comparators=[translated_curve], labels=["orig", "translated"], left=dt(2022, 12, 1))
           plt.show()
           plt.close()

        .. ipython:: python

           curve.nodes
           translated_curve.nodes

        When a curve has a log-cubic spline the knot dates can be preserved or
        translated with the ``t`` argument. Preserving the knot dates preserves the
        interpolation of the curve. A knot sequence for a mixed curve which begins
        after ``start`` will not be affected in either case.

        .. ipython:: python

           curve = Curve(
               nodes={
                   dt(2022, 1, 1): 1.0,
                   dt(2022, 2, 1): 0.999,
                   dt(2022, 3, 1): 0.9978,
                   dt(2022, 4, 1): 0.9963,
                   dt(2022, 5, 1): 0.9940
               },
               t = [dt(2022, 1, 1), dt(2022, 1, 1), dt(2022, 1, 1), dt(2022, 1, 1),
                    dt(2022, 2, 1), dt(2022, 3, 1), dt(2022, 4, 1),
                    dt(2022, 5, 1), dt(2022, 5, 1), dt(2022, 5, 1), dt(2022, 5, 1)]
           )
           translated_curve = curve.translate(dt(2022, 1, 15))
           translated_curve2 = curve.translate(dt(2022, 1, 15), t=True)
           curve.plot("1d", left=dt(2022, 1, 15), comparators=[translated_curve, translated_curve2], labels=["orig", "translated", "translated2"])

        .. plot::

           from rateslib.curves import *
           import matplotlib.pyplot as plt
           from datetime import datetime as dt
           curve = Curve(
               nodes={
                   dt(2022, 1, 1): 1.0,
                   dt(2022, 2, 1): 0.999,
                   dt(2022, 3, 1): 0.9978,
                   dt(2022, 4, 1): 0.9963,
                   dt(2022, 5, 1): 0.9940
               },
               t = [dt(2022, 1, 1), dt(2022, 1, 1), dt(2022, 1, 1), dt(2022, 1, 1),
                    dt(2022, 2, 1), dt(2022, 3, 1), dt(2022, 4, 1),
                    dt(2022, 5, 1), dt(2022, 5, 1), dt(2022, 5, 1), dt(2022, 5, 1)]
           )
           translated = curve.translate(dt(2022, 1, 15))
           translated2 = curve.translate(dt(2022, 1, 15), t=True)
           fig, ax, line = curve.plot("1d", left=dt(2022, 1, 15), comparators=[translated, translated2], labels=["orig", "translated", "translated2"])
           plt.show()

        """
        if start <= self.node_dates[0] or self.node_dates[1] < start:
            raise ValueError(
                "Cannot translate exactly for the given `start`, review the docs."
            )

        new_nodes = self._translate_nodes(start)

        # re-organise the t-knot sequence
        # TODO: shift the t knot sequence if the first knot begins at t-0.
        new_t = None if self.t is None else self.t.copy()
        if self.t and start <= self.t[0]:
            pass  # do nothing to t
        elif self.t and self.t[0] < start < self.t[4]:
            if t:
                for i in range(4):
                    new_t[i] = start  # adjust left side of t to start
        elif self.t and self.t[4] <= start:
            raise ValueError(
                "Cannot translate spline knots for given `start`, review the docs."
            )

        new_curve = type(self)(
            nodes=new_nodes,
            interpolation=self.interpolation,
            t=new_t,
            c=None,
            endpoints=self.spline_endpoints,
            modifier=self.modifier,
            calendar=self.calendar,
            convention=self.convention,
            id=None,
            ad=self.ad
        )
        return new_curve

    def _roll_nodes(self, tenor: datetime, days: int):
        """
        Roll nodes by adding days to each one and scaling DF values.

        Parameters
        ----------
        tenor : datetime
            The intended roll datetime
        days : int
            The number of days between ``tenor`` and initial curve node date.

        Returns
        -------
        dict
        """
        on_rate = self.rate(self.node_dates[0], "1d", None)
        d = 1 / 365 if self.convention.upper() != "ACT360" else 1 / 360
        scalar = 1 / ((1 + on_rate * d / 100) ** days)
        new_nodes = {
            k + timedelta(days=days): v * scalar for k, v in self.nodes.items()
        }
        if tenor > self.node_dates[0]:
            new_nodes = {
                self.node_dates[0]: 1.0,
                **new_nodes
            }
        return new_nodes

# Licence: Creative Commons - Attribution-NonCommercial-NoDerivatives 4.0 International
# Commercial use of this code, and/or copying and redistribution is prohibited.
# Contact rateslib at gmail.com if this code is observed outside its intended sphere.

    def roll(self, tenor: Union[datetime, str]):
        """
        Create a new curve with its shape translated in time

        This curve adjustment is a simulation of a future state of the market where
        forward rates are assumed to have moved so that the present day's curve shape
        is reflected in the future (or the past). This is often used in trade
        strategy analysis.

        Parameters
        ----------
        tenor : datetime or str
            The date or tenor by which to roll the curve. If a tenor, as str, will
            derive the datetime as measured from the initial node date. If supplying a
            negative tenor, or a past datetime, there is a limit to how far back the
            curve can be rolled - it will first roll backwards and then attempt to
            :meth:`translate` forward to maintain the initial node date.

        Returns
        -------
        Curve

        Examples
        ---------
        The basic use of this function translates a curve forward in time and the
        plot demonstrates its rates are exactly the same as initially forecast.

        .. ipython:: python

           curve = Curve(
               nodes = {
                   dt(2022, 1, 1): 1.0,
                   dt(2023, 1, 1): 0.988,
                   dt(2024, 1, 1): 0.975,
                   dt(2025, 1, 1): 0.965,
                   dt(2026, 1, 1): 0.955,
                   dt(2027, 1, 1): 0.9475
               },
               t = [
                   dt(2024, 1, 1), dt(2024, 1, 1), dt(2024, 1, 1), dt(2024, 1, 1),
                   dt(2025, 1, 1),
                   dt(2026, 1, 1),
                   dt(2027, 1, 1), dt(2027, 1, 1), dt(2027, 1, 1), dt(2027, 1, 1),
               ],
           )
           rolled_curve = curve.roll("6m")
           rolled_curve2 = curve.roll("-6m")
           curve.plot(
               "1d",
               comparators=[rolled_curve, rolled_curve2],
               labels=["orig", "rolled", "rolled2"],
               right=dt(2026, 7, 1),
           )

        .. plot::

           from rateslib.curves import *
           import matplotlib.pyplot as plt
           from datetime import datetime as dt
           curve = Curve(
               nodes = {
                   dt(2022, 1, 1): 1.0,
                   dt(2023, 1, 1): 0.988,
                   dt(2024, 1, 1): 0.975,
                   dt(2025, 1, 1): 0.965,
                   dt(2026, 1, 1): 0.955,
                   dt(2027, 1, 1): 0.9475
               },
               t = [
                   dt(2024, 1, 1), dt(2024, 1, 1), dt(2024, 1, 1), dt(2024, 1, 1),
                   dt(2025, 1, 1),
                   dt(2026, 1, 1),
                   dt(2027, 1, 1), dt(2027, 1, 1), dt(2027, 1, 1), dt(2027, 1, 1),
               ],
           )
           rolled_curve = curve.roll("6m")
           rolled_curve2 = curve.roll("-6m")
           fig, ax, line = curve.plot("1d", comparators=[rolled_curve, rolled_curve2], labels=["orig", "rolled", "rolled2"], right=dt(2026,6,30))
           plt.show()

        """
        if isinstance(tenor, str):
            tenor = add_tenor(self.node_dates[0], tenor, None, None)

        if tenor == self.node_dates[0]:
            return self.copy()

        days = (tenor - self.node_dates[0]).days
        new_nodes = self._roll_nodes(tenor, days)
        new_t = [_ + timedelta(days=days) for _ in self.t] if self.t else None
        new_curve = type(self)(
            nodes=new_nodes,
            interpolation=self.interpolation,
            t=new_t,
            c=None,
            endpoints=self.spline_endpoints,
            modifier=self.modifier,
            calendar=self.calendar,
            convention=self.convention,
            id=None,
            ad=self.ad
        )
        if tenor > self.node_dates[0]:
            return new_curve
        else:  # tenor < self.node_dates[0]
            return new_curve.translate(self.node_dates[0])


class LineCurve(Curve):
    """
    Object to return values parametrised by values at node dates and interpolation.

    Parameters
    ----------
    nodes : dict[datetime: float]
        Degrees of freedom of the curve denoted by a node date and a corresponding
        value at that point.
    interpolation : str in {"log_linear", "linear"} or callable
        The interpolation used in the non-spline section of the curve. That is the part
        of the curve between the first node in ``nodes`` and the first knot in ``t``.
        If a callable, this allows a user-defined interpolation scheme, and this must
        have the signature ``method(date, nodes)``, where ``date`` is the datetime
        whose DF will be returned and ``nodes`` is as above and is passed to the
        callable.
    t : list[datetime], optional
        The knot locations for the B-spline cubic interpolation section of the
        curve. If *None* all interpolation will be done by the method specified in
        ``interpolation``.
    c : list[float], optional
        The B-spline coefficients used to define the cubic spline. If not given
        will use :meth:`csolve`.
    id : str, optional, set by Default
        The unique identifier to distinguish between curves in a multicurve framework.
    convention : str, optional,
        This parameter is not used. It is included for signature consistency with
        :class:`Curve`.
    ad : int in {0, 1, 2}, optional
        Sets the automatic differentiation order. Defines whether to convert node
        values to float, :class:`Dual` or :class:`Dual2`. It is advised against
        using this setting directly. It is mainly used internally.
    """
    _op_exp = staticmethod(lambda x: x)  # LineCurve spline is not log based so no exponent needed
    _op_log = staticmethod(lambda x: x)  # LineCurve spline is not log based so no log needed
    _ini_solve = 0  # No constraint placed on initial node in Solver

    def __init__(
        self,
        *args,
        **kwargs,
    ):
        super().__init__(*args, **kwargs)

    # def plot(self, *args, **kwargs):
    #     return super().plot(*args, **kwargs)

    def rate(
        self,
        effective: datetime,
        *args,
    ):
        """
        Return the curve value for a given date.

        Note `LineCurve` s do not determine interest rates via DFs therefore do not
        have the concept of tenors or termination dates - the rate is simply the value
        attributed to that date on the curve.

        Parameters
        ----------
        effective : datetime
            The date of the curve value, which will be interpolated if necessary.

        Returns
        -------
        float, Dual, or Dual2
        """
        return self[effective]

    def shift(self, spread: float):
        """
        Raise or lower the curve in parallel by a set number of basis points.

        Parameters
        ----------
        spread : float, Dual, Dual2
            The number of basis points added to the existing curve.

        Returns
        -------
        LineCurve

        Examples
        --------
        .. ipython:: python

           from rateslib.curves import LineCurve

        .. ipython:: python

           line_curve = LineCurve(
               nodes = {
                   dt(2022, 1, 1): 1.7,
                   dt(2023, 1, 1): 1.65,
                   dt(2024, 1, 1): 1.4,
                   dt(2025, 1, 1): 1.3,
                   dt(2026, 1, 1): 1.25,
                   dt(2027, 1, 1): 1.35
               },
               t = [
                   dt(2024, 1, 1), dt(2024, 1, 1), dt(2024, 1, 1), dt(2024, 1, 1),
                   dt(2025, 1, 1),
                   dt(2026, 1, 1),
                   dt(2027, 1, 1), dt(2027, 1, 1), dt(2027, 1, 1), dt(2027, 1, 1),
               ],
           )
           spread_curve = line_curve.shift(25)
           line_curve.plot("1d", comparators=[spread_curve])

        .. plot::

           from rateslib.curves import *
           import matplotlib.pyplot as plt
           from datetime import datetime as dt
           line_curve = LineCurve(
               nodes = {
                   dt(2022, 1, 1): 1.7,
                   dt(2023, 1, 1): 1.65,
                   dt(2024, 1, 1): 1.4,
                   dt(2025, 1, 1): 1.3,
                   dt(2026, 1, 1): 1.25,
                   dt(2027, 1, 1): 1.35
               },
               t = [
                   dt(2024, 1, 1), dt(2024, 1, 1), dt(2024, 1, 1), dt(2024, 1, 1),
                   dt(2025, 1, 1),
                   dt(2026, 1, 1),
                   dt(2027, 1, 1), dt(2027, 1, 1), dt(2027, 1, 1), dt(2027, 1, 1),
               ],
           )
           spread_curve = line_curve.shift(25)
           fig, ax, line = line_curve.plot("1d", comparators=[spread_curve])
           plt.show()

        """
        # TODO test whether need to lower AD order before adding spread
        _ = LineCurve(
            nodes={k: v + spread/100 for k, v in self.nodes.items()},
            interpolation=self.interpolation,
            t=self.t,
            c=None,
            endpoints=self.spline_endpoints,
            id=None,
            convention=self.convention,
            modifier=self.modifier,
            calendar=self.calendar,
            ad=self.ad
        )
        return _

    def _translate_nodes(self, start: datetime):
        new_nodes = self.nodes.copy()
        # re-organise the nodes on the new curve
        del new_nodes[self.node_dates[0]]
        if start == self.node_dates[1]:
            pass
        else:
            new_nodes = {start: self[start], **new_nodes}
        return new_nodes

# Licence: Creative Commons - Attribution-NonCommercial-NoDerivatives 4.0 International
# Commercial use of this code, and/or copying and redistribution is prohibited.
# Contact rateslib at gmail.com if this code is observed outside its intended sphere.

    def translate(self, start: datetime, t: bool = False):
        """
        Create a new curve with an initial node date moved forward keeping all else
        constant.

        This curve adjustment preserves forward curve expectations as time evolves.
        This method is suitable as a way to create a subsequent *opening* curve from a
        previous day's *closing* curve.

        Parameters
        ----------
        start : datetime
            The new initial node date for the curve, must be in the domain:
            (node_date[0], node_date[1]]
        t : bool
            Set to *True* if the initial knots of the knot sequence should be
            translated forward.

        Returns
        -------
        Curve

        Examples
        ---------
        The basic use of this function translates a curve forward in time and the
        plot demonstrates its rates are exactly the same as initially forecast.

        .. ipython:: python

           line_curve = LineCurve(
               nodes = {
                   dt(2022, 1, 1): 1.7,
                   dt(2023, 1, 1): 1.65,
                   dt(2024, 1, 1): 1.4,
                   dt(2025, 1, 1): 1.3,
                   dt(2026, 1, 1): 1.25,
                   dt(2027, 1, 1): 1.35
               },
               t = [
                   dt(2024, 1, 1), dt(2024, 1, 1), dt(2024, 1, 1), dt(2024, 1, 1),
                   dt(2025, 1, 1),
                   dt(2026, 1, 1),
                   dt(2027, 1, 1), dt(2027, 1, 1), dt(2027, 1, 1), dt(2027, 1, 1),
               ],
           )
           translated_curve = line_curve.translate(dt(2022, 12, 1))
           line_curve.plot("1d", comparators=[translated_curve], left=dt(2022, 12, 1))

        .. plot::

           from rateslib.curves import *
           import matplotlib.pyplot as plt
           from datetime import datetime as dt
           line_curve = LineCurve(
               nodes = {
                   dt(2022, 1, 1): 1.7,
                   dt(2023, 1, 1): 1.65,
                   dt(2024, 1, 1): 1.4,
                   dt(2025, 1, 1): 1.3,
                   dt(2026, 1, 1): 1.25,
                   dt(2027, 1, 1): 1.35
               },
               t = [
                   dt(2024, 1, 1), dt(2024, 1, 1), dt(2024, 1, 1), dt(2024, 1, 1),
                   dt(2025, 1, 1),
                   dt(2026, 1, 1),
                   dt(2027, 1, 1), dt(2027, 1, 1), dt(2027, 1, 1), dt(2027, 1, 1),
               ],
           )
           translated_curve = line_curve.translate(dt(2022, 12, 1))
           fig, ax, line = line_curve.plot("1d", comparators=[translated_curve], labels=["orig", "translated"], left=dt(2022, 12,1))
           plt.show()
           plt.close()

        .. ipython:: python

           line_curve.nodes
           translated_curve.nodes

        When a line curve has a cubic spline the knot dates can be preserved or
        translated with the ``t`` argument. Preserving the knot dates preserves the
        interpolation of the curve. A knot sequence for a mixed curve which begins
        after ``start`` will not be affected in either case.

        .. ipython:: python

           curve = LineCurve(
               nodes={
                   dt(2022, 1, 1): 1.5,
                   dt(2022, 2, 1): 1.6,
                   dt(2022, 3, 1): 1.4,
                   dt(2022, 4, 1): 1.2,
                   dt(2022, 5, 1): 1.1,
               },
               t = [dt(2022, 1, 1), dt(2022, 1, 1), dt(2022, 1, 1), dt(2022, 1, 1),
                    dt(2022, 2, 1), dt(2022, 3, 1), dt(2022, 4, 1),
                    dt(2022, 5, 1), dt(2022, 5, 1), dt(2022, 5, 1), dt(2022, 5, 1)]
           )
           translated_curve = curve.translate(dt(2022, 1, 15))
           translated_curve2 = curve.translate(dt(2022, 1, 15), t=True)
           curve.plot("1d", left=dt(2022, 1, 15), comparators=[translated_curve, translated_curve2], labels=["orig", "translated", "translated2"])

        .. plot::

           from rateslib.curves import *
           import matplotlib.pyplot as plt
           from datetime import datetime as dt
           curve = LineCurve(
               nodes={
                   dt(2022, 1, 1): 1.5,
                   dt(2022, 2, 1): 1.6,
                   dt(2022, 3, 1): 1.4,
                   dt(2022, 4, 1): 1.2,
                   dt(2022, 5, 1): 1.1,
               },
               t = [dt(2022, 1, 1), dt(2022, 1, 1), dt(2022, 1, 1), dt(2022, 1, 1),
                    dt(2022, 2, 1), dt(2022, 3, 1), dt(2022, 4, 1),
                    dt(2022, 5, 1), dt(2022, 5, 1), dt(2022, 5, 1), dt(2022, 5, 1)]
           )
           translated = curve.translate(dt(2022, 1, 15))
           translated2 = curve.translate(dt(2022, 1, 15), t=True)
           fig, ax, line = curve.plot("1d", left=dt(2022, 1, 15), comparators=[translated, translated2], labels=["orig", "translated", "translated2"])
           plt.show()

        """
        return super().translate(start, t)

    def _roll_nodes(self, tenor: datetime, days: int):
        new_nodes = {
            k + timedelta(days=days): v for k, v in self.nodes.items()
        }
        if tenor > self.node_dates[0]:
            new_nodes = {
                self.node_dates[0]: self[self.node_dates[0]],
                **new_nodes
            }
        return new_nodes

    def roll(self, tenor: Union[datetime, str]):
        """
        Create a new curve with its shape translated in time

        This curve adjustment is a simulation of a future state of the market where
        forward rates are assumed to have moved so that the present day's curve shape
        is reflected in the future (or the past). This is often used in trade
        strategy analysis.

        Parameters
        ----------
        tenor : datetime or str
            The date or tenor by which to roll the curve. If a tenor, as str, will
            derive the datetime as measured from the initial node date. If supplying a
            negative tenor, or a past datetime, there is a limit to how far back the
            curve can be rolled - it will first roll backwards and then attempt to
            :meth:`translate` forward to maintain the initial node date.

        Returns
        -------
        Curve

        Examples
        ---------
        The basic use of this function translates a curve forward in time and the
        plot demonstrates its rates are exactly the same as initially forecast.

        .. ipython:: python

           line_curve = LineCurve(
               nodes = {
                   dt(2022, 1, 1): 1.7,
                   dt(2023, 1, 1): 1.65,
                   dt(2024, 1, 1): 1.4,
                   dt(2025, 1, 1): 1.3,
                   dt(2026, 1, 1): 1.25,
                   dt(2027, 1, 1): 1.35
               },
               t = [
                   dt(2024, 1, 1), dt(2024, 1, 1), dt(2024, 1, 1), dt(2024, 1, 1),
                   dt(2025, 1, 1),
                   dt(2026, 1, 1),
                   dt(2027, 1, 1), dt(2027, 1, 1), dt(2027, 1, 1), dt(2027, 1, 1),
               ],
           )
           rolled_curve = line_curve.roll("6m")
           rolled_curve2 = line_curve.roll("-6m")
           line_curve.plot(
               "1d",
               comparators=[rolled_curve, rolled_curve2],
               labels=["orig", "rolled", "rolled2"],
               right=dt(2026, 7, 1)
           )

        .. plot::

           from rateslib.curves import *
           import matplotlib.pyplot as plt
           from datetime import datetime as dt
           line_curve = LineCurve(
               nodes = {
                   dt(2022, 1, 1): 1.7,
                   dt(2023, 1, 1): 1.65,
                   dt(2024, 1, 1): 1.4,
                   dt(2025, 1, 1): 1.3,
                   dt(2026, 1, 1): 1.25,
                   dt(2027, 1, 1): 1.35
               },
               t = [
                   dt(2024, 1, 1), dt(2024, 1, 1), dt(2024, 1, 1), dt(2024, 1, 1),
                   dt(2025, 1, 1),
                   dt(2026, 1, 1),
                   dt(2027, 1, 1), dt(2027, 1, 1), dt(2027, 1, 1), dt(2027, 1, 1),
               ],
           )
           rolled_curve = line_curve.roll("6m")
           rolled_curve2 = line_curve.roll("-6m")
           fig, ax, line = line_curve.plot("1d", comparators=[rolled_curve, rolled_curve2], labels=["orig", "rolled", "rolled2"], right=dt(2026,6,30))
           plt.show()
           plt.close()

        """
        return super().roll(tenor)


def interpolate(x, x_1, y_1, x_2, y_2, interpolation, start=None):
    """
    Perform local interpolation between two data points.

    Parameters
    ----------
    x : Any with topology
        The x-value for which the interpolated y-value is sought.
    x_1 : same type as ``x``
        The left bound for the local interpolation.
    y_1 : float, Dual, Dual2
        The value at the left bound.
    x_2 : same type as ``x``
        The right bound for the local interpolation.
    y_2 : float, Dual, Dual2
        The value at the right bound.
    interpolation : str
        The interpolation rule to use in *{"linear", "log_linear", "linear_zero_rate",
        "flat_forward", "flat_backward"}*.
    start : datetime
        Used only if ``interpolation`` is *"linear_zero_rate"* to identify the start
        date of a curve.

    Returns
    -------
    float, Dual, Dual2

    Notes
    -----
    If ``x`` is outside the region ``[x_1, x_2]`` this function will extrapolate
    instead of interpolate using the same mathematical formula.

    Examples
    --------
    .. ipython:: python

       interpolate(50, 0, 10, 100, 50, "linear")
       interpolate(dt(2000, 1, 6), dt(2000, 1, 1), 10, dt(2000, 1, 11), 50, "linear")
    """
    if interpolation == "linear":
        op = lambda z: z
    elif interpolation == "log_linear":
        op, y_1, y_2 = dual_exp, dual_log(y_1), dual_log(y_2)
    elif interpolation == "linear_zero_rate":
        # convention not used here since we just determine linear rate interpolation
        y_2 = dual_log(y_2) / ((start - x_2) / timedelta(days=365))
        if start == x_1:
            y_1 = y_2
        else:
            y_1 = dual_log(y_1) / ((start - x_1) / timedelta(days=365))
        op = lambda z: dual_exp((start - x) / timedelta(days=365) * z)
    elif interpolation == "flat_forward":
        if x == x_2:
            return y_2
        return y_1
    elif interpolation == "flat_backward":
        if x == x_1:
            return y_1
        return y_2
    ret = op(y_1 + (y_2 - y_1) * ((x - x_1) / (x_2 - x_1)))
    return ret


def index_left(
    list_input: list[Any],
    list_length: int,
    value: Any,
    left_count: Optional[int] = 0,
):
    """
    Return the interval index of a value from an ordered input list on the left side.

    Parameters
    ----------
    input : list
        Ordered list (lowest to highest) containing datatypes the same as value.
    length : int
        The length of ``input``.
    value : Any
        The value for which to determine the list index of.
    left_count : int
        The counter to pass recursively to determine the output. Users should not
        directly specify, it is used in internal calculation only.

    Returns
    -------
    int : The left index of the interval within which value is found (or extrapolated
          from)

    Notes
    -----
    Uses a binary search method which operates with time :math:`O(log_2 n)`.

    Examples
    --------
    .. ipython:: python

       from rateslib.curves import index_left

    Out of domain values return the left-side index of the closest matching interval.
    100 is attributed to the interval (1, 2].

    .. ipython:: python

       list = [0, 1, 2]
       index_left(list, 3, 100)

    -100 is attributed to the interval (0, 1].

    .. ipython:: python

       index_left(list, 3, -100)

    Interior values return the left-side index of the interval.
    1.45 is attributed to the interval (1, 2].

    .. ipython:: python

       index_left(list, 3, 1.45)

    1 is attributed to the interval (0, 1].

    .. ipython:: python

       index_left(list, 3, 1)

    """
    if list_length == 1:
        raise ValueError(
            "`index_left` designed for intervals. Cannot index list of length 1."
        )

    if list_length == 2:
        return left_count

    split = floor((list_length-1)/2)
    if list_length == 3 and value == list_input[split]:
        return left_count

    if value <= list_input[split]:
        return index_left(list_input[:split+1], split+1, value, left_count)
    else:
        return index_left(
            list_input[split:], list_length-split, value, left_count + split
        )


# # ALTERNATIVE index_left: exhaustive search which is inferior to binary search
# def index_left_exhaustive(list_input, value, left_count=0):
#     if left_count == 0:
#         if value > list_input[-1]:
#             return len(list_input)-2
#         if value <= list_input[0]:
#             return 0
#
#     if list_input[0] < value <= list_input[1]:
#         return left_count
#     else:
#         return index_left_exhaustive(list_input[1:], value, left_count + 1)


# Licence: Creative Commons - Attribution-NonCommercial-NoDerivatives 4.0 International
# Commercial use of this code, and/or copying and redistribution is prohibited.
# Contact rateslib at gmail.com if this code is observed outside its intended sphere.