# Hydra → TouchDesigner: porting `hydra-synth` as a set of TOP components

Research notes on turning [hydra-synth](https://github.com/hydra-synth/hydra-synth)'s GLSL
function library into a family of TouchDesigner components, one per hydra function, wired
together as a TOP network instead of a JS method chain.

Sources read (all from `main`, fetched 2026-09-11):

- `src/glsl/glsl-functions.js` — the function table (52 functions)
- `src/glsl/utility-functions.js` — `_luminance`, `_noise`, `_rgbToHsv`, `_hsvToRgb`
- `src/generate-glsl.js` — the chain → shader compiler
- `src/glsl-source.js` — shader assembly, uniform declaration, `main()`
- `src/format-arguments.js` — argument coercion rules

**Verdict: yes, this is achievable, and the decomposition is mathematically faithful.**
The differences are about resampling and precision, not about semantics. Details below.

---

## 1. How hydra actually works

This is the part that decides whether a per-node port is even valid.

Hydra is **not** a node graph at runtime. `osc().rotate().modulate(noise())` builds a JS array
of transforms, and `.out()` compiles that whole array into **one fragment shader, one pass**.
`generate-glsl.js` folds the transform list into nested closures, each wrapping the previous:

```js
// src/generate-glsl.js
} else if (transform.transform.type === 'coord') {
  generator = (c, uv) =>
    `${generateInputs(inputs, shaderParams)(`${c}${i}`,uv)}
     ${uv} = ${shaderString(`${c}${i}`, `${uv}`, transform.name, inputs)};
     ${prev(c, uv)}`
}
```

and `glsl-source.js` wraps the result in:

```glsl
void main () {
  vec2 st = gl_FragCoord.xy/resolution.xy;
  <generated body>
  gl_FragColor = c;
}
```

Two structural consequences:

1. **Coordinate ops run backwards, before the source is ever sampled.** A `coord` transform
   emits `st = rotate(st, …);` *then* the upstream chain. So the last coord op in the JS chain
   is the first line of emitted code. The source function is evaluated once, at fully
   transformed coordinates — there is no intermediate image.
2. **`color`/`combine` ops run forwards, after.** They emit `prev(c, uv)` first, then
   `c = posterize(c, …)`.

So a hydra chain is a *coordinate* pipeline running right-to-left and a *color* pipeline
running left-to-right, fused into a single pass.

### Why a per-TOP decomposition is still correct

This looks like a problem — TD would rasterize every stage — but function composition and
texture lookup composition agree:

- A hydra `coord` node computes `f(st)` and hands it upstream.
- A TD `coord` node samples its input at `f(st)`.

If node *N* writes, at pixel *p*, the value its hydra counterpart computes for coordinate *p*,
then a downstream node sampling *N* at `g(p)` gets exactly what hydra computes at `g(p)`.
By induction the whole chain agrees. The same argument covers `combineCoord`/`modulate*`: the
modulator texture is sampled at the current `st`, and downstream coord ops re-address it the
same way hydra's nesting does.

**What actually differs:**

| | hydra | per-TOP port |
|---|---|---|
| Sampling | once, analytic, at full float precision | once per node, bilinear, at texture resolution |
| Wrapping | one `fract()` inside `src`/`prev` | one `fract()` per coord node (see §6) |
| Passes | 1 | 1 per node |
| Precision | float32 throughout | whatever the TOP pixel format is |

Chains of several coord ops will read slightly softer than hydra, and heavy
`kaleid → repeat → modulate` stacks will show resampling artifacts hydra doesn't have. That is
the real cost of the node-graph form, and it's the trade you're making for editability.

---

## 2. The five function classes

Every hydra function is one of five shapes (`glsl-functions.js` header comment):

| type | GLSL signature | TD component shape |
|---|---|---|
| `src` | `vec4 f(vec2 _st, …)` | 0 image inputs, custom res |
| `coord` | `vec2 f(vec2 _st, …)` | 1 image input, samples it at `f(st)` |
| `color` | `vec4 f(vec4 _c0, …)` | 1 image input, per-pixel |
| `combine` | `vec4 f(vec4 _c0, vec4 _c1, …)` | 2 image inputs |
| `combineCoord` | `vec2 f(vec2 _st, vec4 _c0, …)` | 2 image inputs, input 1 modulates sampling of input 0 |

**Five component templates cover all 52 functions.** The per-function work is only: the GLSL
body (copy-paste from the table), the custom parameter list, and the defaults.

### Full inventory

**`src` (8)** — `noise`, `voronoi`, `osc`, `shape`, `gradient`, `src`, `solid`, `prev`

**`coord` (10)** — `rotate`, `scale`, `pixelate`, `repeat`, `repeatX`, `repeatY`, `kaleid`,
`scroll`, `scrollX`, `scrollY`

**`color` (16)** — `posterize`, `shift`, `invert`, `contrast`, `brightness`, `luma`, `thresh`,
`color`, `saturate`, `hue`, `colorama`, `sum`, `r`, `g`, `b`, `a`

**`combine` (7)** — `add`, `sub`, `layer`, `blend`, `mult`, `diff`, `mask`

**`combineCoord` (11)** — `modulate`, `modulateScale`, `modulatePixelate`, `modulateRotate`,
`modulateHue`, `modulateKaleid`, `modulateRepeat`, `modulateRepeatX`, `modulateRepeatY`,
`modulateScrollX`, `modulateScrollY`

Note the pairing: most `modulate*` functions are the `combineCoord` twin of a `coord` function,
differing only by a `+ _c0.r * amount` term. `repeat`/`modulateRepeat`, `kaleid`/`modulateKaleid`,
`scrollX`/`modulateScrollX`, etc. If you want fewer components, each pair could be one component
with an optional second input — but keeping them separate stays 1:1 with hydra's docs, which is
worth more.

---

## 3. Component structure

Per function, a Base COMP:

```
hydra_osc/
  in1        In TOP        (only for coord/color/combine/combineCoord)
  in2        In TOP        (only for combine/combineCoord)
  glsl1      GLSL TOP
  glsl1_pixel  Text DAT    (the shader)
  out1       Out TOP
```

- Custom pars on the **Base**, one per hydra `input`, same names, same defaults.
- `glsl1` uniforms reference `parent().par.Frequency` etc.
- Resolution: `outputresolution = Input` for everything with an input; `Custom` (with
  `Resolutionw`/`Resolutionh` promoted as component pars) for the `src` family.
- Pixel format: **16-bit float** minimum. hydra runs float32 internally; 8-bit fixed will band
  visibly once you stack `colorama`, `posterize` or repeated `modulate`.

### Parameter naming

hydra input names are camelCase (`repeatX`, `nSides`, `offsetY`). TD custom par names must be
Capitalized and alphanumeric, so `repeatX` → `Repeatx`, `nSides` → `Nsides`. Keep the hydra name
in the par **label** so the docs still map. Worth writing this rule into the builder rather than
doing it by hand 52 times.

---

## 4. GLSL translation

Hydra is WebGL1 / GLSL ES 1.00; TD's GLSL TOP is GLSL 3.30+. The shim is small:

```glsl
#define texture2D texture      // hydra calls texture2D()
```

TD-side boilerplate:

| hydra | TouchDesigner |
|---|---|
| `gl_FragCoord.xy/resolution.xy` | `vUV.st` |
| `gl_FragColor = c;` | `fragColor = TDOutputSwizzle(c);` |
| `uniform sampler2D tex;` | `sTD2DInputs[0]` |
| `uniform float time;` | custom uniform, value expression `absTime.seconds` |
| `uniform vec2 resolution;` | custom uniform, or `uTD2DInfos[0].res.zw` |
| `uniform sampler2D prevBuffer;` | Feedback TOP into an input |

Origin matches: both are 0…1 with (0,0) bottom-left, and **neither aspect-corrects**. Hydra's
`shape()` is an ellipse on a 16:9 canvas; don't "fix" this or your output stops matching hydra.

### Template: `src`

```glsl
uniform float time;
uniform float frequency;
uniform float sync;
uniform float offset;

out vec4 fragColor;

vec4 osc(vec2 _st, float frequency, float sync, float offset) {
   vec2 st = _st;
   float r = sin((st.x-offset/frequency+time*sync)*frequency)*0.5  + 0.5;
   float g = sin((st.x+time*sync)*frequency)*0.5 + 0.5;
   float b = sin((st.x+offset/frequency+time*sync)*frequency)*0.5  + 0.5;
   return vec4(r, g, b, 1.0);
}

void main() {
   fragColor = TDOutputSwizzle(osc(vUV.st, frequency, sync, offset));
}
```

The function body between `{` and `}` is **verbatim** from `glsl-functions.js`. That is the
whole point of the port: the table is the spec, and every component is this template plus a
paste.

### Template: `coord`

```glsl
uniform float time;
uniform float angle;
uniform float speed;

out vec4 fragColor;

vec2 rotate(vec2 _st, float angle, float speed) {
   vec2 xy = _st - vec2(0.5);
   float ang = angle + speed*time;
   xy = mat2(cos(ang),-sin(ang), sin(ang),cos(ang))*xy;
   xy += 0.5;
   return xy;
}

void main() {
   vec2 st = rotate(vUV.st, angle, speed);
   fragColor = TDOutputSwizzle(texture(sTD2DInputs[0], fract(st)));
}
```

### Template: `color`

```glsl
void main() {
   vec4 c0 = texture(sTD2DInputs[0], vUV.st);
   fragColor = TDOutputSwizzle(posterize(c0, bins, gamma));
}
```

### Template: `combine`

```glsl
void main() {
   vec4 c0 = texture(sTD2DInputs[0], vUV.st);
   vec4 c1 = texture(sTD2DInputs[1], vUV.st);
   fragColor = TDOutputSwizzle(add(c0, c1, amount));
}
```

### Template: `combineCoord`

```glsl
void main() {
   vec2 st = vUV.st;
   vec4 c0 = texture(sTD2DInputs[1], st);        // input 1 is the MODULATOR
   vec2 st2 = modulate(st, c0, amount);
   fragColor = TDOutputSwizzle(texture(sTD2DInputs[0], fract(st2)));
}
```

**Input order matters and is easy to get backwards.** In hydra, `a.modulate(b, 0.5)` makes `b`
the modulator — it becomes `_c0` in the GLSL. So **input 0 = the chain being modulated,
input 1 = the modulator**. Label the In TOPs `source` and `modulator`, not `in1`/`in2`.

---

## 5. Utility functions

`_luminance` (used by `mask`, `luma`, `thresh`), `_noise` (by `noise`), `_rgbToHsv`/`_hsvToRgb`
(by `hue`, `colorama`) live in `utility-functions.js`. hydra pastes *all* of them into *every*
shader; you only need the ones a given function calls — 6 of 52 components.

`_noise` is Ashima/McEwan simplex 3D and carries two helpers (`permute`, `taylorInvSqrt`).
Copy the block whole.

If your TD build supports `#include` of a DAT in the GLSL TOP, keep one `hydra_utils` Text DAT
and include it; otherwise paste into the six shaders that need it. Check the GLSL TOP's
parameter dialog — I did not verify include support against your version.

---

## 6. Known divergences and special cases

**`fract()` per node vs once.** hydra applies `fract()` only where a texture is finally sampled
(inside `src` and `prev`). A chain of two coord ops computes `fract(f(g(st)))`. The per-TOP port
computes `fract(f(fract(g(st))))`. These differ wherever coordinates leave 0…1 — i.e. exactly
where `rotate`, `scale` and `kaleid` are interesting. Options: accept it (usually reads fine),
or build one fused "coord stack" component for chains where it shows.

**`prev`** returns the previous frame of the *output* buffer. TD equivalent is a Feedback TOP
pointing at the end of the chain. This is the one function that can't be a self-contained
component — it needs a reference to the output, so give it a `Target` par (an OP path) rather
than an input.

**`src(tex)`** is hydra's external-texture source (`s0`, `o0`). In TD this is just the input TOP;
the component can be a Null TOP, or skipped entirely by wiring the source in directly.

**`sum`** is a hack in the table — its `glsl` string closes the function early and defines a
second overload for `vec2`:

```js
`   vec4 v = _c0 * scale;
   return vec4(vec3(v.r + v.g + v.b + v.a), _c0.a);
   }
   float sum(vec2 _st, vec4 scale) { // vec4 is not a typo …
   vec2 v = _st.xy * scale.xy;
   return v.x + v.y;`
```

A naive "wrap the body in a function" generator produces broken GLSL here. Special-case it, or
implement only the `vec4` variant (the `vec2` one exists for the coercion in §7).

**`modulateHue`** is the only function using `resolution` — it needs the uniform wired.

**`color`** relies on `step(0.0, c)` to detect negative arguments and flip to an inverted
response, so its parameters must allow negatives. Range them −1…1, don't clamp at 0.

**`layer`, `diff`, `mask`** take no float arguments — components with inputs but no custom page.

**Int/float strictness.** GLSL 3.30 is stricter than ES 1.00 about mixing `int` and `float`.
The current table is mostly clean (`voronoi` correctly uses `float(i)`), but the doc comment at
the top of the file shows an older `osc` with `offset*2/freq` that would fail. Watch for it when
a function is updated upstream.

**Alpha.** hydra is straight (non-premultiplied) alpha throughout — `layer` does
`mix(_c0.rgb, _c1.rgb, _c1.a)` explicitly. Keep the TD chain straight-alpha and avoid TD ops
that assume premultiplied between hydra nodes.

**Performance.** An 8-op hydra chain is 1 pass; the port is 8 full-screen passes. At 1080p on a
modern GPU this is not a problem, but it's a real constant factor, and `voronoi` (9 taps in a
double loop) plus `noise` (simplex) are the expensive ones.

---

## 7. Things hydra does that the node graph gets for free — or loses

`format-arguments.js` shows two features worth knowing about:

- **Arrays as arguments** (`osc([10,20,30])`) become time-sequenced uniforms via
  `arrayUtils.getValue`. In TD this is just a CHOP or an expression driving the par — strictly
  better, and it's free.
- **A texture where a float is expected.** `DEFAULT_CONVERSIONS` silently wraps a `GlslSource`
  passed as a `float` argument in `sum([1,1,1,1])`. So `osc(noise())` is legal hydra. To support
  this you'd need every float par to optionally accept a TOP — doable (a second input plus a
  toggle, sampling `sum()` of it) but it doubles the component complexity. **Recommend skipping
  it in v1**; it's rarely used and the explicit `sum` component covers it manually.

---

## 8. Building 52 components without hand-building 52 components

Don't type these in. The function table is data — extract it once, generate from it.

**Step 1 — table to JSON** (outside TD, needs node):

```bash
node -e "import('./src/glsl/glsl-functions.js').then(m=>console.log(JSON.stringify(m.default(),null,2)))" \
  > hydra-functions.json
```

The default export is a *function* returning the array, hence the call. One gotcha: `src`'s
default is `NaN`, which serializes to `null`.

**Step 2 — a builder script in TD**, roughly:

```python
import json

TEMPLATES = {           # prebuilt Base COMPs, one per class
    'src': op('/hydra/_tpl_src'),
    'coord': op('/hydra/_tpl_coord'),
    'color': op('/hydra/_tpl_color'),
    'combine': op('/hydra/_tpl_combine'),
    'combineCoord': op('/hydra/_tpl_combinecoord'),
}

def par_name(hydra_name):
    return hydra_name[0].upper() + hydra_name[1:].lower()

def build(spec, dest):
    comp = dest.copy(TEMPLATES[spec['type']], name='hydra_' + spec['name'].lower())
    page = comp.appendCustomPage('Hydra')
    args = []
    for inp in spec['inputs']:
        if inp['type'] != 'float':
            continue                       # vec4 / sampler2D -> special case
        p = page.appendFloat(par_name(inp['name']), label=inp['name'])[0]
        p.default = p.val = inp['default']
        args.append((inp['name'], par_name(inp['name'])))
    comp.op('glsl1_pixel').text = emit_shader(spec, args)
    return comp
```

where `emit_shader` is string assembly: uniform declarations, the utility functions this body
calls (scan the `glsl` string for `_luminance`/`_noise`/`_rgbToHsv`/`_hsvToRgb`), the function
built from `type`'s signature plus the verbatim body, and the `main()` for that class.

Verify these against your build before trusting them: the GLSL TOP's pixel-shader DAT parameter
name, how the Vectors page maps to `uniform float` vs `uniform vec4`, and whether `copy()` on a
Base COMP is the right cloning call for your workflow. The *structure* above is sound; the exact
par spellings I did not check against a running TouchDesigner.

**Step 3 — regeneration.** Keep `hydra-functions.json` in the repo and make the builder
idempotent. hydra's table changes upstream; re-running the builder should be how you track it,
not manual edits to 52 components.

---

## 9. Suggested order of work

1. **One vertical slice first:** `osc` (src) → `rotate` (coord) → `modulate` (combineCoord) with
   `noise` as modulator. Four components, one from each interesting class. Compare side by side
   against `osc().rotate().modulate(noise())` in a hydra browser tab.
2. **Nail the templates** on that slice — uniforms, `time`, resolution, fract, alpha, pixel
   format. Everything after this is mechanical.
3. **Generate the rest** from JSON.
4. **Special cases last:** `prev` (feedback), `src`, `sum`, `modulateHue`.
5. **Optional:** a fused coord-stack component, for when per-node `fract()` and resampling
   visibly diverge from hydra.

The payoff for the node form: every intermediate is a real TOP you can view, tap, record, or
feed to anything else in TouchDesigner — including the `fold` component. Hydra can't do that;
its intermediates only exist as expressions inside one shader.
