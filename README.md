# reaproj

Semantic access to REAPER `.RPP` project files from Python: tracks, items,
markers, regions, and render settings, instead of hand-editing chunk text.

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

## Fidelity

reaproj never touches content it doesn't understand; everything round-trips
through the element tree. Output differs from REAPER's own formatting only in
cosmetic quoting (quotes dropped on space-free strings), which REAPER parses
identically. Numeric values are preserved as strings, never reformatted.

## License

MIT
