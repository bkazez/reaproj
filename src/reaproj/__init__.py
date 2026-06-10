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

__version__ = "0.1.0"

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

    @property
    def items(self):
        return [Item(el, self.project) for el in self.element.iterfind("ITEM")]


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
    position = property(lambda self: float(self.tokens[2]))
    name = property(lambda self: self.tokens[3] if len(self.tokens) > 3 else "")


class Region:
    def __init__(self, head, tail):
        self.head = head
        self.tail = tail

    id = property(lambda self: int(self.head[1]))
    start = property(lambda self: float(self.head[2]))
    end = property(lambda self: float(self.tail[2]))
    name = property(lambda self: self.head[3])

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
    return len(tokens) > 4 and tokens[4] == "1"
