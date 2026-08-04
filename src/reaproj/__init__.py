"""reaproj: semantic access to REAPER .RPP project files.

Built on the `rpp` tokenizer (Perlence/rpp). Instead of hand-editing chunk
text, work with projects, tracks, items, markers, regions, and render
settings:

    from reaproj import Project, RenderBounds

    project = Project.load("Session.RPP")
    for track in project.tracks:
        for item in track.items:
            print(item.position, item.length, item.source_path)

    project.add_region(12.5, 95.0, "Take 1")
    project.render.directory = "Takes"
    project.render.pattern = "$region"
    project.render.bounds = RenderBounds.ALL_REGIONS
    project.save_next_version()  # writes "Session v2.RPP"

Emission preserves all content; the only changes relative to REAPER's own
output are cosmetic quoting normalizations (quotes dropped on space-free
strings), which REAPER parses identically.
"""

from __future__ import annotations

import base64
import re
import uuid
from enum import IntEnum
from pathlib import Path

import rpp as _rpp

__version__ = "0.1.1"

__all__ = [
    "Project",
    "Track",
    "Item",
    "Marker",
    "Region",
    "RenderSettings",
    "RenderBounds",
    "RENDER_FORMATS",
]

# Base64 RENDER_CFG payloads for common output formats, as written by REAPER.
RENDER_FORMATS = {
    "wav24": "ZXZhdxgAAQ==",
    "mp3": "bDNwbUABAAAAAAAAAgAAAP////8EAAAAQAEAAAAAAAA=",
}


class RenderBounds(IntEnum):
    """RENDER_RANGE bounds values as observed in REAPER 7 project files."""

    CUSTOM_TIME = 0
    ENTIRE_PROJECT = 1
    TIME_SELECTION = 2
    ALL_REGIONS = 3
    SELECTED_ITEMS = 4
    SELECTED_REGIONS = 5
    RAZOR_EDITS = 6
    ALL_MARKERS = 7


class Project:
    """A parsed .RPP project. Load, inspect, modify, save."""

    def __init__(self, element, path=None):
        self.element = element
        self.path = Path(path) if path else None

    @classmethod
    def load(cls, path):
        path = Path(path)
        return cls(_rpp.loads(path.read_text()), path)

    @classmethod
    def loads(cls, text):
        return cls(_rpp.loads(text))

    def dumps(self):
        return _rpp.dumps(self.element)

    def save(self, path=None):
        target = Path(path) if path else self.path
        if target is None:
            raise ValueError("No path: pass one or load the project from a file.")
        target.write_text(self.dumps())
        self.path = target
        return target

    def save_next_version(self):
        """Save as the next free " vN" sibling (e.g. "Session v2.RPP")."""
        if self.path is None:
            raise ValueError("Project was not loaded from a file.")
        stem = re.sub(r" v\d+$", "", self.path.stem)
        version = 2
        while (candidate := self.path.with_name(f"{stem} v{version}{self.path.suffix}")).exists():
            version += 1
        return self.save(candidate)

    @property
    def tracks(self):
        return [Track(el, self) for el in self.element.iterfind("TRACK")]

    @property
    def markers(self):
        return [Marker(tokens) for tokens in self._marker_leaves() if not _is_region_boundary(tokens)]

    @property
    def regions(self):
        regions, pending = [], None
        for tokens in self._marker_leaves():
            if not _is_region_boundary(tokens):
                continue
            if pending is None:
                pending = tokens
            else:
                regions.append(Region(pending, tokens))
                pending = None
        return regions

    def remove_region(self, region):
        """Delete a region: both of its MARKER lines come out together."""
        for tokens in (region.head, region.tail):
            for child in list(self.element):
                if child is tokens:
                    self.element.remove(child)
                    break

    def add_region(self, start, end, name):
        """Append a region; returns the new Region."""
        region_id = max((int(t[1]) for t in self._marker_leaves()), default=0) + 1
        guid = "{" + str(uuid.uuid4()).upper() + "}"
        head = ["MARKER", str(region_id), _num(start), name, "1", "0", "1", "B", guid, "0"]
        tail = ["MARKER", str(region_id), _num(end), "", "1"]
        children = self.element
        index = self._marker_insert_index()
        children.insert(index, head)
        children.insert(index + 1, tail)
        return Region(head, tail)

    def add_track(self, name, index=None):
        """Insert a new track, by default at the end. `index` is an ordinal
        among existing tracks, so 0 puts it first."""
        guid = "{" + str(uuid.uuid4()).upper() + "}"
        element = _rpp.Element(tag="TRACK", attrib=[guid], children=[
            ["NAME", name],
            ["PEAKCOL", "16576"],
            ["BEAT", "-1"],
            ["AUTOMODE", "0"],
            ["PANLAWFLAGS", "3"],
            ["VOLPAN", "1", "0", "-1", "-1", "1"],
            ["MUTESOLO", "0", "0", "0"],
            ["IPHASE", "0"],
            ["PLAYOFFS", "0", "1"],
            ["ISBUS", "0", "0"],
            ["BUSCOMP", "0", "0", "0", "0", "0"],
            ["SHOWINMIX", "1", "0.6667", "0.5", "1", "0.5", "0", "0", "0", "0"],
            ["FIXEDLANES", "9", "0", "0", "0", "0"],
            ["LANEREC", "-1", "-1", "-1", "0"],
            ["SEL", "0"],
            ["REC", "0", "1027", "1", "0", "0", "0", "0", "0"],
            ["VU", "64"],
            ["TRACKHEIGHT", "25", "0", "0", "0", "0", "0", "0"],
            ["INQ", "0", "0", "0", "0.5", "100", "0", "0", "100"],
            ["NCHAN", "2"],
            ["FX", "1"],
            ["TRACKID", guid],
            ["PERF", "0"],
            ["MIDIOUT", "-1"],
            ["MAINSEND", "1", "0"],
        ])
        positions = [i for i, c in enumerate(self.element)
                     if getattr(c, "tag", None) == "TRACK"]
        if index is None or index >= len(positions):
            at = positions[-1] + 1 if positions else len(list(self.element))
        else:
            at = positions[index]
        self.element.insert(at, element)
        return Track(element, self)

    @property
    def render(self):
        return RenderSettings(self.element)

    def resolve(self, source_path):
        """Resolve an item source path against the project directory."""
        source_path = Path(source_path)
        if source_path.is_absolute() or self.path is None:
            return source_path
        return self.path.parent / source_path

    def _marker_leaves(self):
        return [c for c in self.element if isinstance(c, list) and c and c[0] == "MARKER"]

    def _marker_insert_index(self):
        last = None
        for i, child in enumerate(self.element):
            if isinstance(child, list) and child and child[0] == "MARKER":
                last = i
            elif getattr(child, "tag", None) == "TRACK" and last is None:
                return i
        if last is not None:
            return last + 1
        return len(list(self.element))


class Track:
    def __init__(self, element, project=None):
        self.element = element
        self.project = project

    @property
    def name(self):
        leaf = _leaf(self.element, "NAME")
        return leaf[1] if leaf and len(leaf) > 1 else ""

    @name.setter
    def name(self, value):
        _set_leaf(self.element, "NAME", value)

    @property
    def items(self):
        return [Item(el, self.project) for el in self.element.iterfind("ITEM")]

    @property
    def index(self):
        """Ordinal position among the project's tracks, as AUXRECV refers to it."""
        if self.project is None:
            raise ValueError("Track was created without a project.")
        for i, el in enumerate(self.project.element.iterfind("TRACK")):
            if el is self.element:
                return i
        raise ValueError("Track is not part of its project.")

    # VOLPAN <volume> <pan> ... — volume is linear amplitude, pan runs -1..1.
    volume = property(lambda self: _get_field(self.element, "VOLPAN", 0),
                      lambda self, v: _set_field(self.element, "VOLPAN", 0, _num(v)))
    pan = property(lambda self: _get_field(self.element, "VOLPAN", 1),
                   lambda self, v: _set_field(self.element, "VOLPAN", 1, _num(v)))

    @property
    def muted(self):
        return bool(_get_field(self.element, "MUTESOLO", 0))

    @muted.setter
    def muted(self, value):
        _set_field(self.element, "MUTESOLO", 0, "1" if value else "0")

    # PLAYOFFS <seconds> <flags>; flag bit 0 disables, bit 1 means samples.
    play_offset = property(lambda self: _get_field(self.element, "PLAYOFFS", 0),
                           lambda self, v: _set_field(self.element, "PLAYOFFS", 0, _num(v)))

    @property
    def play_offset_enabled(self):
        flags = _get_field(self.element, "PLAYOFFS", 1)
        return flags is not None and int(flags) & 1 == 0

    @play_offset_enabled.setter
    def play_offset_enabled(self, value):
        flags = int(_get_field(self.element, "PLAYOFFS", 1) or 0)
        _set_field(self.element, "PLAYOFFS", 1, str(flags & ~1 if value else flags | 1))

    @property
    def folder(self):
        """ISBUS as (depth, delta): (1, 1) opens a folder, (2, -1) closes one,
        (0, 0) is an ordinary track. A track closing several folders at once
        carries a delta below -1."""
        leaf = _leaf(self.element, "ISBUS")
        if leaf is None or len(leaf) < 3:
            return (0, 0)
        return (int(leaf[1]), int(leaf[2]))

    @folder.setter
    def folder(self, value):
        depth, delta = value
        _set_leaf(self.element, "ISBUS", str(depth), str(delta))

    def add_receive(self, source, volume=1.0, pan=0.0, mode=0):
        """Receive audio from another track. REAPER stores a send on the
        receiving track, so this is what a send looks like from the far end."""
        if source.index == self.index:
            raise ValueError("A track cannot receive from itself.")
        self.remove_receive(source)
        leaf = ["AUXRECV", str(source.index), str(mode), _num(volume), _num(pan),
                "0", "0", "0", "0", "0", "-1:U", "0", "-1", "''"]
        self.element.insert(_receive_insert_index(self.element), leaf)
        return leaf

    def remove_receive(self, source):
        wanted = str(source.index)
        for child in list(self.element):
            if isinstance(child, list) and child and child[0] == "AUXRECV" and child[1] == wanted:
                self.element.remove(child)

    @property
    def receives(self):
        return [(int(c[1]), float(c[3])) for c in self.element
                if isinstance(c, list) and c and c[0] == "AUXRECV"]

    def set_volume_envelope(self, points, square=True):
        """Replace the track volume envelope with `points`, an iterable of
        (time, linear_gain). Square shape holds each value until the next point,
        which is what a per-section level wants; linear would ramp between them."""
        return self._set_envelope("VOLENV2", points, square, extra=[["VOLTYPE", "1"]])

    def set_pan_envelope(self, points, square=True):
        """Replace the track pan envelope with `points`, an iterable of
        (time, pan) where pan runs -1 (hard left) to 1 (hard right)."""
        return self._set_envelope("PANENV2", points, square)

    def remove_envelopes(self, *kinds):
        """Delete whole envelope blocks by tag, e.g. "VOLENV3" for trim volume.
        Returns how many points went with them."""
        removed = 0
        for child in list(self.element):
            if getattr(child, "tag", None) in kinds:
                removed += sum(1 for c in child
                               if isinstance(c, list) and c and c[0] == "PT")
                self.element.remove(child)
        return removed

    @property
    def envelopes(self):
        """{tag: point count} for every envelope on the track."""
        out = {}
        for child in self.element:
            tag = getattr(child, "tag", None)
            if tag and ("ENV" in tag):
                out[tag] = sum(1 for c in child
                               if isinstance(c, list) and c and c[0] == "PT")
        return out

    def _set_envelope(self, tag, points, square, extra=()):
        self.remove_envelopes(tag)
        shape = "1" if square else "0"
        children = [
            ["EGUID", "{" + str(uuid.uuid4()).upper() + "}"],
            ["ACT", "1", "-1"],
            ["VIS", "1", "1", "1"],
            ["LANEHEIGHT", "0", "0"],
            ["ARM", "0"],
            ["DEFSHAPE", shape, "-1", "-1"],
            *[list(e) for e in extra],
        ]
        for time, value in points:
            children.append(["PT", _num(time), _num(value), shape])
        env = _rpp.Element(tag=tag, attrib=[], children=children)
        self.element.insert(_receive_insert_index(self.element), env)
        return env


class Item:
    def __init__(self, element, project=None):
        self.element = element
        self.project = project

    position = property(lambda self: _get_float(self.element, "POSITION"),
                        lambda self, v: _set_leaf(self.element, "POSITION", _num(v)))
    length = property(lambda self: _get_float(self.element, "LENGTH"),
                      lambda self, v: _set_leaf(self.element, "LENGTH", _num(v)))
    soffs = property(lambda self: _get_float(self.element, "SOFFS"),
                     lambda self, v: _set_leaf(self.element, "SOFFS", _num(v)))

    def move_to(self, track):
        """Move this item onto another track, keeping its position and source."""
        for candidate in self.project.element.iterfind("TRACK") if self.project else []:
            for child in list(candidate):
                if child is self.element:
                    candidate.remove(child)
                    break
        track.element.append(self.element)
        return self

    @property
    def source_path(self):
        """The item's media file path, resolved against the project when possible."""
        source = self.element.find("SOURCE")
        if source is None:
            return None
        file_leaf = _leaf(source, "FILE")
        if file_leaf is None or len(file_leaf) < 2:
            return None
        path = file_leaf[1]
        return self.project.resolve(path) if self.project else Path(path)

    @property
    def source_offset_end(self):
        return self.soffs + self.length


class Marker:
    def __init__(self, tokens):
        self.tokens = tokens

    id = property(lambda self: int(self.tokens[1]))
    position = property(lambda self: float(self.tokens[2]),
                        lambda self, v: self.tokens.__setitem__(2, _num(v)))

    @property
    def name(self):
        return self.tokens[3] if len(self.tokens) > 3 else ""

    @name.setter
    def name(self, value):
        while len(self.tokens) <= 3:
            self.tokens.append("")
        self.tokens[3] = value


class Region:
    def __init__(self, head, tail):
        self.head = head
        self.tail = tail

    id = property(lambda self: int(self.head[1]))
    start = property(lambda self: float(self.head[2]),
                     lambda self, v: self.head.__setitem__(2, _num(v)))
    end = property(lambda self: float(self.tail[2]),
                   lambda self, v: self.tail.__setitem__(2, _num(v)))

    @property
    def name(self):
        return self.head[3] if len(self.head) > 3 else ""

    @name.setter
    def name(self, value):
        while len(self.head) <= 3:
            self.head.append("")
        self.head[3] = value

    @property
    def length(self):
        return self.end - self.start


class RenderSettings:
    """View over the project's RENDER_* lines; missing lines are created on set."""

    def __init__(self, element):
        self.element = element

    directory = property(
        lambda self: _leaf_value(self.element, "RENDER_FILE"),
        lambda self, v: _set_leaf(self.element, "RENDER_FILE", str(v)))
    pattern = property(
        lambda self: _leaf_value(self.element, "RENDER_PATTERN"),
        lambda self, v: _set_leaf(self.element, "RENDER_PATTERN", str(v)))

    @property
    def bounds(self):
        leaf = _leaf(self.element, "RENDER_RANGE")
        return RenderBounds(int(leaf[1])) if leaf else None

    @bounds.setter
    def bounds(self, value):
        leaf = _leaf(self.element, "RENDER_RANGE")
        if leaf is None:
            _set_leaf(self.element, "RENDER_RANGE", str(int(value)), "0", "0", "0", "1000")
        else:
            leaf[1] = str(int(value))

    @property
    def stems(self):
        value = _leaf_value(self.element, "RENDER_STEMS")
        return int(value) if value is not None else None

    @stems.setter
    def stems(self, value):
        _set_leaf(self.element, "RENDER_STEMS", str(int(value)))

    @property
    def dither(self):
        value = _leaf_value(self.element, "RENDER_DITHER")
        return int(value) if value is not None else None

    @dither.setter
    def dither(self, value):
        _set_leaf(self.element, "RENDER_DITHER", str(int(value)))

    @property
    def normalize_enabled(self):
        return _leaf(self.element, "RENDER_NORMALIZE") is not None

    @normalize_enabled.setter
    def normalize_enabled(self, value):
        if value:
            raise ValueError("Enabling normalization requires REAPER-specific flags; set the line manually.")
        leaf = _leaf(self.element, "RENDER_NORMALIZE")
        if leaf is not None:
            self.element.remove(leaf)

    @property
    def format(self):
        """Named format ('wav24', 'mp3') if recognized, else the raw base64 config."""
        cfg = self.element.find("RENDER_CFG")
        if cfg is None:
            return None
        payload = next((_payload_str(c) for c in cfg if _payload_str(c)), None)
        for name, b64 in RENDER_FORMATS.items():
            if payload == b64:
                return name
        return payload

    @format.setter
    def format(self, value):
        payload = RENDER_FORMATS.get(value, value)
        base64.b64decode(payload)  # validates
        cfg = self.element.find("RENDER_CFG")
        if cfg is None:
            raise ValueError("Project has no RENDER_CFG block.")
        for child in list(cfg):
            cfg.remove(child)
        cfg.append(payload)


def _leaf(element, key):
    for child in element:
        if isinstance(child, list) and child and child[0] == key:
            return child
    return None


def _leaf_value(element, key):
    leaf = _leaf(element, key)
    return leaf[1] if leaf and len(leaf) > 1 else None


def _get_float(element, key):
    value = _leaf_value(element, key)
    return float(value) if value is not None else None


def _set_leaf(element, key, *values):
    leaf = _leaf(element, key)
    if leaf is None:
        element.insert(0, [key, *values])
    else:
        leaf[1:] = list(values)


def _get_field(element, key, offset):
    leaf = _leaf(element, key)
    if leaf is None or len(leaf) <= offset + 1:
        return None
    return float(leaf[offset + 1])


# What REAPER writes for a default track, used when a line is absent entirely.
_TRACK_DEFAULTS = {
    "VOLPAN": ["1", "0", "-1", "-1", "1"],
    "MUTESOLO": ["0", "0", "0"],
    "PLAYOFFS": ["0", "1"],
    "ISBUS": ["0", "0"],
}


def _set_field(element, key, offset, value):
    leaf = _leaf(element, key)
    if leaf is None:
        leaf = [key, *_TRACK_DEFAULTS.get(key, ["0"])]
        element.insert(_receive_insert_index(element), leaf)
    while len(leaf) <= offset + 1:
        leaf.append("0")
    leaf[offset + 1] = value


def _receive_insert_index(element):
    """AUXRECV and envelope blocks belong after the track's scalar settings but
    before its FX chain and items, which is where REAPER writes them."""
    for i, child in enumerate(element):
        if getattr(child, "tag", None) in ("FXCHAIN", "FXCHAIN_REC", "ITEM"):
            return i
    return len(list(element))


def _num(value):
    return f"{float(value):.14g}"


def _payload_str(child):
    """A RENDER_CFG-style base64 payload child: a bare string or one-token list."""
    if isinstance(child, str):
        return child
    if isinstance(child, list) and len(child) == 1 and isinstance(child[0], str):
        return child[0]
    return None


def _is_region_boundary(tokens):
    """Region lines carry an odd flag in column 5 (bit 0 = region; REAPER
    writes 1, or 5 when other flag bits are set)."""
    try:
        return len(tokens) > 4 and int(tokens[4]) & 1 == 1
    except ValueError:
        return False
