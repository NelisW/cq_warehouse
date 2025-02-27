"""

Parametric Threaded Fasteners

name: fastener.py
by:   Gumyr
date: August 14th 2021

desc: This python/cadquery code is a parameterized threaded fastener generator.

todo: - add helix line to thread object if simple enabled
      - support unthreaded sections on screw shanks
      - calculate depth for thru threaded holes
      - optimize recess creation when recess_taper = 0

license:

    Copyright 2021 Gumyr

    Licensed under the Apache License, Version 2.0 (the "License");
    you may not use this file except in compliance with the License.
    You may obtain a copy of the License at

        http://www.apache.org/licenses/LICENSE-2.0

    Unless required by applicable law or agreed to in writing, software
    distributed under the License is distributed on an "AS IS" BASIS,
    WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
    See the License for the specific language governing permissions and
    limitations under the License.

"""
from abc import ABC, abstractmethod
from typing import Literal, Tuple, Optional, List, TypeVar, Union
from math import sin, cos, tan, radians, pi, degrees, sqrt
import csv
import importlib.resources as pkg_resources
import cadquery as cq
from cq_warehouse.thread import is_safe, imperial_str_to_float, IsoThread
import cq_warehouse

# from functools import cache

MM = 1
IN = 25.4 * MM

# ISO standards use single variable dimension labels which are used extensively
# pylint: disable=invalid-name

# lambdas are only used in Workplane methods which cycle over multiple locations
# and are required
# pylint: disable=unnecessary-lambda


def polygon_diagonal(width: float, num_sides: Optional[int] = 6) -> float:
    """Distance across polygon diagonals given width across flats"""
    return width / cos(pi / num_sides)


def read_fastener_parameters_from_csv(filename: str) -> dict:
    """Parse a csv parameter file into a dictionary of strings"""

    parameters = {}
    with pkg_resources.open_text(cq_warehouse, filename) as csvfile:
        reader = csv.DictReader(csvfile)
        fieldnames = reader.fieldnames
        for row in reader:
            key = row[fieldnames[0]]
            row.pop(fieldnames[0])
            parameters[key] = row

    return parameters


def decode_imperial_size(size: str) -> Tuple[float, float]:
    """Extract the major diameter and pitch from an imperial size"""

    # Imperial # sizes to diameters
    imperial_numbered_sizes = {
        "#0000": 0.0210 * IN,
        "#000": 0.0340 * IN,
        "#00": 0.0470 * IN,
        "#0": 0.0600 * IN,
        "#1": 0.0730 * IN,
        "#2": 0.0860 * IN,
        "#3": 0.0990 * IN,
        "#4": 0.1120 * IN,
        "#5": 0.1250 * IN,
        "#6": 0.1380 * IN,
        "#8": 0.1640 * IN,
        "#10": 0.1900 * IN,
        "#12": 0.2160 * IN,
    }

    sizes = size.split("-")
    if size[0] == "#":
        major_diameter = imperial_numbered_sizes[sizes[0]]
    else:
        major_diameter = imperial_str_to_float(sizes[0])
    pitch = IN / (imperial_str_to_float(sizes[1]) / IN)
    return (major_diameter, pitch)


def metric_str_to_float(measure: str) -> float:
    """Convert a metric measurement to a float value"""

    if is_safe(measure):
        # pylint: disable=eval-used
        # Before eval() is called the string, extracted from the csv file, is verified as safe
        result = eval(measure)
    else:
        result = measure
    return result


def evaluate_parameter_dict_of_dict(
    parameters: dict,
    is_metric: Optional[bool] = True,
) -> dict:
    """Convert string values in a dict of dict structure to floats based on provided units"""

    measurements = {}
    for key, value in parameters.items():
        measurements[key] = evaluate_parameter_dict(
            parameters=value, is_metric=is_metric
        )

    return measurements


def evaluate_parameter_dict(
    parameters: dict,
    is_metric: Optional[bool] = True,
) -> dict:
    """Convert the strings in a parameter dictionary into dimensions"""
    measurements = {}
    for params, value in parameters.items():
        if is_metric:
            measurements[params] = metric_str_to_float(value)
        else:
            measurements[params] = imperial_str_to_float(value)
    return measurements


def isolate_fastener_type(target_fastener: str, fastener_data: dict) -> dict:
    """Split the fastener data 'type:value' strings into dictionary elements"""
    result = {}
    for size, parameters in fastener_data.items():
        dimension_dict = {}
        for type_dimension, value in parameters.items():
            (fastener_name, dimension) = tuple(type_dimension.strip().split(":"))
            if target_fastener == fastener_name and not value == "":
                dimension_dict[dimension] = value
        if len(dimension_dict) > 0:
            result[size] = dimension_dict
    return result


def lookup_drill_diameters(drill_hole_sizes: dict) -> dict:
    """Return a dict of dict of drill size to drill diameter"""

    # Read the drill size csv file and build a drill_size dictionary (Ah, the imperial system)
    drill_sizes = {}
    with pkg_resources.open_text(cq_warehouse, "drill_sizes.csv") as csvfile:
        reader = csv.DictReader(csvfile)
        fieldnames = reader.fieldnames
        for row in reader:
            drill_sizes[row[fieldnames[0]]] = float(row[fieldnames[1]]) * IN

    #  Build a dictionary of hole diameters for these hole sizes
    drill_hole_diameters = {}
    for size, drill_data in drill_hole_sizes.items():
        hole_data = {}
        for fit, drill in drill_data.items():
            try:
                hole_data[fit] = drill_sizes[drill]
            except KeyError:
                if size[0] == "M":
                    hole_data[fit] = float(drill)
                else:
                    hole_data[fit] = imperial_str_to_float(drill)
        drill_hole_diameters[size] = hole_data
    return drill_hole_diameters


def lookup_nominal_screw_lengths() -> dict:
    """Return a dict of dict of drill size to drill diameter"""

    # Read the nominal screw length csv file and build a dictionary
    nominal_screw_lengths = {}
    with pkg_resources.open_text(cq_warehouse, "nominal_screw_lengths.csv") as csvfile:
        reader = csv.DictReader(csvfile)
        for row in reader:
            unit_factor = MM if row["Unit"] == "mm" else IN
            sizes = [
                unit_factor * float(size)
                for size in str(row["Nominal_Sizes"]).split(",")
            ]
            nominal_screw_lengths[row["Screw_Type"]] = sizes

    return nominal_screw_lengths


def _fillet2D(self, radius: float, vertices: List[cq.Vertex]) -> cq.Wire:
    return cq.Workplane(self.val().fillet2D(radius, vertices))


cq.Workplane.fillet2D = _fillet2D


def cross_recess(size: str) -> Tuple[cq.Workplane, float]:
    """Type H Cross / Phillips recess for screws

    size must be one of: PH0, PH1, PH2, PH3, or PH4

    Note: the following dimensions are somewhat simplified to a single
    value per drive size instead of unique sizes for each fastener
    """
    widths = {"PH0": 1.9, "PH1": 3.1, "PH2": 5.3, "PH3": 6.8, "PH4": 10.0}
    depths = {"PH0": 1.1, "PH1": 2.0, "PH2": 3.27, "PH3": 3.53, "PH4": 5.88}
    try:
        m = widths[size]
    except KeyError as e:
        raise ValueError(f"{size} is an invalid cross size {widths}") from e
    recess = (
        cq.Workplane("XY")
        .moveTo(m / 2, 0)
        .vLineTo(m / 12)
        .hLineTo(m / 12)
        .vLineTo(m / 2)
        .hLineTo(0)
        .mirrorX()
        .mirrorY()
    )
    vertices = recess.vertices(
        cq.selectors.BoxSelector((-m / 3, -m / 3, -m / 3), (m / 3, m / 3, m / 3))
    ).vals()

    return (recess.fillet2D(m / 3, vertices), depths[size])


def hex_recess(size: float) -> cq.Workplane:
    """Hexagon recess for screws

    size refers to the size across the flats
    """
    return cq.Workplane("XY").polygon(6, polygon_diagonal(size))


def hexalobular_recess(size: str) -> Tuple[cq.Workplane, float]:
    """Plan of Hexalobular recess for screws

    size must be one of: T6, T8, T10, T15, T20, T25, T30, T40, T45, T50, T55, T60,
                         T70, T80, T90, T100

    depth approximately 60% of maximum diameter
    """
    try:
        screw_data = evaluate_parameter_dict_of_dict(
            read_fastener_parameters_from_csv("iso10664def.csv")
        )[size]
    except KeyError as e:
        raise ValueError(f"{size} is an invalid hexalobular size") from e

    (A, B, Re) = (screw_data[p] for p in ["A", "B", "Re"])

    # Given the outer (A) and inner (B) diameters and the external radius (Re),
    # calculate the internal radius
    sqrt_3 = sqrt(3)
    Ri = (A ** 2 - sqrt_3 * A * B - 4 * A * Re + B ** 2 + 2 * sqrt_3 * B * Re) / (
        2 * (sqrt_3 * A - 2 * B - 2 * sqrt_3 * Re + 4 * Re)
    )

    center_external_arc = [
        cq.Vector(0, A / 2 - Re),
        cq.Vector(sqrt_3 * (A / 2 - Re) / 2, A / 4 - Re / 2),
    ]
    center_internal_arc = cq.Vector(B / 4 + Ri / 2, sqrt_3 * (B / 2 + Ri) / 2)

    # Determine where the two arcs are tangent (i.e. touching)
    tangent_points = [
        center_external_arc[0]
        + (center_internal_arc - center_external_arc[0]).normalized() * Re,
        center_external_arc[1]
        + (center_internal_arc - center_external_arc[1]).normalized() * Re,
    ]

    # Create one sixth of the complete repeating wire
    one_sixth_plan = (
        cq.Workplane("XY")
        .moveTo(0, A / 2)
        .radiusArc(tangent_points[0], Re)
        .radiusArc(tangent_points[1], -Ri)
        .radiusArc(cq.Vector(sqrt_3 * A / 4, A / 4), Re)
        .consolidateWires()
        .val()
    )
    # Create all six of the wires in clockwise direction
    plan_wires = [
        one_sixth_plan.rotate((0, 0, 0), (0, 0, 1), a) for a in range(0, -360, -60)
    ]
    return (cq.Workplane(cq.Wire.assembleEdges(plan_wires)), 0.6 * A)
    # return plan


def slot_recess(width: float, length: float) -> cq.Workplane:
    """Slot recess for screws"""
    return cq.Workplane("XY").rect(width, length)


def square_recess(size: str) -> Tuple[cq.Workplane, float]:
    """Robertson Square recess for screws

    size must be one of: R00, R0, R1, R2, and R3

    Note: Robertson sizes are also color coded: Orange, Yellow, Green, Red, Black

    """
    widths = {"R00": 1.80, "R0": 2.31, "R1": 2.86, "R2": 3.38, "R3": 4.85}
    depths = {"R00": 1.85, "R0": 2.87, "R1": 3.56, "R2": 4.19, "R3": 5.11}

    try:
        m = widths[size]
    except KeyError as e:
        raise ValueError(f"{size} is an invalid square size {widths}") from e
    return (cq.Workplane("XY").rect(m, m), depths[size])


def select_by_size_fn(cls, size: str) -> dict:
    """Given a fastener size, return a dictionary of {class:[type,...]}"""
    type_dict = {}
    for fastener_class in cls.__subclasses__():
        for fastener_type in fastener_class.types():
            if size in fastener_class.sizes(fastener_type):
                if fastener_class in type_dict.keys():
                    type_dict[fastener_class].append(fastener_type)
                else:
                    type_dict[fastener_class] = [fastener_type]

    return type_dict


class Nut(ABC):
    """Base Class used to create standard threaded nuts"""

    # Read clearance and tap hole dimesions tables
    # Close, Medium, Loose
    clearance_hole_drill_sizes = read_fastener_parameters_from_csv(
        "clearance_hole_sizes.csv"
    )
    clearance_hole_data = lookup_drill_diameters(clearance_hole_drill_sizes)

    # Soft (Aluminum, Brass, & Plastics) or Hard (Steel, Stainless, & Iron)
    tap_hole_drill_sizes = read_fastener_parameters_from_csv("tap_hole_sizes.csv")
    tap_hole_data = lookup_drill_diameters(tap_hole_drill_sizes)

    @property
    def tap_drill_sizes(self):
        """A dictionary of drill sizes for tapped holes"""
        try:
            return self.tap_hole_drill_sizes[self.size]
        except KeyError as e:
            raise ValueError(f"No tap hole data for size {self.size}") from e

    @property
    def tap_hole_diameters(self):
        """A dictionary of drill diameters for tapped holes"""
        try:
            return self.tap_hole_data[self.size]
        except KeyError as e:
            raise ValueError(f"No tap hole data for size {self.size}") from e

    @property
    def clearance_drill_sizes(self):
        """A dictionary of drill sizes for clearance holes"""
        try:
            return self.clearance_hole_drill_sizes[self.size.split("-")[0]]
        except KeyError as e:
            raise ValueError(f"No clearance hole data for size {self.size}") from e

    @property
    def clearance_hole_diameters(self):
        """A dictionary of drill diameters for clearance holes"""
        try:
            return self.clearance_hole_data[self.size.split("-")[0]]
        except KeyError as e:
            raise ValueError(f"No clearance hole data for size {self.size}") from e

    @classmethod
    def select_by_size(cls, size: str) -> dict:
        """Return a dictionary of list of fastener types of this size"""
        return select_by_size_fn(cls, size)

    @property
    @classmethod
    @abstractmethod
    def fastener_data(cls):
        """Each derived class must provide a fastener_data dictionary"""
        return NotImplementedError

    @abstractmethod
    def nut_profile(self) -> cq.Workplane:
        """Each derived class must provide the profile of the nut"""
        return NotImplementedError

    @abstractmethod
    def nut_plan(self) -> cq.Workplane:
        """Each derived class must provide the plan of the nut"""
        return NotImplementedError

    @abstractmethod
    def countersink_profile(
        self, fit: Literal["Close", "Normal", "Loose"]
    ) -> cq.Workplane:
        """Each derived class must provide the profile of a countersink cutter"""
        return NotImplementedError

    @property
    def info(self):
        """Return identifying information"""
        return f"{self.nut_class}({self.fastener_type}): {self.size}"

    @property
    def nut_class(self):
        """Which derived class created this nut"""
        return type(self).__name__

    @classmethod
    def types(cls) -> List[str]:
        """Return a set of the nut types"""
        return set(p.split(":")[0] for p in list(cls.fastener_data.values())[0].keys())

    @classmethod
    def sizes(cls, fastener_type: str) -> List[str]:
        """Return a list of the nut sizes for the given type"""
        return list(isolate_fastener_type(fastener_type, cls.fastener_data).keys())

    @property
    def nut_thickness(self):
        """Calculate the maximum thickness of the nut"""
        return cq.Workplane(self.cq_object).vertices(">Z").val().Z

    @property
    def nut_diameter(self):
        """Calculate the maximum diameter of the nut"""
        vertices = cq.Workplane(self.cq_object).vertices().vals()
        radii = [
            (cq.Vector(0, 0, v.Z) - cq.Vector(v.toTuple())).Length for v in vertices
        ]
        if len(radii) == 0:
            raise Exception(f"Invalid nut: {type(self).__name__},{self.__dict__}")
        return 2 * max(radii)

    @property
    def cq_object(self):
        """A cadquery Compound screw as defined by class attributes"""
        return self._cq_object

    def length_offset(self):
        """Screw only parameter"""
        return 0

    # @cache
    def __init__(
        self,
        size: str,
        fastener_type: str,
        hand: Literal["right", "left"] = "right",
        simple: bool = True,
    ):
        """Parse Nut input parameters"""
        size_parts = size.strip().split("-")
        if not len(size_parts) == 2:
            raise ValueError(
                f"{size_parts} invalid, must be formatted as size-pitch or size-TPI"
            )

        self.size = size
        self.is_metric = self.size[0] == "M"
        if self.is_metric:
            self.thread_diameter = float(size_parts[0][1:])
            self.thread_pitch = float(size_parts[1])
        else:
            (self.thread_diameter, self.thread_pitch) = decode_imperial_size(self.size)

        if fastener_type not in self.types():
            raise ValueError(f"{fastener_type} invalid, must be one of {self.types()}")
        self.fastener_type = fastener_type
        if hand in ["left", "right"]:
            self.hand = hand
        else:
            raise ValueError(f"{hand} invalid, must be one of 'left' or 'right'")
        self.simple = simple
        self.socket_clearance = 6 * MM  # Used as extra clearance when countersinking
        try:
            self.nut_data = evaluate_parameter_dict(
                isolate_fastener_type(self.fastener_type, self.fastener_data)[
                    self.size
                ],
                is_metric=self.is_metric,
            )
        except KeyError as e:
            raise ValueError(
                f"{size} invalid, must be one of {self.sizes(self.fastener_type)}"
            ) from e
        self._cq_object = self.make_nut().val()

    def make_nut(self) -> cq.Workplane:
        """Create a screw head from the 2D shapes defined in the derived class"""

        def method_exists(method: str) -> bool:
            """Did the derived class create this method"""
            return hasattr(self.__class__, method) and callable(
                getattr(self.__class__, method)
            )

        # pylint: disable=no-member
        profile = self.nut_profile()
        max_nut_height = profile.vertices(">Z").val().Z
        nut_thread_height = self.nut_data["m"]

        # Create the basic nut shape
        nut = profile.toPending().revolve()

        # Modify the head to conform to the shape of head_plan (e.g. hex)
        # Note that some nuts (e.g. domed nuts) extend beyond the threaded section
        nut_blank = (
            cq.Workplane("XY")
            .add(self.nut_plan().val())
            .toPending()
            .extrude(max_nut_height)
            .faces("<Z")
            .workplane()
            .hole(self.thread_diameter, nut_thread_height)
        )
        nut = nut.intersect(nut_blank)

        # Add a flange as it exists outside of the head plan
        if method_exists("flange_profile"):
            flange = (
                cq.Workplane("XZ")
                .add(self.flange_profile().val())
                .toPending()
                .revolve()
            )
            flange = (
                cq.Workplane("XY")
                .add(flange)
                .toPending()
                .faces(">Z")
                .hole(self.thread_diameter)
            )
            nut = nut.union(flange)

        # Add the thread to the nut body
        if not self.simple:
            # Create the thread
            thread = IsoThread(
                major_diameter=self.thread_diameter,
                pitch=self.thread_pitch,
                length=self.nut_data["m"],
                external=False,
                end_finishes=("fade", "fade"),
                hand=self.hand,
            )
            nut = nut.union(thread.cq_object)

        return nut

    def default_nut_profile(self):
        """Create 2D profile of hex nuts with double chamfers"""
        (m, s) = (self.nut_data[p] for p in ["m", "s"])
        e = polygon_diagonal(s, 6)
        # Chamfer angle must be between 15 and 30 degrees
        cs = (e - s) * tan(radians(15)) / 2

        # Note that when intersecting a revolved shape with a extruded polygon the OCCT
        # core may fail unless the polygon is slightly larger than the circle so
        # all profiles must be reduced by a small fudge factor
        profile = (
            cq.Workplane("XZ")
            .hLineTo(s / 2)
            .lineTo(e / 2 - 0.001, cs)
            .vLineTo(m - cs)
            .lineTo(s / 2, m)
            .hLineTo(0)
            .close()
        )
        return profile
        # return cq.Workplane("XZ").rect(e, m, centered=False)

    def default_nut_plan(self) -> cq.Workplane:
        """Create a hexagon solid"""
        return cq.Workplane("XY").polygon(6, polygon_diagonal(self.nut_data["s"]))
        # return cq.Workplane("XY").circle(self.nut_data["s"] / 2)

    def default_countersink_profile(self, fit) -> cq.Workplane:
        """A simple rectangle with gets revolved into a cylinder with an
        extra socket_clearance (defaults to 6mm across the diameter) for a socket wrench"""
        # Note that fit is only used for some flanged nuts but is here for uniformity
        del fit
        (m, s) = (self.nut_data[p] for p in ["m", "s"])
        width = polygon_diagonal(s, 6) + self.socket_clearance
        return cq.Workplane("XZ").rect(width / 2, m, centered=False)


class DomedCapNut(Nut):
    """
    size: str
    fastener_type: str
        din1587 Hexagon domed cap nuts
    hand: Literal["right", "left"] = "right"
    simple: bool = True
    """

    fastener_data = read_fastener_parameters_from_csv("domed_cap_nut_parameters.csv")

    def nut_profile(self):
        """Create 2D profile of hex nuts with double chamfers"""
        (dk, m, s) = (self.nut_data[p] for p in ["dk", "m", "s"])
        e = polygon_diagonal(s, 6)
        # Chamfer angle must be between 15 and 30 degrees
        cs = (e - s) * tan(radians(15)) / 2
        profile = (
            cq.Workplane("XZ")
            .moveTo(1 * MM, 0)
            .hLineTo(s / 2)
            .lineTo(e / 2, cs)
            .vLineTo(m - cs)
            .lineTo(s / 2, m)
            .hLineTo(dk / 2)
            .radiusArc((0, m + dk / 2), -dk / 2)
            .vLineTo(m + dk / 2 - 1 * MM)
            .radiusArc((dk / 2 - 1 * MM, m), (dk / 2 - 1 * MM))
            .hLineTo(1 * MM)
            .close()
        )
        return profile

    def countersink_profile(self, fit) -> cq.Workplane:
        """A simple rectangle with gets revolved into a cylinder with an
        extra socket_clearance (defaults to 6mm across the diameter) for a socket wrench"""
        # Note that fit is only used for some flanged nuts but is here for uniformity
        del fit
        (dk, m, s) = (self.nut_data[p] for p in ["dk", "m", "s"])
        width = polygon_diagonal(s, 6) + self.socket_clearance
        return cq.Workplane("XZ").rect(width / 2, m + dk / 2, centered=False)

    nut_plan = Nut.default_nut_plan


class HexNut(Nut):
    """
    size: str
    fastener_type: str
        iso4032	Hexagon nuts, Style 1
        iso4033	Hexagon nuts, Style 2
        iso4035	Hexagon thin nuts, chamfered
    hand: Literal["right", "left"] = "right"
    simple: bool = True
    """

    fastener_data = read_fastener_parameters_from_csv("hex_nut_parameters.csv")

    nut_profile = Nut.default_nut_profile
    nut_plan = Nut.default_nut_plan
    countersink_profile = Nut.default_countersink_profile


class HexNutWithFlange(Nut):
    """
    size: str
    fastener_type: str
        din1665 Hexagon nuts with flange
    hand: Literal["right", "left"] = "right"
    simple: bool = True
    """

    fastener_data = read_fastener_parameters_from_csv(
        "hex_nut_with_flange_parameters.csv"
    )

    nut_profile = Nut.default_nut_profile
    nut_plan = Nut.default_nut_plan

    def flange_profile(self):
        """Flange for hexagon Bolts"""
        (dc, c) = (self.nut_data[p] for p in ["dc", "c"])
        flange_angle = 25
        tangent_point = (
            cq.Vector(
                (c / 2) * cos(radians(90 - flange_angle)),
                (c / 2) * sin(radians(90 - flange_angle)),
            )
            + cq.Vector((dc - c) / 2, c / 2)
        )
        profile = (
            cq.Workplane("XZ")
            .hLineTo(dc / 2 - c / 2)
            .radiusArc(tangent_point, -c / 2)
            .polarLine(dc / 2 - c / 2, 180 - flange_angle)
            .hLineTo(0)
            .close()
        )
        return profile

    def countersink_profile(
        self, fit: Literal["Close", "Normal", "Loose"]
    ) -> cq.Workplane:
        """A simple rectangle with gets revolved into a cylinder with
        at least socket_clearance (default 6mm across the diameter) for a socket wrench"""
        try:
            clearance_hole_diameter = self.clearance_hole_diameters[fit]
        except KeyError as e:
            raise ValueError(
                f"{fit} invalid, must be one of {list(self.clearance_hole_diameters.keys())}"
            ) from e
        (dc, s, m) = (self.nut_data[p] for p in ["dc", "s", "m"])
        clearance = clearance_hole_diameter - self.thread_diameter
        width = max(dc + clearance, polygon_diagonal(s, 6) + self.socket_clearance)
        return cq.Workplane("XZ").rect(width / 2, m, centered=False)

    nut_plan = Nut.default_nut_plan


class UnchamferedHexagonNut(Nut):
    """
    size: str
    fastener_type: str
        iso4036 Hexagon thin nuts, unchamfered
    hand: Literal["right", "left"] = "right"
    simple: bool = True
    """

    fastener_data = read_fastener_parameters_from_csv(
        "unchamfered_hex_nut_parameters.csv"
    )

    def nut_profile(self):
        """Create 2D profile of hex nuts with double chamfers"""
        (m, s) = (self.nut_data[p] for p in ["m", "s"])
        return cq.Workplane("XZ").rect(
            polygon_diagonal(s, 6) / 2 - 0.001, m, centered=False
        )

    nut_plan = Nut.default_nut_plan
    countersink_profile = Nut.default_countersink_profile


class SquareNut(Nut):
    """
    size: str
    fastener_type: str
        din557 - Square Nuts
    hand: Literal["right", "left"] = "right"
    simple: bool = True
    """

    fastener_data = read_fastener_parameters_from_csv("square_nut_parameters.csv")

    def nut_profile(self):
        """Create 2D profile of hex nuts with double chamfers"""
        (m, s) = (self.nut_data[p] for p in ["m", "s"])
        e = polygon_diagonal(s, 4)
        # Chamfer angle must be between 15 and 30 degrees
        cs = (e - s) * tan(radians(15)) / 2
        profile = (
            cq.Workplane("XZ")
            .hLineTo(e / 2 - 0.001)
            .vLineTo(m - cs)
            .lineTo(s / 2, m)
            .hLineTo(0)
            .close()
        )
        return profile

    def nut_plan(self) -> cq.Workplane:
        """Simple square for the plan"""
        return cq.Workplane("XY").rect(self.nut_data["s"], self.nut_data["s"])

    def countersink_profile(self, fit) -> cq.Workplane:
        """A simple rectangle with gets revolved into a cylinder with an
        extra socket_clearance (defaults to 6mm across the diameter) for a socket wrench"""
        # Note that fit is only used for some flanged nuts but is here for uniformity
        del fit
        (m, s) = (self.nut_data[p] for p in ["m", "s"])
        width = polygon_diagonal(s, 4) + self.socket_clearance
        return cq.Workplane("XZ").rect(width / 2, m, centered=False)


class Screw(ABC):
    """Base class for a set of threaded screws or bolts"""

    # Read clearance and tap hole dimesions tables
    # Close, Medium, Loose
    clearance_hole_drill_sizes = read_fastener_parameters_from_csv(
        "clearance_hole_sizes.csv"
    )
    clearance_hole_data = lookup_drill_diameters(clearance_hole_drill_sizes)

    # Soft (Aluminum, Brass, & Plastics) or Hard (Steel, Stainless, & Iron)
    tap_hole_drill_sizes = read_fastener_parameters_from_csv("tap_hole_sizes.csv")
    tap_hole_data = lookup_drill_diameters(tap_hole_drill_sizes)

    # Build a dictionary of nominal screw lengths keyed by screw type
    nominal_length_range = lookup_nominal_screw_lengths()

    @property
    def tap_drill_sizes(self):
        """A dictionary of drill sizes for tapped holes"""
        try:
            return self.tap_hole_drill_sizes[self.size]
        except KeyError as e:
            raise ValueError(f"No tap hole data for size {self.size}") from e

    @property
    def tap_hole_diameters(self):
        """A dictionary of drill diameters for tapped holes"""
        try:
            return self.tap_hole_data[self.size]
        except KeyError as e:
            raise ValueError(f"No tap hole data for size {self.size}") from e

    @property
    def clearance_drill_sizes(self):
        """A dictionary of drill sizes for clearance holes"""
        try:
            return self.clearance_hole_drill_sizes[self.size.split("-")[0]]
        except KeyError as e:
            raise ValueError(f"No clearance hole data for size {self.size}") from e

    @property
    def clearance_hole_diameters(self):
        """A dictionary of drill diameters for clearance holes"""
        try:
            return self.clearance_hole_data[self.size.split("-")[0]]
        except KeyError as e:
            raise ValueError(f"No clearance hole data for size {self.size}") from e

    @property
    @classmethod
    @abstractmethod
    def fastener_data(cls):
        """Each derived class must provide a fastener_data dictionary"""
        return NotImplementedError

    @abstractmethod
    def countersink_profile(
        self, fit: Literal["Close", "Normal", "Loose"]
    ) -> cq.Workplane:
        """Each derived class must provide the profile of a countersink cutter"""
        return NotImplementedError

    @classmethod
    def select_by_size(cls, size: str) -> dict:
        """Return a dictionary of list of fastener types of this size"""
        return select_by_size_fn(cls, size)

    @classmethod
    def types(cls) -> List[str]:
        """Return a set of the screw types"""
        return set(p.split(":")[0] for p in list(cls.fastener_data.values())[0].keys())

    @classmethod
    def sizes(cls, fastener_type: str) -> List[str]:
        """Return a list of the screw sizes for the given type"""
        return list(isolate_fastener_type(fastener_type, cls.fastener_data).keys())

    def length_offset(self):
        """
        To enable screws to include the head height in their length (e.g. Countersunk),
        allow each derived class to override this length_offset calculation to the
        appropriate head height.
        """
        return 0

    def min_hole_depth(self, counter_sunk: bool = True) -> float:
        """Minimum depth of a hole able to accept the screw"""
        countersink_profile = self.countersink_profile("Loose")
        head_offset = countersink_profile.vertices(">Z").val().Z
        if counter_sunk:
            result = self.length + head_offset - self.length_offset()
        else:
            result = self.length - self.length_offset()
        return result

    @property
    def nominal_lengths(self) -> List[float]:
        """A list of nominal screw lengths for this screw"""
        try:
            range_min = self.screw_data["short"]
        except KeyError:
            range_min = None
        try:
            range_max = self.screw_data["long"]
        except KeyError:
            range_max = None
        if (
            range_min is None
            or range_max is None
            or not self.fastener_type in Screw.nominal_length_range.keys()
        ):
            result = None
        else:
            result = [
                size
                for size in Screw.nominal_length_range[self.fastener_type]
                if range_min <= size <= range_max
            ]
        return result

    @property
    def info(self):
        """Return identifying information"""
        return f"{self.screw_class}({self.fastener_type}): {self.size}x{self.length}{' left hand thread' if self.hand=='left' else ''}"

    @property
    def screw_class(self):
        """Which derived class created this screw"""
        return type(self).__name__

    @property
    def head_height(self):
        """Calculate the maximum height of the head"""
        if self.head is None:
            result = 0
        else:
            result = self.head.vertices(">Z").val().Z - self.head.vertices("<Z").val().Z
        return result

    @property
    def head_diameter(self):
        """Calculate the maximum diameter of the head"""
        if self.head is None:
            result = 0
        else:
            vertices = self.head.vertices().vals()
            radii = [
                (cq.Vector(0, 0, v.Z) - cq.Vector(v.toTuple())).Length for v in vertices
            ]
            result = 2 * max(radii)
        return result

    @property
    def head(self):
        """A cadquery Solid thread as defined by class attributes"""
        return self._head

    @property
    def cq_object(self):
        """A cadquery Compound screw as defined by class attributes"""
        return self._cq_object

    # @cache
    def __init__(
        self,
        size: str,
        length: float,
        fastener_type: str,
        hand: Optional[Literal["right", "left"]] = "right",
        simple: Optional[bool] = True,
        socket_clearance: Optional[float] = 6 * MM,
    ):
        """Parse Screw input parameters"""
        size_parts = size.strip().split("-")
        if not len(size_parts) == 2:
            raise ValueError(
                f"{size_parts} invalid, must be formatted as size-pitch or size-TPI"
            )

        self.size = size
        self.is_metric = self.size[0] == "M"
        if self.is_metric:
            self.thread_diameter = float(size_parts[0][1:])
            self.thread_pitch = float(size_parts[1])
        else:
            (self.thread_diameter, self.thread_pitch) = decode_imperial_size(self.size)

        self.length = length
        if fastener_type not in self.types():
            raise ValueError(f"{fastener_type} invalid, must be one of {self.types()}")
        self.fastener_type = fastener_type
        if hand in ["left", "right"]:
            self.hand = hand
        else:
            raise ValueError(f"{hand} invalid, must be one of 'left' or 'right'")
        self.simple = simple
        try:
            self.screw_data = evaluate_parameter_dict(
                isolate_fastener_type(self.fastener_type, self.fastener_data)[
                    self.size
                ],
                is_metric=self.is_metric,
            )
        except KeyError as e:
            raise ValueError(
                f"{size} invalid, must be one of {self.sizes(self.fastener_type)}"
            ) from e
        self.socket_clearance = socket_clearance  # Only used for hex head screws

        length_offset = self.length_offset()
        if length_offset >= self.length:
            raise ValueError(
                f"Screw length {self.length} is <= countersunk screw head {length_offset}"
            )
        self.max_thread_length = self.length - length_offset
        self.thread_length = length - length_offset
        head = self.make_head()
        if head is None:  # A fully custom screw
            self._head = None
            self._shank = None
            self._cq_object = None
        else:
            self._head = head.translate((0, 0, -self.length_offset()))
            thread = IsoThread(
                major_diameter=self.thread_diameter,
                pitch=self.thread_pitch,
                length=self.thread_length,
                external=True,
                hand=self.hand,
                end_finishes=("fade", "raw"),
            )

            self.shank = (
                cq.Workplane("XY")
                .circle(thread.min_radius)
                .extrude(self.thread_length)
                .val()
            )
            if not self.simple:
                self.shank = self.shank.fuse(thread.cq_object)
            self._cq_object = self._head.union(
                self.shank.translate(cq.Vector(0, 0, -self.length))
            ).val()

    def make_head(self) -> cq.Workplane:
        """Create a screw head from the 2D shapes defined in the derived class"""

        def method_exists(method: str) -> bool:
            """Did the derived class create this method"""
            return hasattr(self.__class__, method) and callable(
                getattr(self.__class__, method)
            )

        # Determine what shape creation methods have been defined
        has_profile = method_exists("head_profile")
        has_plan = method_exists("head_plan")
        has_recess = method_exists("head_recess")
        has_flange = method_exists("flange_profile")
        if has_profile:
            # pylint: disable=no-member
            profile = self.head_profile()
            max_head_height = profile.vertices(">Z").val().Z
            max_head_radius = profile.vertices(">X").val().X

            # Create the basic head shape
            head = cq.Workplane("XZ").add(profile.val()).toPending().revolve()
        if has_plan:
            # pylint: disable=no-member
            head_plan = self.head_plan()
        else:
            # Ensure this default plan is outside of the maximum profile dimension.
            # As the slot cuts across the entire head it must go outside of the top
            # face. By creating an overly large head plan the slot can be safely
            # contained within and it doesn't clip the revolved profile.
            # head_plan = cq.Workplane("XY").circle(max_head_radius)
            head_plan = cq.Workplane("XY").rect(
                3 * max_head_radius, 3 * max_head_radius
            )

        # Potentially modify the head to conform to the shape of head_plan
        # (e.g. hex) and/or to add an engagement recess
        if has_recess:
            # pylint: disable=no-member
            (recess_plan, recess_depth, recess_taper) = self.head_recess()
            recess = cq.Solid.extrudeLinear(
                recess_plan.val(),
                [],
                cq.Vector(0, 0, -recess_depth),
                taper=recess_taper,
            ).translate((0, 0, max_head_height))
            head_blank = head_plan.extrude(max_head_height).cut(recess)
            head = head.intersect(head_blank)
        elif has_plan:
            head_blank = (
                cq.Workplane("XY")
                .add(head_plan.val())
                .toPending()
                .extrude(max_head_height)
            )
            head = head.intersect(head_blank)

        # # Add a flange as it exists outside of the head plan
        if has_flange:
            # pylint: disable=no-member
            head = head.union(
                cq.Workplane("XZ")
                .add(self.flange_profile().val())
                .toPending()
                .revolve()
            )
        return head

    def default_head_recess(self) -> Tuple[cq.Workplane, float, float]:
        """Return the plan of the recess, its depth and taper"""

        recess_plan = None
        # Slot Recess
        try:
            (dk, n, t) = (self.screw_data[p] for p in ["dk", "n", "t"])
            recess_plan = slot_recess(dk, n)
            recess_depth = t
            recess_taper = 0
        except KeyError:
            pass
        # Hex Recess
        try:
            (s, t) = (self.screw_data[p] for p in ["s", "t"])
            recess_plan = hex_recess(s)
            recess_depth = t
            recess_taper = 0
        except KeyError:
            pass

        # Philips, Torx or Robertson Recess
        try:
            recess = self.screw_data["recess"]
            recess = str(recess).upper()
            if recess.startswith("PH"):
                (recess_plan, recess_depth) = cross_recess(recess)
                recess_taper = 30
            elif recess.startswith("T"):
                (recess_plan, recess_depth) = hexalobular_recess(recess)
                recess_taper = 0
            elif recess.startswith("R"):
                (recess_plan, recess_depth) = square_recess(recess)
                recess_taper = 0
        except KeyError:
            pass

        if recess_plan is None:
            raise ValueError(f"Recess data missing from screw_data{self.screw_data}")

        return (recess_plan, recess_depth, recess_taper)

    def default_countersink_profile(
        self, fit: Literal["Close", "Normal", "Loose"]
    ) -> cq.Workplane:
        """A simple rectangle with gets revolved into a cylinder"""
        try:
            clearance_hole_diameter = self.clearance_hole_diameters[fit]
        except KeyError as e:
            raise ValueError(
                f"{fit} invalid, must be one of {list(self.clearance_hole_diameters.keys())}"
            ) from e
        width = clearance_hole_diameter - self.thread_diameter + self.screw_data["dk"]
        return cq.Workplane("XZ").rect(width / 2, self.screw_data["k"], centered=False)


class ButtonHeadScrew(Screw):
    """
    size: str
    length: float
    fastener_type: str
        iso7380_1 - Hexagon socket button head screws
    hand: Optional[Literal["right", "left"]] = "right"
    simple: Optional[bool] = True
    """

    fastener_data = read_fastener_parameters_from_csv("button_head_parameters.csv")

    def head_profile(self):
        """Create 2D profile of button head screws"""
        (dk, dl, k, rf) = (self.screw_data[p] for p in ["dk", "dl", "k", "rf"])
        profile = (
            cq.Workplane("XZ")
            .vLineTo(k)
            .hLineTo(dl / 2)
            .radiusArc((dk / 2, 0), rf)
            .hLineTo(0)
            .close()
        )
        return profile

    head_recess = Screw.default_head_recess

    countersink_profile = Screw.default_countersink_profile


class ButtonHeadWithCollarScrew(Screw):
    """
    size: str
    length: float
    fastener_type: str
        iso7380_2 - Hexagon socket button head screws with collar
    hand: Optional[Literal["right", "left"]] = "right"
    simple: Optional[bool] = True
    """

    fastener_data = read_fastener_parameters_from_csv(
        "button_head_with_collar_parameters.csv"
    )

    def head_profile(self):
        """Create 2D profile of button head screws with collar"""
        (dk, dl, dc, k, rf, c) = (
            self.screw_data[p] for p in ["dk", "dl", "dc", "k", "rf", "c"]
        )
        profile = (
            cq.Workplane("XZ")
            .vLineTo(k)
            .hLineTo(dl / 2)
            .radiusArc((dk / 2, c), rf)
            .hLineTo(dc / 2)
            .vLineTo(0)
            .close()
        )
        vertices = profile.toPending().vertices(">X").vals()
        return profile.fillet2D(0.45 * c, vertices)

    head_recess = Screw.default_head_recess

    # countersink_profile = Screw.default_countersink_profile
    def countersink_profile(
        self, fit: Literal["Close", "Normal", "Loose"]
    ) -> cq.Workplane:
        """A simple rectangle with gets revolved into a cylinder"""
        try:
            clearance_hole_diameter = self.clearance_hole_diameters[fit]
        except KeyError as e:
            raise ValueError(
                f"{fit} invalid, must be one of {list(self.clearance_hole_diameters.keys())}"
            ) from e
        width = clearance_hole_diameter - self.thread_diameter + self.screw_data["dc"]
        return cq.Workplane("XZ").rect(width / 2, self.screw_data["k"], centered=False)


class CheeseHeadScrew(Screw):
    """
    size: str
    length: float
    fastener_type: str
        iso1207 - Slotted cheese head screws
        iso7048 - Cross-recessed cheese head screws
        iso14580 - Hexalobular socket cheese head screws
    hand: Optional[Literal["right", "left"]] = "right"
    simple: Optional[bool] = True
    """

    fastener_data = read_fastener_parameters_from_csv("cheese_head_parameters.csv")

    def head_profile(self):
        """cheese head screws"""
        (k, dk) = (self.screw_data[p] for p in ["k", "dk"])
        profile = (
            cq.Workplane("XZ")
            .hLineTo(dk / 2)
            .polarLine(k / cos(degrees(5)), 5 - 90)
            .hLineTo(0)
            .close()
        )
        vertices = profile.toPending().edges(">Z").vertices(">X").vals()
        return profile.fillet2D(k * 0.25, vertices)

    head_recess = Screw.default_head_recess

    countersink_profile = Screw.default_countersink_profile


class CounterSunkScrew(Screw):
    """
    size: str
    length: float
    fastener_type: str
        iso2009 - Slotted countersunk head screws
        iso7046 - Cross recessed countersunk flat head screws
        iso10642 - Hexagon socket countersunk head cap screws
        iso14581 - Hexalobular socket countersunk flat head screws
        iso14582 - Hexalobular socket countersunk flat head screws, high head
    hand: Optional[Literal["right", "left"]] = "right"
    simple: Optional[bool] = True
    """

    fastener_data = read_fastener_parameters_from_csv("countersunk_head_parameters.csv")

    def length_offset(self):
        """Countersunk screws include the head in the total length"""
        return self.screw_data["k"]

    def head_profile(self):
        """Create 2D profile of countersunk screw heads"""
        (a, k, dk) = (self.screw_data[p] for p in ["a", "k", "dk"])
        side_length = k / cos(radians(a / 2))
        profile = (
            cq.Workplane("XZ")
            .vLineTo(k)
            .hLineTo(dk / 2)
            .polarLine(side_length, -90 - a / 2)
            .close()
        )
        vertices = profile.toPending().edges(">Z").vertices(">X").vals()
        return profile.fillet2D(k * 0.075, vertices)

    head_recess = Screw.default_head_recess

    def countersink_profile(
        self, fit: Literal["Close", "Normal", "Loose"]
    ) -> cq.Workplane:
        """Create 2D profile of countersink profile"""
        (a, dk, k) = (self.screw_data[p] for p in ["a", "dk", "k"])
        side_length = k / cos(radians(a / 2))

        return (
            cq.Workplane("XZ")
            .vLineTo(k)
            .hLineTo(dk / 2)
            .polarLine(side_length, -90 - a / 2)
            .close()
        )


class HexHeadScrew(Screw):
    """
    size: str
    length: float
    fastener_type: str
        iso4014 - Hexagon head bolt
        iso4017 - Hexagon head screws
    hand: Optional[Literal["right", "left"]] = "right"
    simple: Optional[bool] = True
    socket_clearance: Optional[float] = 6 * MM
    """

    fastener_data = read_fastener_parameters_from_csv("hex_head_parameters.csv")

    def head_profile(self):
        """Create 2D profile of hex head screws"""
        (k, s) = (self.screw_data[p] for p in ["k", "s"])
        e = polygon_diagonal(s, 6)
        # Chamfer angle must be between 15 and 30 degrees
        cs = (e - s) * tan(radians(15)) / 2
        profile = (
            cq.Workplane("XZ")
            .hLineTo(e / 2)
            .vLineTo(k - cs)
            .lineTo(s / 2, k)
            .hLineTo(0)
            .close()
        )
        return profile

    def head_plan(self) -> cq.Workplane:
        """Create a hexagon solid"""
        return cq.Workplane("XY").polygon(6, polygon_diagonal(self.screw_data["s"]))

    def countersink_profile(
        self, fit: Literal["Close", "Normal", "Loose"]
    ) -> cq.Workplane:
        """A simple rectangle with gets revolved into a cylinder with an
        extra socket_clearance (defaults to 6mm across the diameter) for a socket wrench"""
        # Note that fit isn't used but remains for uniformity in the workplane hole methods
        del fit
        (k, s) = (self.screw_data[p] for p in ["k", "s"])
        e = polygon_diagonal(s, 6)
        width = e + self.socket_clearance + e
        return cq.Workplane("XZ").rect(width / 2, k, centered=False)


class HexHeadWithFlangeScrew(Screw):
    """
    size: str
    length: float
    fastener_type: str
        en1662 - Hexagon bolts with flange small series
        en1665 - Hexagon head bolts with flange
    hand: Optional[Literal["right", "left"]] = "right"
    simple: Optional[bool] = True
    socket_clearance: Optional[float] = 6 * MM
    """

    fastener_data = read_fastener_parameters_from_csv(
        "hex_head_with_flange_parameters.csv"
    )

    head_profile = HexHeadScrew.head_profile
    head_plan = HexHeadScrew.head_plan

    def flange_profile(self):
        """Flange for hexagon Bolts"""
        (dc, c) = (self.screw_data[p] for p in ["dc", "c"])
        flange_angle = 25
        tangent_point = (
            cq.Vector(
                (c / 2) * cos(radians(90 - flange_angle)),
                (c / 2) * sin(radians(90 - flange_angle)),
            )
            + cq.Vector((dc - c) / 2, c / 2)
        )
        profile = (
            cq.Workplane("XZ")
            .hLineTo(dc / 2 - c / 2)
            .radiusArc(tangent_point, -c / 2)
            .polarLine(dc / 2 - c / 2, 180 - flange_angle)
            .hLineTo(0)
            .close()
        )
        return profile

    def countersink_profile(
        self, fit: Literal["Close", "Normal", "Loose"]
    ) -> cq.Workplane:
        """A simple rectangle with gets revolved into a cylinder with
        at least socket_clearance (default 6mm across the diameter) for a socket wrench"""
        try:
            clearance_hole_diameter = self.clearance_hole_diameters[fit]
        except KeyError as e:
            raise ValueError(
                f"{fit} invalid, must be one of {list(self.clearance_hole_diameters.keys())}"
            ) from e
        (dc, s, k) = (self.screw_data[p] for p in ["dc", "s", "k"])
        shaft_clearance = clearance_hole_diameter - self.thread_diameter
        width = max(
            dc + shaft_clearance, polygon_diagonal(s, 6) + self.socket_clearance
        )
        return cq.Workplane("XZ").rect(width / 2, k, centered=False)


class PanHeadScrew(Screw):
    """
    size: str
    length: float
    fastener_type: str
        iso1580 - Slotted pan head screws
        iso14583 - Hexalobular socket pan head screws
        asme_b_18.6.3 - Type 1 Cross Recessed Pan Head Machine Screws
    hand: Optional[Literal["right", "left"]] = "right"
    simple: Optional[bool] = True
    """

    fastener_data = read_fastener_parameters_from_csv("pan_head_parameters.csv")

    def head_profile(self):
        """Slotted pan head screws"""
        (k, dk) = (self.screw_data[p] for p in ["k", "dk"])
        profile = (
            cq.Workplane("XZ")
            .hLineTo(dk / 2)
            .spline(
                [(dk * 0.25, k)],
                tangents=[(-sin(radians(5)), cos(radians(5))), (-1, 0)],
                includeCurrent=True,
            )
            .hLineTo(0)
            .close()
        )
        return profile

    head_recess = Screw.default_head_recess
    countersink_profile = Screw.default_countersink_profile


class PanHeadWithCollarScrew(Screw):
    """
    size: str
    length: float
    fastener_type: str
        din967 - Cross recessed pan head screws with collar
    hand: Optional[Literal["right", "left"]] = "right"
    simple: Optional[bool] = True
    """

    fastener_data = read_fastener_parameters_from_csv(
        "pan_head_with_collar_parameters.csv"
    )

    def head_profile(self):
        """Cross recessed pan head screws with collar"""
        (rf, k, dk, c) = (self.screw_data[p] for p in ["rf", "k", "dk", "c"])

        flat = sqrt(k - c) * sqrt(2 * rf - (k - c))
        profile = (
            cq.Workplane("XZ")
            .hLineTo(dk / 2)
            .vLineTo(c)
            .hLineTo(flat)
            .radiusArc((0, k), -rf)
            .close()
        )
        return profile

    head_recess = Screw.default_head_recess

    countersink_profile = Screw.default_countersink_profile


class RaisedCheeseHeadScrew(Screw):
    """
    size: str
    length: float
    fastener_type: str
        iso7045 - Cross recessed raised cheese head screws
    hand: Optional[Literal["right", "left"]] = "right"
    simple: Optional[bool] = True
    """

    fastener_data = read_fastener_parameters_from_csv(
        "raised_cheese_head_parameters.csv"
    )

    def head_profile(self):
        """raised cheese head screws"""
        (dk, k, rf) = (self.screw_data[p] for p in ["dk", "k", "rf"])
        oval_height = rf - sqrt(4 * rf ** 2 - dk ** 2) / 2
        profile = (
            cq.Workplane("XZ")
            .vLineTo(k)
            .radiusArc((dk / 2, k - oval_height), rf)
            .vLineTo(0)
            .close()
        )
        return profile

    head_recess = Screw.default_head_recess

    countersink_profile = Screw.default_countersink_profile


class RaisedCounterSunkOvalHeadScrew(Screw):
    """
    size: str
    length: float
    fastener_type: str
        iso2010 - Slotted raised countersunk oval head screws
        iso7047 - Cross recessed raised countersunk head screws
        iso14584 - Hexalobular socket raised countersunk head screws
    hand: Optional[Literal["right", "left"]] = "right"
    simple: Optional[bool] = True
    """

    fastener_data = read_fastener_parameters_from_csv(
        "raised_countersunk_oval_head_parameters.csv"
    )

    def length_offset(self):
        """Raised countersunk oval head screws include the head but not oval
        in the total length"""
        return self.screw_data["k"]

    def head_profile(self):
        """raised countersunk oval head screws"""
        (a, k, rf, dk) = (self.screw_data[p] for p in ["a", "k", "rf", "dk"])
        side_length = k / cos(radians(a / 2))
        oval_height = rf - sqrt(4 * rf ** 2 - dk ** 2) / 2
        profile = (
            cq.Workplane("XZ")
            .vLineTo(k + oval_height)
            .radiusArc((dk / 2, k), rf)
            .polarLine(side_length, -90 - a / 2)
            .close()
        )
        vertices = profile.toPending().edges(">Z").vertices(">X").vals()
        return profile.fillet2D(k * 0.075, vertices)

    head_recess = Screw.default_head_recess

    def countersink_profile(
        self, fit: Literal["Close", "Normal", "Loose"]
    ) -> cq.Workplane:
        """A flat bottomed cone"""
        (a, k, dk) = (self.screw_data[p] for p in ["a", "k", "dk"])
        side_length = k / cos(radians(a / 2))
        return (
            cq.Workplane("XZ")
            .vLineTo(k)
            .hLineTo(dk / 2)
            .polarLine(side_length, -90 - a / 2)
            .close()
        )


class SetScrew(Screw):
    """
    size: str
    length: float
    fastener_type: str
        iso4026 - Hexagon socket set screws with flat point
    hand: Optional[Literal["right", "left"]] = "right"
    simple: Optional[bool] = True
    """

    fastener_data = read_fastener_parameters_from_csv("setscrew_parameters.csv")

    @property
    def head(self):
        """Setscrews don't have heads"""
        return None

    @property
    def shank(self):
        """Setscrews don't have shanks"""
        return None

    @property
    def cq_object(self):
        """Setscrews are custom builds"""
        return self.make_setscrew()

    def make_setscrew(self) -> cq.Workplane:
        """Construct set screw shape"""

        (s, t) = (self.screw_data[p] for p in ["s", "t"])
        e = polygon_diagonal(s, 6)

        thread = IsoThread(
            major_diameter=self.thread_diameter,
            pitch=self.thread_pitch,
            length=self.length,
            external=True,
            end_finishes=("fade", "fade"),
            hand=self.hand,
        )
        core = (
            cq.Workplane("XY")
            .circle(thread.min_radius)
            .polygon(6, e)
            .extrude(t)
            .faces(">Z")
            .workplane()
            .circle(thread.min_radius)
            .extrude(self.length - t)
            .mirror()
        )
        return core.union(thread.cq_object.translate((0, 0, -thread.length))).val()

    def make_head(self):
        """There is no head on a setscrew"""
        return None

    def countersink_profile(self, fit):
        """There is no head on a setscrew"""
        return None


class SocketHeadCapScrew(Screw):
    """
    size: str
    length: float
    fastener_type: str
        iso4762 - Hexagon socket head cap screws
    hand: Optional[Literal["right", "left"]] = "right"
    simple: Optional[bool] = True
    """

    fastener_data = read_fastener_parameters_from_csv("socket_head_cap_parameters.csv")

    def head_profile(self):
        """Socket Head Cap Screws"""
        (dk, k) = (self.screw_data[p] for p in ["dk", "k"])
        profile = cq.Workplane("XZ").rect(dk / 2, k, centered=False)
        vertices = profile.toPending().edges(">Z").vertices(">X").vals()
        return profile.fillet2D(k * 0.075, vertices)

    head_recess = Screw.default_head_recess

    countersink_profile = Screw.default_countersink_profile


class Washer(ABC):
    """Base Class used to create standard washers"""

    # Read clearance and tap hole dimesions tables
    # Close, Normal, Loose
    clearance_hole_drill_sizes = read_fastener_parameters_from_csv(
        "clearance_hole_sizes.csv"
    )
    clearance_hole_data = lookup_drill_diameters(clearance_hole_drill_sizes)

    @property
    def clearance_hole_diameters(self):
        """A dictionary of drill diameters for clearance holes"""
        try:
            return self.clearance_hole_data[self.size.split("-")[0]]
        except KeyError as e:
            raise ValueError(f"No clearance hole data for size {self.size}") from e

    @property
    @classmethod
    @abstractmethod
    def fastener_data(cls):
        """Each derived class must provide a fastener_data dictionary"""
        return NotImplementedError

    @abstractmethod
    def washer_profile(self) -> cq.Workplane:
        """Each derived class must provide the profile of the washer"""
        return NotImplementedError

    @property
    def info(self):
        """Return identifying information"""
        return f"{self.washer_class}({self.fastener_type}): {self.size}"

    @property
    def washer_class(self):
        """Which derived class created this washer"""
        return type(self).__name__

    @classmethod
    def types(cls) -> List[str]:
        """Return a set of the washer types"""
        return set(p.split(":")[0] for p in list(cls.fastener_data.values())[0].keys())

    @classmethod
    def sizes(cls, fastener_type: str) -> List[str]:
        """Return a list of the washer sizes for the given type"""
        return list(isolate_fastener_type(fastener_type, cls.fastener_data).keys())

    @classmethod
    def select_by_size(cls, size: str) -> dict:
        """Return a dictionary of list of fastener types of this size"""
        return select_by_size_fn(cls, size)

    @property
    def washer_thickness(self):
        """Calculate the maximum thickness of the washer"""
        return cq.Workplane(self.cq_object).vertices(">Z").val().Z

    @property
    def washer_diameter(self):
        """Calculate the maximum diameter of the washer"""
        vertices = cq.Workplane(self.cq_object).vertices().vals()
        radii = [
            (cq.Vector(0, 0, v.Z) - cq.Vector(v.toTuple())).Length for v in vertices
        ]
        return 2 * max(radii)

    @property
    def cq_object(self):
        """A cadquery Compound screw as defined by class attributes"""
        return self._cq_object

    # @cache
    def __init__(
        self,
        size: str,
        fastener_type: str,
    ):
        self.size = size
        self.is_metric = self.size[0] == "M"
        # Used only for clearance gap calculations
        if self.is_metric:
            self.thread_diameter = float(size[1:])
        else:
            self.thread_diameter = imperial_str_to_float(size)

        if fastener_type not in self.types():
            raise ValueError(f"{fastener_type} invalid, must be one of {self.types()}")
        self.fastener_type = fastener_type
        try:
            self.washer_data = evaluate_parameter_dict(
                isolate_fastener_type(self.fastener_type, self.fastener_data)[
                    self.size
                ],
                is_metric=self.is_metric,
            )
        except KeyError as e:
            raise ValueError(
                f"{size} invalid, must be one of {self.sizes(self.fastener_type)}"
            ) from e
        self._cq_object = self.make_washer().val()

    def make_washer(self) -> cq.Workplane:
        """Create a screw head from the 2D shapes defined in the derived class"""

        # Create the basic washer shape
        # pylint: disable=no-member
        return self.washer_profile().toPending().revolve()

    def default_washer_profile(self):
        """Create 2D profile of hex washers with double chamfers"""
        (d1, d2, h) = (self.washer_data[p] for p in ["d1", "d2", "h"])
        profile = (
            cq.Workplane("XZ")
            .moveTo(d1 / 2, 0)
            .hLineTo(d2 / 2)
            .vLineTo(h)
            .hLineTo(d1 / 2)
            .close()
        )
        return profile

    def default_countersink_profile(
        self, fit: Literal["Close", "Normal", "Loose"]
    ) -> cq.Workplane:
        """A simple rectangle with gets revolved into a cylinder"""
        try:
            clearance_hole_diameter = self.clearance_hole_diameters[fit]
        except KeyError as e:
            raise ValueError(
                f"{fit} invalid, must be one of {list(self.clearance_hole_diameters.keys())}"
            ) from e
        gap = clearance_hole_diameter - self.thread_diameter
        (d2, h) = (self.washer_data[p] for p in ["d2", "h"])
        return cq.Workplane("XZ").rect(d2 / 2 + gap, h, centered=False)


class PlainWasher(Washer):
    """
    size: str - e.g. "M6"
    fastener_type: str - e.g. "iso7089"
        iso7089 - Plain washers, Form A
        iso7091 - Plain washers
        iso7093 - Plain washers — Large series
        iso7094 - Plain washers - Extra large series
    """

    fastener_data = read_fastener_parameters_from_csv("plain_washer_parameters.csv")
    washer_profile = Washer.default_washer_profile
    washer_countersink_profile = Washer.default_countersink_profile


class ChamferedWasher(Washer):
    """
    size: str - e.g. "M6"
    fastener_type: str - e.g. "iso7090"
        iso7090 - Plain washers, Form B
    """

    fastener_data = read_fastener_parameters_from_csv("chamfered_washer_parameters.csv")

    def washer_profile(self):
        """Create 2D profile of hex washers with double chamfers"""
        (d1, d2, h) = (self.washer_data[p] for p in ["d1", "d2", "h"])
        profile = (
            cq.Workplane("XZ")
            .moveTo(d1 / 2, 0)
            .hLineTo(d2 / 2)
            .vLineTo(0.75 * h)
            .lineTo(d2 / 2 - h * 0.25, h)
            .hLineTo(d1 / 2)
            .close()
        )
        return profile

    washer_countersink_profile = Washer.default_countersink_profile


class CheeseHeadWasher(Washer):
    """
    size: str - e.g. "M6"
    fastener_type: str - e.g. "iso7092"
        iso7092 - Washers for cheese head screws
    """

    fastener_data = read_fastener_parameters_from_csv(
        "cheese_head_washer_parameters.csv"
    )

    def washer_profile(self):
        """Create 2D profile of hex washers with double chamfers"""
        (d1, d2, h) = (self.washer_data[p] for p in ["d1", "d2", "h"])
        profile = (
            cq.Workplane("XZ")
            .moveTo(d1 / 2 + h / 4, 0)
            .hLineTo(d2 / 2)
            .vLineTo(h)
            .hLineTo(d1 / 2 + h / 4)
            .lineTo(d1 / 2, 0.75 * h)
            .vLineTo(h / 4)
            .close()
        )
        return profile

    washer_countersink_profile = Washer.default_countersink_profile


T = TypeVar("T", bound="Workplane")


def _fastenerHole(
    self: T,
    hole_diameters: dict,
    fastener: Union[Nut, Screw],
    depth: float,
    washers: List[Washer],
    fit: Optional[Literal["Close", "Normal", "Loose"]] = None,
    material: Optional[Literal["Soft", "Hard"]] = None,
    counterSunk: Optional[bool] = True,
    baseAssembly: Optional[cq.Assembly] = None,
    hand: Optional[Literal["right", "left"]] = None,
    simple: Optional[bool] = False,
    clean: Optional[bool] = True,
) -> T:
    """
    Makes a counterbore clearance, tap or threaded hole for the given screw for each item
    on the stack. The surface of the hole is at the current workplane.
    """

    # If there is a thread direction, this is a threaded hole
    threaded_hole = not hand is None

    bore_direction = cq.Vector(0, 0, -1)
    origin = cq.Vector(0, 0, 0)

    # Setscrews' countersink_profile is None so check if it exists
    countersink_profile = fastener.countersink_profile(fit)
    if counterSunk and not countersink_profile is None:
        head_offset = countersink_profile.vertices(">Z").val().Z
        countersink_cutter = (
            countersink_profile.toPending()
            .revolve()
            .translate((0, 0, -head_offset))
            .val()
        )
    else:
        head_offset = 0

    if threaded_hole:
        hole_radius = fastener.thread_diameter / 2
    else:
        key = fit if material is None else material
        try:
            hole_radius = hole_diameters[key] / 2
        except KeyError as e:
            raise ValueError(
                f"{key} invalid, must be one of {list(hole_diameters.keys())}"
            ) from e

    shank_hole = cq.Solid.makeCylinder(
        radius=hole_radius,
        height=depth,
        pnt=origin,
        dir=bore_direction,
    )
    if counterSunk and not countersink_profile is None:
        fastener_hole = countersink_cutter.fuse(shank_hole)
    else:
        fastener_hole = shank_hole

    cskAngle = 82  # Common tip angle
    h = hole_radius / tan(radians(cskAngle / 2.0))
    drill_tip = cq.Solid.makeCone(
        hole_radius, 0.0, h, bore_direction * depth, bore_direction
    )
    fastener_hole = fastener_hole.fuse(drill_tip)

    # Record the location of each hole for use in the assembly
    null_object = cq.Solid.makeBox(1, 1, 1)
    relocated_test_objects = self.eachpoint(lambda loc: null_object.moved(loc), True)
    hole_locations = [loc.location() for loc in relocated_test_objects.vals()]

    # Add fasteners and washers to the base assembly if it was provided
    if baseAssembly is not None:
        for hole_loc in hole_locations:
            washer_thicknesses = 0
            if not washers is None:
                for washer in washers:
                    baseAssembly.add(
                        washer.cq_object,
                        loc=hole_loc
                        * cq.Location(
                            bore_direction
                            * (
                                head_offset
                                - fastener.length_offset()
                                - washer_thicknesses
                            )
                        ),
                    )
                    washer_thicknesses += washer.washer_thickness
                    if hasattr(baseAssembly, "metadata"):
                        baseAssembly.metadata[baseAssembly.children[-1].name] = washer
                    else:
                        baseAssembly.metadata = {baseAssembly.children[-1].name: washer}

            baseAssembly.add(
                fastener.cq_object,
                loc=hole_loc
                * cq.Location(
                    bore_direction
                    * (head_offset - fastener.length_offset() - washer_thicknesses)
                ),
            )
            if hasattr(baseAssembly, "metadata"):
                baseAssembly.metadata[baseAssembly.children[-1].name] = fastener
            else:
                baseAssembly.metadata = {baseAssembly.children[-1].name: fastener}

    # Make holes in the stack solid object
    part = self.cutEach(lambda loc: fastener_hole.moved(loc), True, False)

    # Add threaded inserts
    if threaded_hole and not simple:
        thread = IsoThread(
            major_diameter=fastener.thread_diameter,
            pitch=fastener.thread_pitch,
            length=depth - head_offset,
            external=False,
            hand=hand,
        )
        for hole_loc in hole_locations:
            part = part.union(
                thread.cq_object.moved(hole_loc * cq.Location(bore_direction * depth))
            )
    if clean:
        part = part.clean()
    return part


cq.Workplane.fastenerHole = _fastenerHole


def _clearanceHole(
    self: T,
    fastener: Union[Nut, Screw],
    washers: Optional[List[Washer]] = None,
    fit: Optional[Literal["Close", "Normal", "Loose"]] = "Normal",
    depth: Optional[float] = None,
    counterSunk: Optional[bool] = True,
    baseAssembly: Optional[cq.Assembly] = None,
    clean: Optional[bool] = True,
) -> T:
    """Clearance hole front end to fastener hole"""
    if depth is None:
        depth = self.largestDimension()

    return self.fastenerHole(
        hole_diameters=fastener.clearance_hole_diameters,
        fastener=fastener,
        washers=washers,
        fit=fit,
        depth=depth,
        counterSunk=counterSunk,
        baseAssembly=baseAssembly,
        clean=clean,
    )


def _tapHole(
    self: T,
    fastener: Union[Nut, Screw],
    washers: Optional[List[Washer]] = None,
    material: Optional[Literal["Soft", "Hard"]] = "Soft",
    depth: Optional[float] = None,
    counterSunk: Optional[bool] = True,
    fit: Optional[Literal["Close", "Normal", "Loose"]] = "Normal",
    baseAssembly: Optional[cq.Assembly] = None,
    clean: Optional[bool] = True,
) -> T:
    """Tap hole front end to fastener hole"""
    if depth is None:
        depth = self.largestDimension()

    return self.fastenerHole(
        hole_diameters=fastener.tap_hole_diameters,
        fastener=fastener,
        washers=washers,
        fit=fit,
        material=material,
        depth=depth,
        counterSunk=counterSunk,
        baseAssembly=baseAssembly,
        clean=clean,
    )


def _threadedHole(
    self: T,
    fastener: Screw,
    depth: float,
    washers: Optional[List[Washer]] = None,
    hand: Literal["right", "left"] = "right",
    simple: Optional[bool] = False,
    counterSunk: Optional[bool] = True,
    fit: Optional[Literal["Close", "Normal", "Loose"]] = "Normal",
    baseAssembly: Optional[cq.Assembly] = None,
    clean: Optional[bool] = True,
) -> T:
    """Threaded hole front end to fastener hole"""
    return self.fastenerHole(
        hole_diameters=fastener.clearance_hole_diameters,
        fastener=fastener,
        washers=washers,
        fit=fit,
        depth=depth,
        counterSunk=counterSunk,
        baseAssembly=baseAssembly,
        hand=hand,
        simple=simple,
        clean=clean,
    )


cq.Workplane.clearanceHole = _clearanceHole
cq.Workplane.tapHole = _tapHole
cq.Workplane.threadedHole = _threadedHole


def _fastener_quantities(self, bom: bool = True) -> dict:
    """Generate a bill of materials of the fasteners in an assembly augmented by the hole methods
    bom: returns fastener.info if True else fastener
    """
    if self.metadata is None:
        return None

    # Extract a list of only the fasteners from the metadata
    fasteners = [
        value
        for value in self.metadata.values()
        if isinstance(value, (Screw, Nut, Washer))
    ]
    unique_fasteners = set(fasteners)
    if bom:
        quantities = {f.info: fasteners.count(f) for f in unique_fasteners}
    else:
        quantities = {f: fasteners.count(f) for f in unique_fasteners}
    return quantities


cq.Assembly.fastener_quantities = _fastener_quantities
