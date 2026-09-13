# Building the hydra components

Generates one TouchDesigner component per [hydra](https://hydra.ojack.xyz/api/)
function, plus an FFT component, from `hydra-functions.json`.

Research and design rationale: `claude/HYDRA_RESEARCH.md`.

## Files

| file | what it is |
|---|---|
| `hydra-functions.json` | the 52 function specs, extracted from hydra's `glsl-functions.js` |
| `generate-hydra-functions.sh` | regenerates that JSON from hydra's source |
| `emit_frags.py` | writes `shader/*.frag` from the JSON |
| `build_hydra.py` | builds the TD components from the JSON + shaders |
| `fft_chop.py` | Script CHOP callbacks: hydra's `a.fft[n]` |
| `time_exec.py` | Parameter Execute callbacks, embedded into time-using components |
| `hydra_seq.py` | hydra's array arguments (`[1,2,3].fast().ease()`) — optional |
| `shader/*.frag` | **generated** — do not hand edit |
| `shader/_utils.glsl` | hydra's `_noise`, `_luminance`, `_rgbToHsv`, `_hsvToRgb` |

## One-time setup in TouchDesigner

Make a container — `/project1/hydra` below — and add Text DATs synced to the
Python files. For each: set `file`, turn on **Sync to File** and **Load on Start**.

| DAT name | file |
|---|---|
| `build_hydra` | `assets/scripts/hydra/build_hydra.py` |
| `emit_frags` | `assets/scripts/hydra/emit_frags.py` |
| `hydra_seq` | `assets/scripts/hydra/hydra_seq.py` (only if you want array args) |

Paths are relative to the `.toe`, so keep them relative — they stay portable.

`build_hydra` and `emit_frags` are *imported*, not run: use the Textport, not
Run Script.

## Commands

From the Textport:

```python
b = mod('/project1/hydra/build_hydra')

b.build_all(op('/project1/hydra'))    # build everything (replaces same-named)
b.rebuild(op('/project1/hydra'))      # wipe all generated ops, then build
b.clear(op('/project1/hydra'))        # wipe only
b.build_audio(op('/project1/hydra'))  # just hydra_fft
b.export_tox(op('/project1/hydra'))      # write the .tox tree
b.export_palette(op('/project1/hydra'))  # write into the TD user palette
b.layout_groups(op('/project1/hydra'), b.load_specs())   # re-arrange only
b.build(spec, op('/project1/hydra'))  # a single component, from one spec dict
```

`build_all` and `rebuild` take `layout=False`, `audio=False`, `tox=False`,
`palette=False` to skip those stages.

> `clear` and `rebuild` destroy **every** `hydra_*` component and **every**
> Annotate COMP in the target container, including annotations you added by
> hand. Keep this container for generated content only.

`build_all` replaces components one by one, so it leaves behind components whose
function was renamed or removed upstream. `rebuild` does not.

To regenerate the shaders after a hydra update:

```bash
./assets/scripts/hydra/generate-hydra-functions.sh   # refresh the JSON
python3 assets/scripts/hydra/emit_frags.py           # rewrite shader/*.frag
```

then `b.rebuild(...)` in TD. Or from the Textport:
`mod('/project1/hydra/emit_frags').emit_all()`.

## Exported .tox files

Every build also writes each component to disk, one folder per group:

```
components/hydra/
  source/    hydra_osc.tox, hydra_noise.tox, …
  geometry/  hydra_rotate.tox, …
  color/     …
  blend/     …
  modulate/  …
  audio/     hydra_fft.tox
```

Existing files are overwritten, so the tree always matches the last build. Drag
one into any project to use a single function without the builder.

A `.tox` embeds its internals, including the shader **text** as it stood at save
time — but the Text DATs keep their `file` + Sync to File pars, so a component
dropped into another project re-reads `assets/scripts/hydra/shader/*.frag`
*relative to that project's* `.toe`. Either keep the same folder layout, or turn
Sync to File off in the exported copies to freeze the shaders.

Renaming a function upstream leaves its old `.tox` behind — export only writes,
it never deletes. Clear the tree by hand when that happens.

## The TouchDesigner palette

The same export also writes into the user palette, so the components show up
under **My Components / Hydra / <Group> / hydra_<func>**:

```python
b.export_palette(op('/project1/hydra'))
b.export_palette(op('/project1/hydra'), root='/some/other/Palette')
```

The palette is a plain folder tree — subfolders become categories. `palette_root()`
resolves it per install, never hardcoded: `app.userPaletteFolder`, then
`<app.configFolder>/Palette`, then `~/Documents/Derivative/Palette`, printing every
candidate it tried if none exist. On macOS this usually lands at
`~/Library/Application Support/Derivative/TouchDesigner<version>/Palette`, so it
follows both the user and the TD version. Pass `root=` to override.

**Refresh the Palette pane** after a build; it does not watch the folder.

## What a generated component looks like

```
hydra_osc/
  source, modulator   In TOPs     image inputs, class-dependent
  frequency, sync…    In CHOPs    one per numeric argument
  frequency_default   Constant CHOPs, feeding each In CHOP's fallback input
  pixel               Text DAT    file-synced to shader/<name>.frag
  glsl                GLSL TOP    the internals
  time_exec           Parameter Execute DAT, time-using functions only
  out                 Out TOP     also the component's Operator Viewer
  + custom page 'Hydra'       one parameter per argument
  + custom page 'Time Sync'   time-using functions only
```

**Arguments resolve in one of two ways.** Connect a CHOP to an argument's
connector and it wins; connect nothing and the component falls back to the
custom parameter, via a Constant CHOP on the In CHOP's internal input. Channel
names on the connected CHOP are ignored — only the connector matters.

Parameters accept expressions like anything else in TD, including
`mod('/project1/hydra/hydra_seq').val([10, 20, 30], ease='easeInOutCubic')`
for hydra's array arguments. CHOPs are usually the better tool.

**Connector order** is image inputs first, then arguments in hydra's declared
order, set through each In OP's *Connect Order*.

## Time

The 10 functions whose shader reads `time` — `osc`, `noise`, `voronoi`,
`gradient`, `rotate`, `scroll`, `scrollX`, `scrollY`, `modulateScrollX`,
`modulateScrollY` — get a **Time Sync** page carrying a **time** parameter that
holds the expression `absTime.seconds`. It lives on its own page so the `Hydra`
page stays purely the function's arguments. It is a parameter and not an input,
since rewiring it is rare;
replace the expression to run a chain on a Timer CHOP, a scrubbed value, a
slowed clock, or anything else.

**Propagate Time Expr** (pulse) copies this component's Time — its expression if
it has one, otherwise its constant — onto every downstream **hydra** component
that has a Time parameter, following output connections breadth first. Set the
clock once on the first node of a chain, pulse, and the whole chain follows.

It only writes into components carrying the `hydra` tag (see below), so an
unrelated operator downstream that happens to have a parameter called Time is
left alone. The pulse prints what it touched.

The callback lives in a `time_exec` Parameter Execute DAT inside each such
component, with the code embedded rather than file-synced, so exported `.tox`
files carry it. `time_exec.py` is the editable master — change it and rebuild.

Resetting the Time parameter to its default gives you `0`, not the expression;
re-enter `absTime.seconds` or rebuild the component.

**Input 1 of a `combineCoord` is the modulator** — `a.modulate(b)` puts `b`
there. The In TOPs are named `source` and `modulator` for this reason.

## Groups

Components are coloured and annotated by hydra's documentation groups, which map
exactly onto hydra's five GLSL classes:

| group | class | count |
|---|---|---|
| Source | `src` | 8 |
| Geometry | `coord` | 10 |
| Color | `color` | 16 |
| Blend | `combine` | 7 |
| Modulate | `combineCoord` | 11 |
| Audio | — | 1 (`hydra_fft`) |

Annotation headers use the node colour (`op.color`); the body uses `Backcolor*`
at 60% value — same hue, darker.

## Tags

Every generated component is tagged, and tags survive a `.tox` save/load:

| tag | meaning |
|---|---|
| `hydra` | generated by this builder |
| `hydra:<func>` | which function, e.g. `hydra:modulateScrollX` |
| `hydra:<class>` | which group, e.g. `hydra:combineCoord`, `hydra:audio` |

This is how **Propagate Time Expr** tells hydra components from everything else,
and how `clear` finds what to remove even if someone renamed a component. To find
them yourself:

```python
[o for o in op('/project1/hydra').children if 'hydra' in o.tags]
op('/project1/hydra').findChildren(tags=['hydra:coord'])
```

## hydra_fft

`a.fft[n]` as a component: audio CHOP in, `fft_0 … fft_<Bins-1>` plus `vol` out.
Wire a channel straight into any argument connector.

Ported from hydra's `audio.js`: amplitude spectrum → 24 Bark bands (each summed,
then `^0.23`) → grouped into `Bins` sums → one-pole smoothing →
`max(0, (bin - cutoff) / scale)`.

Two things to know:

- `floor(24 / Bins)` is hydra's grouping, so non-divisors **drop the top bands** —
  5 bins uses only 20 of 24 and loses everything above ~7 kHz. 4, 6, 8 and 12
  divide evenly.
- `Cutoff` and `Scale` will need retuning. Band shape matches Meyda exactly;
  absolute magnitudes depend on Meyda's internal normalisation. Watch `vol` with
  sound playing: set Cutoff just under its floor, Scale to roughly its range.

## Extending

`build_hydra.py` is data-driven — a new function needs only a JSON entry, as long
as it fits one of the five classes. Things worth knowing before editing:

- `GROUPS` / `GROUP_ORDER` — group labels, colours and vertical order.
- `EXTRA_MEMBERS` — components not in the JSON that still belong to a group
  (that's how `hydra_fft` joins Audio).
- `NAME_INPUTS` — per-function overrides for image inputs. `src` and `prev` are
  class `src` but still need a TOP input.
- `CELL_W` / `CELL_H` / `COLS` / `PAD` — layout grid.
- `find_par`, `set_menu`, `set_color_par` — all take candidate names and print
  what exists when nothing matches. TD parameter spellings vary between builds;
  when something silently doesn't apply, look for a `!!` line in the Textport.
