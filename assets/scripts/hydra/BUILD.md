# Building the hydra components

Generates one TouchDesigner component per [hydra](https://hydra.ojack.xyz/api/)
function, plus an FFT component, from `hydra-functions.json`.

Research and design rationale: `claude/HYDRA_RESEARCH.md`.

## Files

| file | what it is |
|---|---|
| `hydra-functions.json` | the 52 function specs, extracted from hydra's `glsl-functions.js` |
| `hydra-extensions.json` | deliberate additions to that table — merged over it at load |
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
b.upgrade(op('/project1'))               # repair placed copies after a shader change
b.export_tox(op('/project1/hydra'))      # write the .tox tree
b.export_palette(op('/project1/hydra'))  # write into the TD user palette
b.layout_groups(op('/project1/hydra'), b.load_specs())   # re-arrange only
b.build(spec, op('/project1/hydra'))  # a single component, from one spec dict
b.spawn(['osc', 'osc', 'rotate', 'scale'])               # copies to patch with
```

`build_all` and `rebuild` take `layout=False`, `audio=False`, `tox=False`,
`palette=False` to skip those stages.

> `clear` and `rebuild` destroy **every** `hydra_*` COMPONENT and **every**
> Annotate COMP in the target container, including annotations you added by
> hand. Keep this container for generated content only. Helper DATs are safe —
> only COMPs are destroyed, so `hydra_seq` survives despite its name.

## Upgrading placed copies

Shaders are file-synced and **shared by every copy in every patch**. Regenerating
them (a hydra update, or a new feature like coordinate mode) therefore changes
components you placed weeks ago — but a copy keeps its own uniform entries,
inputs and parameters, which then no longer match the shader. The symptom is a
checkerboard: the GLSL TOP failing to compile or reporting an unassigned uniform.

```python
b.upgrade(op('/project1'))      # searches recursively, repairs in place
```

It re-applies uniforms, adds missing inputs and parameters, and leaves wiring,
parameter values and node positions alone. It identifies each component by its
`hydra:<func>` tag, so renamed copies (`hydra_osc3`) are still recognised.

Run it after every `emit_frags` run that changes shader uniforms.

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
  + custom page 'Output'      resolution (sources), input smoothness, pixel format
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

## Spawning copies

The generated components are a library — patch with copies of them, not with the
originals:

```python
b.spawn(['osc', 'osc', 'rotate', 'scale'])
b.spawn(['osc', 'rotate'], postfix='_a')       # hydra_osc_a, hydra_rotate_a
b.spawn(['noise'], dest=op('/project1/sketch2'))
b.spawn(['osc'], lib=op('/some/other/hydra'))  # a different library
```

Repeats in the list are fine — each copy gets a numeric suffix, so
`['osc', 'osc']` yields `hydra_osc` and `hydra_osc1`. `postfix` lands before that
suffix.

`spawn` returns the created components, so you can wire them up directly:

```python
a, b_, r = b.spawn(['osc', 'noise', 'modulate'])
r.inputConnectors[0].connect(a)
r.inputConnectors[1].connect(b_)
```

**Where copies land.** `dest` defaults to the *parent* of the library, not the
library itself — copies inherit the `hydra` tag, so a `clear()` or `rebuild()` on
the library container would destroy them along with the originals. Keep them
apart.

**Which library.** `build_all` remembers its target, so `spawn` usually needs no
`lib`. After a TD restart that memory is gone and it searches the project for
tagged components instead; pass `lib=` if it guesses wrong.

## Resolution

Source functions (`osc`, `noise`, `shape`, `voronoi`, `gradient`, `solid`) have
nothing upstream to inherit a resolution from, so they get an **Output** page with
a `resolution` parameter, defaulting to `DEFAULT_RES` — 1280×720. Every other
class follows its input, so setting the sources sets the chain.

Retune a whole container at once:

```python
b.set_resolution(op('/project1/hydra'), 1280, 1280)
```

**A non-commercial licence caps output at 1280×1280**, so that is the practical
ceiling here; `set_resolution` warns when you ask for more. Change `DEFAULT_RES`
in `build_hydra.py` to move the default for new builds.

Anything else feeding a chain — a Constant TOP seeding a Feedback TOP, for
instance — has to be set to match by hand, or it resamples.

## Input Smoothness and Pixel Format

TD does not expose a COMP's internal TOP Common parameters, so the **Output** page
mirrors the two that matter, bound to the inner GLSL TOP:

| parameter | on | mirrors |
|---|---|---|
| `resolution` | sources only | `resolutionw` / `resolutionh` |
| `input smoothness` | everything with an image input | `inputfiltertype` |
| `pixel format` | everything | `format` |

Menus are cloned from the GLSL TOP, so the entries are TD's own. Sources have no
image input, so they get no smoothness control — how a source is read is decided
by whoever samples it, i.e. the next component down.

Set them across a container:

```python
b.set_output(op('/project1/hydra'), smoothness='nearest')
b.set_output(op('/project1/hydra'), pixel_format='8-bit')
```

Matching is by substring against the menu text, and a miss prints the available
options.

**When this matters: feedback loops.** Hydra's output buffers are created with
`mag: 'nearest'` and 8-bit RGBA (`output.js`). At TD's default interpolation, a
sub-pixel scroll inside a feedback loop blends neighbouring texels every frame —
a running blur that diffuses detail across the whole frame, where hydra's nearest
sampling copies it back untouched and keeps it crisp. 16-bit float compounds this
by keeping residues alive that 8-bit would quantize to zero.

The tension: 16-bit float is right when `noise()` feeds `modulate()` (negative
values survive), and wrong for hydra-exact feedback. Pick per patch.

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

## Coordinate mode

Hydra does not rotate a picture of `osc` — it rotates the *coordinate* and then
evaluates `osc` there, analytically, over an unbounded domain. A node graph
rasterizes each stage, so `hydra_rotate` sampling `hydra_osc` runs off the edge of
a finite texture and wraps, leaving a seam. `osc(9,-0.3,900).rotate(6)` shows it
plainly: continuous diagonal stripes in hydra, a hard diagonal break here.

**Coordinate mode** restores hydra's model. `coord` components transform a
*coordinate map* (RG = `st`) instead of sampling an image, and `src` components
read their `st` from one — so the source is evaluated at the final coordinates,
however far outside 0..1 they land.

```
hydra_coords ──→ hydra_rotate ──→ hydra_osc ──→ …
(identity st)     Coordmode on     Coordmode on
```

- **`hydra_coords`** is the identity map that seeds the chain, in the Source group.
- **`coordinate mode`** (Output page) switches a component between image and
  coordinate handling. Off by default: existing patches are untouched.
- Sources gain a **`coords`** input, after their image inputs.
- **Use 32-bit float** on every TOP in the coordinate path. Coordinates leave
  0..1 immediately and anything fixed-point clips them.

**Coord nodes wire in reverse of the hydra chain.** Hydra emits
`st = c_last(st); … st = c_first(st); src(st)`, so the last coord op in the sketch
runs first. `osc().rotate().scale()` becomes
`coords → scale → rotate → osc`. Faithful to evaluation order, backwards from how
it reads.

Not covered: `combineCoord` (the `modulate*` family) stays image-only for now —
its modulator is an image sampled mid-chain, which needs its own design pass.

## Extensions

`hydra-extensions.json` adds inputs hydra itself does not have. Both `emit_frags.py`
and `build_hydra.py` merge it over `hydra-functions.json` at load, so regenerating
the upstream table never discards it.

Today it gives **`layer`, `diff` and `mask` an `amount`**, which hydra provides for
`add`, `sub`, `mult` and `blend` but not for those three. It follows hydra's own
convention — `mix(_c0, result, amount)`, i.e. fade the result back toward the
source — and **defaults to 1, which is exactly hydra's behaviour**. Only a value
below 1 diverges from upstream.

An input marked `"extension"` is not passed to the hydra function; the emitter
applies it at the call site, so the function body stays verbatim:

```glsl
vec4 result = diff(c0, c1);
fragColor = TDOutputSwizzle(mix(c0, result, amount));
```

The builder needs no special case — it sees a normal `float` input and makes the
parameter, the Constant default and the CHOP connector like any other.

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
| `hydra:fn:<func>` | which function, e.g. `hydra:fn:modulateScrollX` |
| `hydra:class:<class>` | which group, e.g. `hydra:class:combineCoord` |

The `fn:`/`class:` namespacing is load-bearing. An earlier scheme wrote both as
bare `hydra:<value>`, and `hydra:src` — the class tag on every source — is then
indistinguishable from the *function* named `src`. Components were identified by
whichever tag the set yielded first, so an `osc` could be read as a `src` and get
that shader's uniform list: no `time`, no arguments, everything unassigned, white
output. `upgrade` retags to the namespaced form and identifies by node name first.

This is how **Propagate Time Expr** tells hydra components from everything else,
and how `clear` finds what to remove even if someone renamed a component. To find
them yourself:

```python
[o for o in op('/project1/hydra').children if 'hydra' in o.tags]
op('/project1/hydra').findChildren(tags=['hydra:class:coord'])
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
