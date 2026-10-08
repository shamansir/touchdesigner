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
| `hydra-utils.glsl` | hydra's `_noise`, `_luminance`, `_rgbToHsv`, `_hsvToRgb`, verbatim |
| `build_hydra.py` | builds and upgrades the TD components from the JSON |
| `hydra_compile.py` | generates each component's shader, and every callback — embedded into every component; the builder loads it too, for the facts both share |
| `fft_chop.py` | Script CHOP callbacks: hydra's `a.fft[n]` |
| `tests/` | the compiler and the builder's TD-free parts, against a mock graph |
| `hydra_seq.py` | hydra's array arguments (`[1,2,3].fast().ease()`) — optional |

There are no shader files: each component stores its function on itself and
generates its shader from that (see **Modes**).

## One-time setup in TouchDesigner

Make a container — `/project1/hydra` below — and add Text DATs synced to the
Python files. For each: set `file`, turn on **Sync to File** and **Load on Start**.

| DAT name | file |
|---|---|
| `build_hydra` | `assets/scripts/hydra/build_hydra.py` |
| `hydra_seq` | `assets/scripts/hydra/hydra_seq.py` (only if you want array args) |

Paths are relative to the `.toe`, so keep them relative — they stay portable.

`build_hydra` is *imported*, not run: use the Textport, not Run Script.

## Commands

From the Textport:

```python
b = mod('/project1/hydra/build_hydra')

b.build_all(op('/project1/hydra'))    # build everything (replaces same-named)
b.rebuild(op('/project1/hydra'))      # wipe all generated ops, then build
b.clear(op('/project1/hydra'))        # wipe only
b.build_audio(op('/project1/hydra'))  # just hydra_fft
b.upgrade(op('/project1'))               # bring placed copies up to the current code
b.export_tox(op('/project1/hydra'))      # write the .tox tree
b.export_palette(op('/project1/hydra'))  # write into the TD user palette
b.layout_groups(op('/project1/hydra'), b.load_specs())   # re-arrange only
b.build(spec, op('/project1/hydra'))  # a single component, from one spec dict
b.spawn(['osc', 'osc', 'rotate', 'scale'])               # copies to patch with
b.set_mode(op('/project1/sketch'), 'image')  # Mode on every placed component
b.cook_report(op('/project1'))           # GPU/CPU cook time per component
b.version_report(op('/project1'))        # components not built from the current code
b.dump_shaders(op('/project1'))          # write every generated shader to build/hydra-shaders
```

`build_all` and `rebuild` take `layout=False`, `audio=False`, `tox=False`,
`palette=False` to skip those stages.

> `clear` and `rebuild` destroy **every** `hydra_*` COMPONENT and **every**
> Annotate COMP in the target container, including annotations you added by
> hand. Keep this container for generated content only. Helper DATs are safe —
> only COMPs are destroyed, so `hydra_seq` survives despite its name.

## Upgrading placed copies

A placed copy carries its own internals — parameters, In OPs, the embedded
compiler, the stored function spec — so a change to the builder or the compiler
does not reach it by itself.

```python
b.upgrade(op('/project1'))      # searches recursively, repairs in place
b.version_report(op('/project1'))   # what is still stale
```

`upgrade` runs the same `ensure()` as a fresh build: it creates whatever the copy
lacks, rewrites what is generated (spec, compiler, callbacks, shader), and keeps
wiring, parameter values and node positions. It identifies each component by the
spec stored on it, else its `hydra:fn:` tag — never by name, so renamed copies are
recognised.

`build_all` replaces components one by one, so it leaves behind components whose
function was renamed or removed upstream. `rebuild` does not.

After a hydra update, refresh the JSON and rebuild:

```bash
./assets/scripts/hydra/generate-hydra-functions.sh   # refresh the JSON
```

then `b.rebuild(...)` and `b.upgrade(...)` in TD.

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

A `.tox` is self-contained: the function spec, the compiler and every callback
live inside it, and nothing is file-synced. Drop it into any project.

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
  pixel               Text DAT    the generated shader -- not a file
  glsl                GLSL TOP    the internals
  out                 Out TOP     also the component's Operator Viewer
  boundary0, …        Select TOPs textures from further up a compiled chain
  compile             Text DAT    hydra_compile.py, embedded
  chain_exec          OP Execute DAT: rewire / rename / Viewer flag
  par_exec            Parameter Execute DAT: Mode, Compile, Propagate Time Expr
  storage hydra_spec  the function: body, inputs, utilities -- what `compile` reads
  + custom page 'Hydra'       one parameter per argument
  + custom page 'Pipeline'    mode (Compiled / Image), Compile
  + custom page 'Time Sync'   time-using functions only
  + custom page 'Output'      resolution (sources), input smoothness, pixel format
  + custom page 'Version'     version, compiler hash, build time -- read only

Pages always appear in that order (`PAGE_ORDER`), whichever of them a given
function happens to have.
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
b.spawn(['osc*3', 'rotate'])                   # counts
b.spawn('osc*3 rotate scale')                  # or one string
b.spawn(['osc', 'rotate'], postfix='_a')       # hydra_osc_a, hydra_rotate_a
b.spawn(['noise'], dest=op('/project1/sketch2'))
b.spawn(['osc'], lib=op('/some/other/hydra'))  # a different library
```

`name*count` repeats a name; a whitespace-separated string works in place of a list.

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
a `resolution` parameter, defaulting to `DEFAULT_RES` — 1280×720. Everything
else takes the resolution of its chain's root source (Compiled) or of its input
(Image), so setting the sources sets the chain.

Retune every source under a container, nested ones included:

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

Set them on every component under a container, nested ones included:

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

The code is `propagate_time` in `hydra_compile.py`, reached through the
component's `par_exec` — embedded, so exported `.tox` files carry it.

Resetting the Time parameter to its default gives you `0`, not the expression;
re-enter `absTime.seconds` or rebuild the component.

**Input 1 of a `combineCoord` is the modulator** — `a.modulate(b)` puts `b`
there. The In TOPs are named `source` and `modulator` for this reason.

## Modes

Every component has a **Mode** on its **Pipeline** page. **Compiled** is the
default; **Image** is the exception.

```
osc ──→ rotate ──→ scale        all Compiled: scale's shader evaluates
                                osc(rotate(scale(st))), as hydra does
```

**Compiled** is hydra's own model, in hydra's wiring order. The component walks
the wiring upstream and fuses every Compiled hydra component it finds into one
shader, the way hydra's `generate-glsl.js` does. The source is evaluated at the
final coordinate, so nothing wraps, nothing seams, nothing is resampled, and
`fract` happens once, inside `src`/`prev`. Any pixel format works.

**Image** renders this component on its own and makes it a texture for
everything downstream — a render-here point. Use it where re-evaluating is
wasteful: a slow source shared by several branches (Compiled evaluates it once per
branch, per pixel), or one that does not change over time — rendered as a
texture, it cooks only when its parameters change. Downstream of it, coordinate
ops sample a finite texture again, so they can seam; keep Image for sources and
color work, not ahead of `rotate`/`scale`/`kaleid`.

```python
b.set_mode(op('/project1/sketch'), 'compiled')   # or 'image'
```

**Boundaries.** Anything a compiled chain does not inline is read as a texture,
`texture(t, fract(uv))` — exactly what hydra's `src()` does:

- an Image-mode component
- whatever feeds `src` or `prev` — in hydra that is a buffer, not a chain
- anything that is not a hydra component (a Feedback TOP, a Movie File In, …)

The GLSL TOP has no Samplers page, so a boundary arrives on one of its inputs: the
component's own In TOP when the texture is wired into this component, a Select
TOP (`boundary0`, …) pointing at another component's In TOP when it enters further
up the chain.

**What gets compiled.** Image components always. Compiled ones only when
something looks at them:

- **Viewer on** — its node viewer shows the chain up to there, exactly.
- **An output** — its texture is read by something outside a compiled chain: a
  non-hydra operator, an Image-mode component, a `src` input, or nothing at all
  (end of a chain).

Everything else keeps its last shader and, with nothing pulling on it, never
cooks. A viewed intermediate costs a full pass of the chain above it, every frame.

**When it recompiles.** On rewiring (the component and everything downstream),
renaming, a Viewer flag, a Mode change, or the **Compile** pulse. Triggers within
one frame are collapsed, and an identical result is not reinstalled. Parameter and
CHOP changes need no recompile — every argument is a uniform expression pointing
at its own component's In CHOP.

**The generated shader.** In `pixel`. `main()` is one statement per value:

```glsl
vec2 uv0 = scale_0(st, u0_amount, …);
vec2 uv1 = rotate_1(uv0, u1_angle, u1_speed);
vec4 c2 = osc_2(uv1, u2_frequency, u2_sync, u2_offset);
```

Hydra itself nests one expression, which repeats the incoming coordinate in every
`modulate*` and so grows as 2^k for k chained modulates. Statements keep it linear,
and a component used twice at the same coordinate is evaluated once. Every
function is pure, so the result is identical.

**Time.** Each inlined function keeps its own component's Time
(`#define time u<k>_time` around its body). With every Time equal — the default, or
after Propagate Time Expr — this is hydra's one global `time`.

**Paths.** Uniforms and Select TOPs refer to other components by *relative* path,
so copying a whole sketch container keeps every chain pointing inside the copy.
Renames recompile by themselves.

**Comparing speed.** `b.cook_report(op('/project1/sketch'))` prints last-frame GPU
and CPU cook time per component. Components that did not cook report 0.

The trigger callbacks rely on OP Execute toggles (`wirechange`, `flagchange`,
`namechange`, `pathchange`).

## Tests

```bash
python3 -m unittest discover assets/scripts/hydra/tests
```

`tests/td_mock.py` models just enough of TouchDesigner — operators, connectors,
storage — to run the compiler on real hydra specs. Its paths are deliberately
opaque: `relativePath()` returns a token like `@12`, and only exact tokens resolve.
TD's rules for combining relative paths are not ours to rely on, so any path the
code builds instead of asking for fails, the way 0.2.0's did in TD.

Run them before bumping `VERSION`. They do not replace a check in TD: the GLSL is
checked for shape (balanced braces, every uniform used, no duplicate functions,
no macro name clashing with a hydra body), not compiled.

## Generated shaders on disk

```python
b.dump_shaders(op('/project1'))                      # -> build/hydra-shaders/
b.dump_shaders(op('/project1/sketch'), root='/some/folder')
```

Writes each component's `pixel` text to `<path below dest>.frag`, e.g.
`khoparzi1__hydra_add.frag`. Export only — nothing reads them back, and the folder
is git-ignored. For reading, diffing two versions, or a GLSL validator. A Compiled
component nothing looks at keeps its last shader, which may be stale; each file's
header names what it was generated for.

## Versions

Every component carries a read-only **Version** page:

| field | what it is |
|---|---|
| `version` | `VERSION` in `build_hydra.py` — bumped by hand on every change to the builder, the compiler or `hydra-utils.glsl` |
| `compiler` | first 8 hex digits of the SHA-1 of `hydra_compile.py` — the embedded `compile` DAT; `-` on components without one |
| `built` | when this copy was built or last upgraded |

`build` and `upgrade` stamp it; spawned copies inherit the library's. The compiler
hash changes by itself, so it catches a copy that missed an `upgrade` even when
`VERSION` was not bumped.

```python
b.version_report(op('/project1'))   # lists every stale copy; empty = all current
```

When reporting a problem, the version and compiler hash of the component
involved say exactly which code it runs.

## Extensions

`hydra-extensions.json` adds inputs hydra itself does not have. `build_hydra.py`
merges it over `hydra-functions.json` at load, so regenerating the upstream table
never discards it.

Today it gives **`layer`, `diff` and `mask` an `amount`**, which hydra provides for
`add`, `sub`, `mult` and `blend` but not for those three. It follows hydra's own
convention — `mix(_c0, result, amount)`, i.e. fade the result back toward the
source — and **defaults to 1, which is exactly hydra's behaviour**. Only a value
below 1 diverges from upstream.

An input marked `"extension"` is not passed to the hydra function; the compiler
wraps the call instead, so the function body stays verbatim:

```glsl
vec4 diff_1_x(vec4 _c0, vec4 _c1, float amount) {
   return mix(_c0, diff_1(_c0, _c1), amount);
}
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

The `fn:`/`class:` namespacing is load-bearing: a bare `hydra:src` could be the
class of every source or the *function* named `src`.

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
- `CLASS_INPUTS` / `TEXTURE_INPUT` in `hydra_compile.py` — image inputs per class;
  `src` and `prev` are class `src` but still read a texture.
- `CELL_W` / `CELL_H` / `COLS` / `PAD` — layout grid.
- TD parameter names are written exactly as TD's offline help gives them
  (`/Applications/TouchDesigner.app/Contents/Resources/tfs/Samples/Learn/OfflineHelp`).
  Only In OPs' `connectorder` is not documented there, so `set_order` checks for it.
