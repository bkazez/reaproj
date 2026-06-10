import textwrap

import pytest

from reaproj import Project, RenderBounds, RENDER_FORMATS

FIXTURE = textwrap.dedent("""\
    <REAPER_PROJECT 0.1 "7.59/macOS-arm64" 1700000000 0
      TEMPO 120 4 4
      MARKER 1 10.5 "good bit" 0 0 1 B {11111111-2222-3333-4444-555555555555} 0
      MARKER 2 20 Verse 1 0 1 B {AAAAAAAA-BBBB-CCCC-DDDD-EEEEEEEEEEEE} 0
      MARKER 2 30.25 "" 1
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
    (region,) = project.regions
    assert (region.name, region.start, region.end) == ("Verse", 20.0, 30.25)
    assert region.length == 10.25


def test_add_region_roundtrips(project):
    project.add_region(40, 55.125, "Take 7")
    reloaded = Project.loads(project.dumps())
    added = reloaded.regions[-1]
    assert (added.name, added.start, added.end) == ("Take 7", 40.0, 55.125)
    assert added.id == 3  # next free id
    assert len(reloaded.regions) == 2
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
