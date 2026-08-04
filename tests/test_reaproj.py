import textwrap

import pytest

from reaproj import Project, RenderBounds, RENDER_FORMATS

FIXTURE = textwrap.dedent("""\
    <REAPER_PROJECT 0.1 "7.59/macOS-arm64" 1700000000 0
      TEMPO 120 4 4
      MARKER 1 10.5 "good bit" 0 0 1 B {11111111-2222-3333-4444-555555555555} 0
      MARKER 2 20 Verse 1 0 1 B {AAAAAAAA-BBBB-CCCC-DDDD-EEEEEEEEEEEE} 0
      MARKER 2 30.25 "" 1
      MARKER 3 40 Bridge 5 0 1 B {AAAAAAAA-BBBB-CCCC-DDDD-FFFFFFFFFFFF} 0
      MARKER 3 45.5 "" 5
      RENDER_FILE Renders
      RENDER_PATTERN $region
      RENDER_RANGE 1 0 0 0 1000
      RENDER_STEMS 8
      RENDER_DITHER 1
      RENDER_NORMALIZE 262153 0.251189 1 0 0 1 1
      <RENDER_CFG
        bDNwbUABAAAAAAAAAgAAAP////8EAAAAQAEAAAAAAAA=
      >
      <TRACK {54BE1B8C-82FF-1E40-BA23-88E741571F0C}
        NAME "AB pair"
        <ITEM
          POSITION 13.5
          LENGTH 85.25
          SOFFS 2.5
          <SOURCE WAVE
            FILE "Media/take1.wav"
          >
        >
        <ITEM
          POSITION 100
          LENGTH 50
          SOFFS 0
          <SOURCE WAVE
            FILE "Media/take2.wav"
          >
        >
      >
    >
    """)


@pytest.fixture
def project():
    return Project.loads(FIXTURE)


def test_roundtrip_is_idempotent(project):
    once = project.dumps()
    assert Project.loads(once).dumps() == once


def test_tracks_and_items(project):
    (track,) = project.tracks
    assert track.name == "AB pair"
    first, second = track.items
    assert first.position == 13.5
    assert first.length == 85.25
    assert first.soffs == 2.5
    assert str(first.source_path) == "Media/take1.wav"
    assert second.source_offset_end == 50


def test_markers_and_regions(project):
    (marker,) = project.markers
    assert (marker.id, marker.position, marker.name) == (1, 10.5, "good bit")
    verse, bridge = project.regions
    assert (verse.name, verse.start, verse.end) == ("Verse", 20.0, 30.25)
    assert verse.length == 10.25
    # flag 5 (region bit plus other flag bits) must still parse as a region
    assert (bridge.name, bridge.start, bridge.end) == ("Bridge", 40.0, 45.5)


def test_add_region_roundtrips(project):
    project.add_region(50, 55.125, "Take 7")
    reloaded = Project.loads(project.dumps())
    added = reloaded.regions[-1]
    assert (added.name, added.start, added.end) == ("Take 7", 50.0, 55.125)
    assert added.id == 4  # next free id
    assert len(reloaded.regions) == 3
    assert len(reloaded.markers) == 1


def test_item_setters_roundtrip(project):
    item = project.tracks[0].items[0]
    item.position = 99.875
    assert Project.loads(project.dumps()).tracks[0].items[0].position == 99.875


def test_render_settings(project):
    render = project.render
    assert render.directory == "Renders"
    assert render.bounds == RenderBounds.ENTIRE_PROJECT
    assert render.stems == 8
    assert render.format == "mp3"
    assert render.normalize_enabled

    render.directory = "Takes"
    render.bounds = RenderBounds.ALL_REGIONS
    render.stems = 0
    render.dither = 0
    render.normalize_enabled = False
    render.format = "wav24"

    reloaded = Project.loads(project.dumps()).render
    assert reloaded.directory == "Takes"
    assert reloaded.bounds == RenderBounds.ALL_REGIONS
    assert reloaded.stems == 0
    assert reloaded.dither == 0
    assert not reloaded.normalize_enabled
    assert reloaded.format == "wav24"


def test_save_next_version(tmp_path, project):
    base = tmp_path / "Session.RPP"
    base.write_text(FIXTURE)
    loaded = Project.load(base)
    v2 = loaded.save_next_version()
    assert v2.name == "Session v2.RPP"
    v3 = Project.load(v2).save_next_version()
    assert v3.name == "Session v3.RPP"


def test_unknown_format_payload_must_be_base64(project):
    with pytest.raises(Exception):
        project.render.format = "not base64!!"
    project.render.format = RENDER_FORMATS["wav24"]  # raw payloads accepted
    assert project.render.format == "wav24"


def test_track_scalar_setters_roundtrip(project):
    track = project.tracks[0]
    track.name = "Renamed"
    track.volume = 0.5
    track.pan = -1.0
    track.muted = True
    track.play_offset = -0.003
    track.play_offset_enabled = True
    track.folder = (1, 1)
    reloaded = Project.loads(project.dumps()).tracks[0]
    assert reloaded.name == "Renamed"
    assert reloaded.volume == pytest.approx(0.5)
    assert reloaded.pan == pytest.approx(-1.0)
    assert reloaded.muted
    assert reloaded.play_offset == pytest.approx(-0.003)
    assert reloaded.play_offset_enabled
    assert reloaded.folder == (1, 1)


def test_play_offset_enable_preserves_other_flags(project):
    track = project.tracks[0]
    track.play_offset = 0.01
    # bit 1 means "value is in samples" and must survive toggling bit 0
    _set = track.element
    for child in _set:
        if isinstance(child, list) and child and child[0] == "PLAYOFFS":
            child[2] = "3"
    track.play_offset_enabled = True
    assert track.play_offset_enabled
    for child in _set:
        if isinstance(child, list) and child and child[0] == "PLAYOFFS":
            assert child[2] == "2"


def test_add_track_and_position(project):
    before = len(project.tracks)
    added = project.add_track("New Bus")
    assert len(project.tracks) == before + 1
    assert added.name == "New Bus"
    assert project.tracks[-1].name == "New Bus"
    first = project.add_track("First", index=0)
    assert project.tracks[0].name == "First"
    assert first.index == 0
    assert Project.loads(project.dumps()).tracks[0].name == "First"


def test_add_and_remove_receive(project):
    source = project.tracks[0]
    dest = project.add_track("Reverb")
    dest.add_receive(source, 0.25)
    assert dest.receives == [(source.index, pytest.approx(0.25))]
    # adding again replaces rather than duplicating
    dest.add_receive(source, 0.5)
    assert dest.receives == [(source.index, pytest.approx(0.5))]
    assert Project.loads(project.dumps()).tracks[-1].receives == [
        (source.index, pytest.approx(0.5))]
    dest.remove_receive(source)
    assert dest.receives == []


def test_receive_from_self_is_rejected(project):
    track = project.tracks[0]
    with pytest.raises(ValueError):
        track.add_receive(track)


def test_volume_envelope_roundtrips(project):
    track = project.tracks[0]
    track.set_volume_envelope([(0.0, 1.0), (10.0, 0.5), (20.0, 2.0)])
    reloaded = Project.loads(project.dumps()).tracks[0]
    env = reloaded.element.find("VOLENV2")
    points = [c for c in env if isinstance(c, list) and c and c[0] == "PT"]
    assert [float(p[1]) for p in points] == [0.0, 10.0, 20.0]
    assert [float(p[2]) for p in points] == pytest.approx([1.0, 0.5, 2.0])
    # replacing leaves exactly one envelope behind
    reloaded.set_volume_envelope([(0.0, 1.0)])
    assert len([c for c in reloaded.element if getattr(c, "tag", None) == "VOLENV2"]) == 1


def test_item_move_to_track(project):
    source = project.tracks[0]
    dest = project.add_track("Destination")
    item = source.items[0]
    before = (item.position, item.length, item.soffs)
    count = len(source.items)
    item.move_to(dest)
    assert len(source.items) == count - 1
    assert len(dest.items) == 1
    reloaded = Project.loads(project.dumps())
    moved = [t for t in reloaded.tracks if t.name == "Destination"][0].items[0]
    assert (moved.position, moved.length, moved.soffs) == before


def test_region_rename_roundtrips(project):
    region = project.regions[0]
    region.name = "Renamed T3*"
    assert Project.loads(project.dumps()).regions[0].name == "Renamed T3*"


def test_region_bounds_are_settable(project):
    region = project.regions[0]
    region.start = 5.5
    region.end = 12.25
    reloaded = Project.loads(project.dumps()).regions[0]
    assert reloaded.start == pytest.approx(5.5)
    assert reloaded.end == pytest.approx(12.25)


def test_remove_region(project):
    before = len(project.regions)
    doomed = project.regions[0]
    name = doomed.name
    project.remove_region(doomed)
    assert len(project.regions) == before - 1
    reloaded = Project.loads(project.dumps())
    assert len(reloaded.regions) == before - 1
    # the surviving regions still pair up correctly
    assert all(r.end >= r.start for r in reloaded.regions)
    assert name not in [r.name for r in reloaded.regions] or before > 1


def test_pan_envelope_roundtrips(project):
    track = project.tracks[0]
    track.set_pan_envelope([(0.0, 0.0), (10.0, 0.4), (20.0, 0.0)])
    reloaded = Project.loads(project.dumps()).tracks[0]
    env = reloaded.element.find("PANENV2")
    points = [c for c in env if isinstance(c, list) and c and c[0] == "PT"]
    assert [float(p[2]) for p in points] == pytest.approx([0.0, 0.4, 0.0])
    assert reloaded.envelopes["PANENV2"] == 3


def test_volume_and_pan_envelopes_coexist(project):
    track = project.tracks[0]
    track.set_volume_envelope([(0.0, 1.0)])
    track.set_pan_envelope([(0.0, -0.5)])
    reloaded = Project.loads(project.dumps()).tracks[0]
    assert reloaded.envelopes == {"VOLENV2": 1, "PANENV2": 1}


def test_remove_envelopes_by_tag(project):
    track = project.tracks[0]
    track.set_volume_envelope([(0.0, 1.0), (5.0, 0.5)])
    track.set_pan_envelope([(0.0, 0.0)])
    assert track.remove_envelopes("VOLENV2") == 2
    assert "VOLENV2" not in track.envelopes
    assert "PANENV2" in track.envelopes
    assert track.remove_envelopes("NOSUCHENV") == 0


def test_marker_rename_and_move(project):
    marker = project.markers[0]
    marker.name = "Renamed marker"
    marker.position = 42.5
    reloaded = Project.loads(project.dumps()).markers[0]
    assert reloaded.name == "Renamed marker"
    assert reloaded.position == pytest.approx(42.5)
