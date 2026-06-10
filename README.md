# reaproj

Parse and edit REAPER `.RPP` project files in Python: tracks, items, markers,
regions, and render settings as objects, instead of hand-editing chunk text.

Typical uses: add regions to a project programmatically, batch-change render
settings (output directory, `$region` naming, format) before a headless
`-renderproject` render, read item positions and source files for analysis,
or save versioned copies of a session.

Built on [rpp](https://github.com/Perlence/rpp) (the tokenizer/emitter);
reaproj adds the object model on top.

## Install

```
pip install reaproj
```

## Read a project

```python
from reaproj import Project

project = Project.load("Session.RPP")

for track in project.tracks:
    for item in track.items:
        print(item.position, item.length, item.soffs, item.source_path)

for region in project.regions:
    print(region.name, region.start, region.end)
```

## Add regions

```python
project.add_region(12.5, 95.0, "Take 1")   # id and GUID handled for you
```

## Configure rendering

```python
from reaproj import RenderBounds

project.render.directory = "Takes"
project.render.pattern = "$region"
project.render.bounds = RenderBounds.ALL_REGIONS
project.render.stems = 0                    # master mix
project.render.normalize_enabled = False
project.render.format = "wav24"             # or "mp3", or a raw RENDER_CFG base64 payload
```

## Save

```python
project.save()                  # in place
project.save("Other.RPP")       # elsewhere
project.save_next_version()     # "Session v2.RPP", "Session v3.RPP", ...
```

Then render headlessly:

```
REAPER -renderproject "Session v2.RPP"
```

## Alternatives

- [rpp](https://github.com/Perlence/rpp): the underlying low-level
  parser/emitter; gives you an ElementTree-like view of raw chunks. Use it
  directly when you need something reaproj doesn't model yet.
- [reathon](https://github.com/jamesb93/reathon): constructs new REAPER
  projects from scratch; not aimed at editing existing ones.
- [rppxml](https://github.com/IcEarthlight/rppxml): RPP parsing via the WDL
  implementation.
- [reapy](https://github.com/RomeoDespres/reapy): controls a running REAPER
  instance through the ReaScript API; requires REAPER to be open. reaproj
  works on the files themselves, no REAPER required.

## Fidelity

reaproj never touches content it doesn't understand; everything round-trips
through the element tree. Output differs from REAPER's own formatting only in
cosmetic quoting (quotes dropped on space-free strings), which REAPER parses
identically. Numeric values are preserved as strings, never reformatted.

## License

MIT
