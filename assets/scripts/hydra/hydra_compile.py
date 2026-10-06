"""Generate a hydra component's shader -- fused with its upstream chain, the way
hydra does, or on its own.

Hydra never rasterizes intermediate stages. `osc().rotate().posterize()` becomes
one shader, `posterize(osc(rotate(st, ...), ...), ...)`, so the source is
evaluated analytically at the final coordinate -- no wrap, no seam, no
resampling. This module walks the TD wiring upstream from a component and emits
that same chain (hydra src/generate-glsl.js, v1.3.29), then installs it in the
component's GLSL TOP.

Embedded verbatim into every component as the `compile` Text DAT, so an
exported .tox carries it -- this file is the editable master. Re-run the
builder (or `upgrade`) after changing it.

Two modes, on the component's Pipeline page:

  Compiled  every upstream component that is itself Compiled is inlined. Each
            becomes one instance of its function, body verbatim, renamed
            <fn>_<k>, with its arguments as uniforms u<k>_<arg> reading that
            component's own CHOP input. `time` is #defined per instance to that
            component's Time, so per-component clocks survive; with equal clocks
            this is exactly hydra.
  Image     nothing is inlined: the component's own function, its inputs read as
            textures. A render-here point -- downstream components read its
            texture instead of re-evaluating it, which is cheaper for a slow or
            static source shared by several branches.

Anything not inlined is a boundary, read as texture(<input>, fract(uv)) -- what
hydra's own src() does: an Image-mode component, a plain TD TOP, the texture fed
to `src`/`prev`. A boundary is always the In TOP of the component that reads it:
wired straight into the GLSL TOP when that is this component, through a Select
TOP when it is further up the chain.

A Compiled component is only (re)compiled when something looks at it: Viewer on,
or an output -- its texture is read by something outside a compiled chain.
Everything else keeps its last shader and, with nothing pulling it, never cooks.
"""

HYDRA_TAG = 'hydra'
SPEC_KEY = 'hydra_spec'          # set by the builder: see build_hydra.store_spec
SIG_KEY = 'hydra_compiled_sig'   # what is installed now -- skip identical installs
PENDING_KEY = 'hydra_compile_frame'
MAX_DEPTH = 64

# `src` and `prev` read a texture -- in hydra a buffer, never an inlined chain
TEXTURE_INPUT = {'src': 'source', 'prev': 'source'}

SIGNATURE = {
    'src':          ('vec4', ['vec2 _st']),
    'coord':        ('vec2', ['vec2 _st']),
    'color':        ('vec4', ['vec4 _c0']),
    'combine':      ('vec4', ['vec4 _c0', 'vec4 _c1']),
    'combineCoord': ('vec2', ['vec2 _st', 'vec4 _c0']),
}


# --- reading the graph ---------------------------------------------------------

def spec_of(o):
    if o is None or not o.isCOMP or HYDRA_TAG not in o.tags:
        return None
    return o.fetch(SPEC_KEY, None, search=False)


def mode_of(o):
    names = [p.name for p in o.customPars]
    return o.par.Mode.eval() if 'Mode' in names else 'image'


def inlinable(o):
    """A hydra component a compiled chain evaluates instead of reading its texture."""
    return spec_of(o) is not None and mode_of(o) == 'compiled'


def upstream(comp, label):
    """The op wired into the component input whose In TOP is `label`."""
    for c in comp.inputConnectors:
        if c.inOP is not None and c.inOP.name == label and c.connections:
            return c.connections[0].owner
    return None


def reads_texture(comp, label):
    """Does `comp` read the texture arriving on its input `label`?"""
    spec = spec_of(comp)
    return spec is None or mode_of(comp) != 'compiled' \
        or TEXTURE_INPUT.get(spec['name']) == label


def is_output(comp):
    """True when something outside a compiled chain reads this texture.

    Only asked of Compiled components -- Image ones always install, being read
    as textures by definition.
    """
    targets = [conn for oc in comp.outputConnectors for conn in oc.connections]
    return not targets or any(
        reads_texture(conn.owner, conn.inOP.name if conn.inOP is not None else None)
        for conn in targets)


def hydra_neighbours(comp, outputs=True):
    side = comp.outputConnectors if outputs else comp.inputConnectors
    return [conn.owner for c in side for conn in c.connections
            if spec_of(conn.owner) is not None]


def downstream(comp):
    seen, queue, found = {comp.id}, [comp], []
    while queue:
        for nxt in hydra_neighbours(queue.pop(0)):
            if nxt.id not in seen:
                seen.add(nxt.id)
                queue.append(nxt)
                found.append(nxt)
    return found


def par_name(hydra_name):
    return hydra_name[0].upper() + hydra_name[1:].lower()


# --- code generation -------------------------------------------------------------

class Chain:
    """One generated shader: instances, uniforms, boundaries, and main()'s body.

    main() is a list of statements, one variable per value, rather than one
    nested expression: hydra's nesting repeats the incoming coordinate in every
    modulate (once for the call, once for the modulator), so k chained modulates
    grow the shader as 2^k. Variables keep it linear, and caching them by
    (component, coordinate) evaluates a component used twice at the same
    coordinate only once. Every function is pure, so the result is identical.
    """

    def __init__(self, root, inline=True):
        self.root = root
        self.inline = inline
        self.uniforms = []       # (glsl type, name, (expr per component,))
        self.boundaries = []     # (macro name, In TOP)
        self.functions = []      # instance definitions
        self.utils = {}          # utility name -> text, first use wins
        self.instances = {}      # comp.id -> (fn name, [uniform names])
        self.body = []           # main() statements
        self.values = {}         # (comp.id or In TOP path, uv) -> variable
        self.resolution = False  # an instance uses hydra's `resolution`

    def rel(self, o):
        """Path to `o` from the GLSL TOP, so copying a container keeps working.

        Always ask for the exact op an expression reads -- never append to a
        returned path: from the GLSL TOP its own component is `..`-relative,
        and `op()` does not resolve '<that>/<child>' to the child (0.2.0 bug:
        every argument uniform evaluated op(None)[0]).
        """
        return self.root.op('glsl').relativePath(o)

    def let(self, gtype, expr):
        name = f"{'c' if gtype == 'vec4' else 'uv'}{len(self.body)}"
        self.body.append(f'   {gtype} {name} = {expr};')
        return name

    # the vec4 variable holding `comp` evaluated at coordinate variable `uv`
    def gen(self, comp, uv, depth=0):
        if depth > MAX_DEPTH:
            raise RuntimeError(f'chain deeper than {MAX_DEPTH} at {comp.path}')
        key = (comp.id, uv)
        if key in self.values:
            return self.values[key]

        spec = spec_of(comp)
        fn, args = self.define(comp, spec)
        a = ''.join(', ' + x for x in args)
        kind = spec['type']
        inp = lambda label, at: self.input(comp, label, at, depth)

        if kind == 'src':
            c = self.let('vec4', f'{fn}({uv}{a})')
        elif kind == 'coord':
            c = inp('source', self.let('vec2', f'{fn}({uv}{a})'))
        elif kind == 'color':
            c = self.let('vec4', f"{fn}({inp('source', uv)}{a})")
        elif kind == 'combine':
            c = self.let('vec4', f"{fn}({inp('source', uv)}, {inp('with', uv)}{a})")
        elif kind == 'combineCoord':
            # hydra: f0(modulate(uv, f1(uv), ...)) -- the modulator is evaluated
            # at the incoming uv, the source at the modulated one
            m = inp('modulator', uv)
            c = inp('source', self.let('vec2', f'{fn}({uv}, {m}{a})'))
        else:
            raise RuntimeError(f'{comp.path}: unknown class {kind!r}')
        self.values[key] = c
        return c

    def input(self, comp, label, uv, depth):
        owner = upstream(comp, label)
        if owner is None:
            return 'vec4(0.0)'                       # nothing wired: transparent black
        if self.inline and inlinable(owner):
            return self.gen(owner, uv, depth + 1)
        key = (comp.op(label).path, uv)
        if key not in self.values:
            tex = self.boundary(comp, label)
            self.values[key] = self.let('vec4', f'texture({tex}, fract({uv}))')
        return self.values[key]

    def boundary(self, comp, label):
        """Macro name for the texture on `comp`'s input `label`."""
        top = comp.op(label)
        for name, t in self.boundaries:
            if t.path == top.path:
                return name
        # a #define, so the name must not occur in any hydra body or util:
        # `s0` did -- a local in _noise -- and broke every shader with both
        name = f'HYDRA_INPUT_{len(self.boundaries)}'
        self.boundaries.append((name, top))
        return name

    def uniform(self, gtype, name, exprs):
        self.uniforms.append((gtype, name, tuple(exprs)))
        return name

    def define(self, comp, spec):
        """Emit one instance of this component's function; return (name, args)."""
        if comp.id in self.instances:
            return self.instances[comp.id]

        k = len(self.instances)
        name, kind = spec['name'], spec['type']
        fn = f'{name}_{k}'
        ret, lead = SIGNATURE[kind]
        p = self.rel(comp)
        alias = spec.get('alias')
        body = spec['glsl']
        if name == 'sum':
            # hydra's `sum` closes itself early to add a vec2 overload, which
            # would be redefined per instance -- keep only the vec4 function
            body = body.split('float sum(vec2')[0].rstrip().rstrip('}').rstrip()

        passed = [i for i in spec['inputs']
                  if i['type'] != 'sampler2D' and not i.get('extension')]
        extension = [i for i in spec['inputs'] if i.get('extension')]

        args = []
        for i in passed + extension:
            n = i['name']
            un = f'u{k}_{n}'
            if i['type'] == 'float' and comp.op(n) is not None:
                # the In CHOP always has a channel: the wired CHOP, or the
                # Constant fallback on its own input. A bare reference may take
                # TD's optimized-expression path and skip Python.
                self.uniform('float', un, (f"op('{self.rel(comp.op(n))}')[0]",))
            elif i['type'] == 'float':                  # no In CHOP: the parameter
                self.uniform('float', un, (f"op('{p}').par.{par_name(n)}",))
            else:                                       # vecN: straight from pars
                pars = sorted(comp.pars(par_name(n) + '*'), key=lambda q: q.name)
                self.uniform(i['type'], un, [f"op('{p}').par.{q.name}" for q in pars])
            args.append(un)

        out = [f'// {name}  <-  {comp.path}']
        undef = []
        if 'time' in spec.get('uses', ()):
            self.uniform('float', f'u{k}_time', (f"op('{p}').par.Time",))
            out.append(f'#define time u{k}_time')
            undef.append('time')
        if alias:
            out.append(f'#define {alias} {self.boundary(comp, TEXTURE_INPUT[name])}')
            undef.append(alias)
        if 'resolution' in spec.get('uses', ()):
            self.resolution = True

        sig = lead + [f"{i['type']} {i['name']}" for i in passed]
        out += [f"{ret} {fn}({', '.join(sig)}) {{", body.rstrip(), '}']
        out += [f'#undef {u}' for u in undef]

        if extension:
            # blend_amount: fade the result back toward the source, applied
            # around the call so the body stays verbatim
            wsig = lead + [f"{i['type']} {i['name']}" for i in passed + extension]
            inner = ', '.join([s.split()[1] for s in lead] + [i['name'] for i in passed])
            out += [f"{ret} {fn}_x({', '.join(wsig)}) {{",
                    f'   return mix(_c0, {fn}({inner}), amount);', '}']
            fn = f'{fn}_x'

        for uname, text in spec.get('utils', {}).items():
            self.utils.setdefault(uname, text)
        self.functions.append('\n'.join(out))
        self.instances[comp.id] = (fn, args)
        return fn, args

    def shader(self, result):
        mode = 'compiled chain' if self.inline else 'image'
        # the input count is in the text so that a change in it always changes
        # the text, which is what makes TD recompile (see install)
        lines = [f'// hydra {mode} for {self.root.path}',
                 '// GENERATED by hydra_compile -- do not edit.',
                 f'// boundary inputs: {len(self.boundaries)}', '']
        lines += [f'uniform {t} {n};' for t, n, _ in self.uniforms]
        if self.resolution:
            lines.append('uniform vec2 resolution;')
        # boundaries arrive on the GLSL TOP's inputs, in this order
        lines += [f'#define {n} sTD2DInputs[{i}]'
                  for i, (n, _) in enumerate(self.boundaries)]
        lines += ['', '#define texture2D texture', '', 'out vec4 fragColor;', '']
        for uname, text in self.utils.items():
            lines += [f'// --- {uname}, from hydra utility-functions.js ---', text, '']
        for f in self.functions:
            lines += [f, '']
        lines += ['void main() {', '   vec2 st = vUV.st;'] + self.body
        lines += [f'   fragColor = TDOutputSwizzle({result});', '}']
        return '\n'.join(lines) + '\n'


def resolution(comp, inline):
    """(w expr, h expr) for a non-source component, or None to follow input 0.

    A compiled chain is drawn at its root's canvas: the source found along
    `source` inputs, or the texture that ends that path.
    """
    glsl = comp.op('glsl')
    cur = comp
    for _ in range(MAX_DEPTH):
        spec = spec_of(cur)
        if spec['type'] == 'src' and spec['name'] not in TEXTURE_INPUT:
            if cur is comp:
                return None                  # a source sets its own, on Output
            pars = sorted((q for q in cur.customPars
                           if q.name.startswith('Resolution')), key=lambda q: q.name)
            path = glsl.relativePath(cur)
            return tuple(f"op('{path}').par.{q.name}" for q in pars[:2]) \
                if len(pars) >= 2 else None
        label = TEXTURE_INPUT.get(spec['name'], 'source')
        owner = upstream(cur, label)
        if owner is None or cur is comp and not inline:
            return None
        if inline and inlinable(owner) and spec['name'] not in TEXTURE_INPUT:
            cur = owner
            continue
        path = glsl.relativePath(cur.op(label))
        return (f"op('{path}').width", f"op('{path}').height")
    return None


# --- installing it in TouchDesigner ---------------------------------------------

def _set_uniforms(glsl, uniforms):
    """[(name, (expr, ...))] onto the Vectors page."""
    glsl.seq.vec.numBlocks = max(len(uniforms), 1)
    for i, (uname, exprs) in enumerate(uniforms):
        glsl.par[f'vec{i}name'] = uname
        for c in 'xyzw':
            p = glsl.par[f'vec{i}value{c}']
            p.mode = ParMode.CONSTANT
            p.val = 0
        for c, expr in zip('xyzw', exprs):
            glsl.par[f'vec{i}value{c}'].expr = expr


def _set_inputs(comp, glsl, boundaries):
    """Wire the boundaries into the GLSL TOP, in order.

    The GLSL TOP has no Samplers page (the GLSL MAT does), so a texture can only
    arrive on an input. This component's own In TOPs are wired directly; one
    further up the chain comes through a Select TOP.
    """
    for c in glsl.inputConnectors:
        c.disconnect()
    selects = 0
    for i, (name, top) in enumerate(boundaries):
        if top.parent() == comp:
            src = top
        else:
            src = comp.op(f'boundary{selects}') \
                or comp.create(selectTOP, f'boundary{selects}')
            src.nodeX, src.nodeY = -300, -600 - 120 * selects
            src.par.top = src.relativePath(top)
            selects += 1
        glsl.inputConnectors[i].connect(src)
    while comp.op(f'boundary{selects}'):            # left over from a longer chain
        comp.op(f'boundary{selects}').destroy()
        selects += 1


def _set_menu(par, *needles):
    for i, n in enumerate(par.menuNames):
        if all(x in n.lower() for x in needles):
            par.menuIndex = i
            return


def install(comp, inline):
    """Generate and install this component's shader. Returns stages, or 0 if
    what is installed already matches."""
    chain = Chain(comp, inline)
    text = chain.shader(chain.gen(comp, 'st'))
    uniforms = [(n, exprs) for _, n, exprs in chain.uniforms]
    if chain.resolution:
        uniforms.append(('resolution', ('me.width', 'me.height')))
    is_src = spec_of(comp)['type'] == 'src' and spec_of(comp)['name'] not in TEXTURE_INPUT
    res = None if is_src else resolution(comp, inline)
    inputs = [top.path for _, top in chain.boundaries]

    glsl, dat = comp.op('glsl'), comp.op('pixel')
    sig = repr((text, uniforms, inputs, res))
    if comp.fetch(SIG_KEY, None, search=False) == sig \
            and glsl.par.pixeldat.eval() == dat:
        return 0

    # Inputs first: TD sizes sTD2DInputs[] by the inputs present when the shader
    # compiles, and does not recompile when they change afterwards.
    _set_inputs(comp, glsl, chain.boundaries)
    _set_uniforms(glsl, uniforms)
    if dat.par.syncfile.eval():
        dat.par.syncfile = False     # never write generated text back to a file
        dat.par.file = ''
    dat.text = text
    if glsl.par.pixeldat.eval() != dat:
        glsl.par.pixeldat = dat
    if res is not None:
        _set_menu(glsl.par.outputresolution, 'custom')
        glsl.par.resolutionw.expr, glsl.par.resolutionh.expr = res
    elif not is_src:
        _set_menu(glsl.par.outputresolution, 'input')
    comp.store(SIG_KEY, sig)
    return len(chain.instances)


def compile_now(comp):
    comp.unstore(PENDING_KEY)
    if spec_of(comp) is None or not comp.op('glsl') or not comp.op('pixel'):
        return
    mode = mode_of(comp)
    if mode == 'compiled' and not (comp.viewer or is_output(comp)):
        return                       # nothing looks at it; compiled when that changes
    try:
        n = install(comp, inline=mode == 'compiled')
        if n:
            print(f'{comp.path}: {mode}, {n} stage(s)')
    except Exception as e:
        print(f'  !! {comp.path}: compile failed ({e})')


def schedule(comp, down=True, up=False):
    """Compile next frame, once per component however many triggers arrive."""
    targets = [comp] + (downstream(comp) if down else []) \
        + (hydra_neighbours(comp, outputs=False) if up else [])
    frame = absTime.frame
    for t in targets:
        if t.fetch(PENDING_KEY, None, search=False) == frame:
            continue
        t.store(PENDING_KEY, frame)
        run('args[0].op("compile").module.compile_now(args[0])', t,
            delayFrames=1, delayRef=op.TDResources)
