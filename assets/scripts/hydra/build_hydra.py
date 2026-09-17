"""Build one Base COMP per hydra function, wrapping the GLSL TOP and its shader.

Each component:

    hydra_osc/
      source, modulator   In TOPs    (class-dependent; none for `src`)
      frequency, sync...  In CHOPs   (one per numeric hydra argument)
      frequency_default   Constant CHOPs, wired to input 1 of the matching In CHOP
      pixel               Text DAT   (file-synced to assets/scripts/hydra/<name>.frag)
      glsl                GLSL TOP   (internals -- users never open this)
      out                 Out TOP    (also the component's Operator Viewer)
    + custom page 'Hydra', one float par per hydra input

Argument resolution, per input:

    Each numeric argument gets its own CHOP input on the component, named after
    the hydra argument. Whatever channel you connect is used -- the name does not
    matter, only the connector. With nothing connected, the In CHOP falls through
    to its second input, a Constant CHOP driven by the component's custom
    parameter. So: connect a CHOP to animate, or leave it and dial the parameter.

    The parameter may itself hold any expression, including
    mod('/project1/hydra/hydra_seq').val([...]) for hydra's array arguments.

Run from the Textport once a Text DAT is synced to this file:

    b = mod('/project1/hydra/build_hydra')
    b.build_all(op('/project1/hydra'))     # build (replaces same-named components)
    b.rebuild(op('/project1/hydra'))       # wipe everything generated, then build
    b.clear(op('/project1/hydra'))         # wipe only
    b.layout_groups(op('/project1/hydra'), b.load_specs())   # re-arrange only

`clear` and `rebuild` destroy every hydra_* component AND every Annotate COMP in
the target container -- keep these in their own container, not next to work you
care about.
"""

import json
import os

HERE = 'assets/scripts/hydra'
SHADERS = f'{HERE}/shader'   # .frag files live here
TOX_ROOT = 'components/hydra'   # exported .tox tree, one folder per group
HYDRA_TAG = 'hydra'             # marks generated components; survives .tox save
DEFAULT_RES = (1280, 720)       # source components; everything else follows its input
NONCOMMERCIAL_MAX = 1280        # the free licence caps output at 1280x1280

# image inputs per hydra function class; `source` is always input 0
CLASS_INPUTS = {
    'src':          [],
    'coord':        ['source'],
    'color':        ['source'],
    'combine':      ['source', 'with'],
    'combineCoord': ['source', 'modulator'],   # modulator is hydra's _c0
}

# functions whose class says "no image input" but whose body samples a texture:
# `src` takes an external TOP, `prev` takes the feedback of the chain's output
NAME_INPUTS = {
    'src':  ['source'],
    'prev': ['source'],
}


def inputs_for(spec):
    return NAME_INPUTS.get(spec['name'], CLASS_INPUTS[spec['type']])


# hydra's documentation groups are exactly its five GLSL classes. Colours are
# eyeballed from the docs' underlines; `src` is a guess (that group is cropped
# in the screenshot I worked from) -- adjust to taste, it is only cosmetic.
GROUPS = {
    'src':          ('Source',   (0.96, 0.62, 0.42)),   # orange
    'coord':        ('Geometry', (0.95, 0.82, 0.42)),   # yellow
    'color':        ('Color',    (0.71, 0.91, 0.33)),   # lime
    'combine':      ('Blend',    (0.44, 0.90, 0.63)),   # green
    'combineCoord': ('Modulate', (0.50, 0.91, 0.88)),   # cyan
    'audio':        ('Audio',    (0.94, 0.38, 0.60)),   # pink
}

GROUP_ORDER = ('src', 'coord', 'color', 'combine', 'combineCoord', 'audio')

# components that are not generated from hydra-functions.json, by group
EXTRA_MEMBERS = {'audio': [{'name': 'fft', 'type': 'audio'}]}

CELL_W, CELL_H = 240, 200      # spacing between components
COLS = 6                       # components per row within a group
PAD = 80                       # annotation margin around a group

# the slice, used when hydra-functions.json is not present yet
SLICE = [
    {'name': 'osc', 'type': 'src', 'inputs': [
        {'name': 'frequency', 'type': 'float', 'default': 60},
        {'name': 'sync', 'type': 'float', 'default': 0.1},
        {'name': 'offset', 'type': 'float', 'default': 0}]},
    {'name': 'noise', 'type': 'src', 'inputs': [
        {'name': 'scale', 'type': 'float', 'default': 10},
        {'name': 'offset', 'type': 'float', 'default': 0.1}]},
    {'name': 'rotate', 'type': 'coord', 'inputs': [
        {'name': 'angle', 'type': 'float', 'default': 10},
        {'name': 'speed', 'type': 'float', 'default': 0}]},
    {'name': 'modulate', 'type': 'combineCoord', 'inputs': [
        {'name': 'amount', 'type': 'float', 'default': 0.1}]},
]


def par_name(hydra_name):
    """repeatX -> Repeatx. TD custom par names are Capitalized alphanumerics."""
    return hydra_name[0].upper() + hydra_name[1:].lower()


def set_menu(par, *needles):
    for i, n in enumerate(par.menuNames):
        if all(x in n.lower() for x in needles):
            par.menuIndex = i
            return n
    print(f'  !! {par.name}: no match for {needles} in {par.menuNames}')


def find_par(o, *candidates):
    """First existing par among `candidates`; prints what is there if none match."""
    names = {p.name for p in o.pars()}
    for c in candidates:
        if c in names:
            return o.par[c]
    print(f'  !! {o.path}: none of {candidates} in {sorted(names)}')
    return None


def darken(rgb, amount=0.4):
    """Scale value only -- multiplying RGB uniformly leaves hue and saturation."""
    return tuple(c * (1.0 - amount) for c in rgb)


def set_color_par(o, prefix, rgb):
    """Set a 3-par colour tuplet like Fillcolorr/g/b. True if it existed."""
    names = {p.name for p in o.pars()}
    keys = [prefix + c for c in 'rgb']
    if not all(k in names for k in keys):
        return False
    for k, v in zip(keys, rgb):
        o.par[k].val = v
    return True


def set_order(o, index):
    """Connect Order on an In OP -- decides the component's connector order."""
    p = find_par(o, 'connectorder', 'connectionorder', 'order')
    if p is not None:
        p.val = index


def shader_text(name):
    """Read the .frag from disk to see which built-in uniforms it declares."""
    path = os.path.join(project.folder, SHADERS, f'{name}.frag')
    try:
        with open(path) as f:
            return f.read()
    except OSError:
        print(f'  !! no shader at {path}')
        return ''


def load_specs():
    path = os.path.join(project.folder, HERE, 'hydra-functions.json')
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    return SLICE


TIME_DEFAULT_EXPR = 'absTime.seconds'


def uses_time(text):
    return 'uniform float time' in text


def time_exec_source():
    """The Parameter Execute callbacks, embedded into each time-using component."""
    path = os.path.join(project.folder, HERE, 'time_exec.py')
    try:
        with open(path) as f:
            return f.read()
    except OSError:
        print(f'  !! no time_exec.py at {path}')
        return ''


def _uniforms(spec, text, par_exprs):
    """[(uniform name, (expr per component,))] in Vectors-page order."""
    out = []
    if uses_time(text):
        out.append(('time', ('parent().par.Time',)))
    if 'uniform vec2 resolution' in text:
        out.append(('resolution', ('me.width', 'me.height')))
    for inp in spec['inputs']:
        n = inp['name']
        if inp['type'] == 'float':
            # the In CHOP always has a channel: connected, or the Constant default
            out.append((n, (f"op('{n}')[0].eval()",)))
        elif n in par_exprs:              # vec2/3/4 -- straight from its parameters
            out.append((n, par_exprs[n]))
        # sampler2D inputs became TOP inputs via #define in the shader
    return out


def build(spec, dest):
    name, kind = spec['name'], spec['type']
    comp_name = f'hydra_{name.lower()}'

    old = dest.op(comp_name)
    if old:
        old.destroy()
    comp = dest.create(baseCOMP, comp_name)

    text = shader_text(name)
    wants_time = uses_time(text)

    # --- custom parameters ----------------------------------------------------
    floats = [i for i in spec['inputs'] if i['type'] == 'float']
    vecs = [i for i in spec['inputs'] if i['type'].startswith('vec')]
    par_exprs = {}                      # hydra input name -> (expr per component,)
    if floats or vecs:
        page = comp.appendCustomPage('Hydra')
        for inp in floats:
            p = page.appendFloat(par_name(inp['name']), label=inp['name'])[0]
            d = float(inp['default'] or 0)
            p.default = p.val = d
            p.normMin, p.normMax = min(0.0, 2 * d), max(1.0, 2 * d)
        for inp in vecs:                # only `sum` today; no CHOP input for these
            width = int(inp['type'][3:])
            pars = page.appendFloat(par_name(inp['name']), label=inp['name'],
                                    size=width)
            defaults = inp['default'] or [0] * width
            for p, v in zip(pars, defaults):
                p.default = p.val = float(v)
            par_exprs[inp['name']] = tuple(f'parent().par.{p.name}' for p in pars)

    res_pars = None
    if not inputs_for(spec):            # a source: nothing upstream to inherit from
        opage = comp.appendCustomPage('Output')
        res_pars = opage.appendInt('Resolution', label='resolution', size=2)
        for p, v in zip(res_pars, DEFAULT_RES):
            p.default = p.val = v

    if wants_time:                      # its own page -- 'Hydra' stays arguments only
        tpage = comp.appendCustomPage('Time Sync')
        t = tpage.appendFloat('Time', label='time')[0]
        t.default = 0.0
        t.expr = TIME_DEFAULT_EXPR      # a parameter, not an input -- rarely rewired
        tpage.appendPulse('Propagatetime', label='Propagate Time Expr')

    # --- internals ------------------------------------------------------------
    dat = comp.create(textDAT, 'pixel')
    dat.par.file = f'{SHADERS}/{name}.frag'
    dat.par.syncfile = True
    dat.par.loadonstart = True
    dat.nodeX, dat.nodeY = -200, -300

    in_tops = []
    for i, label in enumerate(inputs_for(spec)):
        t = comp.create(inTOP, label)
        t.nodeX, t.nodeY = -600, -160 * i
        set_order(t, i)                    # image inputs come first
        in_tops.append(t)

    # one CHOP input per numeric argument, each defaulting to its parameter
    for i, inp in enumerate(floats):
        hn = inp['name']
        y = 200 + 160 * i

        const = comp.create(constantCHOP, f'{hn}_default')
        const.nodeX, const.nodeY = -800, y
        cname = find_par(const, 'name0', 'const0name')
        cvalue = find_par(const, 'value0', 'const0value')
        if cname is not None:
            cname.val = hn
        if cvalue is not None:
            cvalue.expr = f'parent().par.{par_name(hn)}'

        chop = comp.create(inCHOP, hn)
        chop.nodeX, chop.nodeY = -600, y
        set_order(chop, len(in_tops) + i)   # then the arguments, in hydra's order
        # The In CHOP's own input is the fallback, used when the component's
        # external connector is empty. Builds differ on how many connectors it
        # exposes, so take the last one rather than assuming an index.
        if chop.inputConnectors:
            chop.inputConnectors[len(chop.inputConnectors) - 1].connect(const)
        else:
            print(f'  !! {chop.path}: no input connectors to attach the default to')

    glsl = comp.create(glslTOP, 'glsl')
    glsl.nodeX, glsl.nodeY = 0, 0
    glsl.par.pixeldat = dat
    set_menu(glsl.par.format, '16', 'float')
    set_menu(glsl.par.outputresolution, *(('input',) if in_tops else ('custom',)))
    if res_pars is not None:
        for gpar, cpar in (('resolutionw', res_pars[0]), ('resolutionh', res_pars[1])):
            p = find_par(glsl, gpar)
            if p is not None:
                p.expr = f'parent().par.{cpar.name}'
    for i, t in enumerate(in_tops):
        glsl.inputConnectors[i].connect(t)

    if wants_time:
        # embedded, not file-synced, so an exported .tox carries its own callback
        pexec = comp.create(parameterexecuteDAT, 'time_exec')
        pexec.nodeX, pexec.nodeY = -200, -450
        pexec.text = time_exec_source()
        target = find_par(pexec, 'op', 'ops', 'operators')
        if target is not None:
            target.val = '..'
        which = find_par(pexec, 'pars', 'parameters', 'par')
        if which is not None:
            which.val = 'Propagatetime'
        for cand in ('pulse', 'onpulse'):
            p = pexec.par[cand] if cand in {x.name for x in pexec.pars()} else None
            if p is not None:
                p.val = True
                break
        else:
            print(f'  !! {pexec.path}: no pulse toggle found')

    out = comp.create(outTOP, 'out')
    out.nodeX, out.nodeY = 250, 0
    out.inputConnectors[0].connect(glsl)

    viewer = find_par(comp, 'opviewer')
    if viewer is not None:
        viewer.val = './out'

    comp.color = GROUPS[kind][1]
    comp.tags = {HYDRA_TAG, f'hydra:{name}', f'hydra:{kind}'}

    # --- uniforms -------------------------------------------------------------
    uniforms = _uniforms(spec, text, par_exprs)
    glsl.seq.vec.numBlocks = max(len(uniforms), 1)
    for i, (uname, exprs) in enumerate(uniforms):
        glsl.par[f'vec{i}name'] = uname
        for c in 'xyzw':
            p = glsl.par[f'vec{i}value{c}']
            p.mode = ParMode.CONSTANT
            p.val = 0
        for c, expr in zip('xyzw', exprs):
            glsl.par[f'vec{i}value{c}'].expr = expr

    print(f'{comp_name}: {kind}, {len(in_tops)} TOP in, '
          f'{len(floats)} CHOP in, {len(uniforms)} uniforms')
    return comp


AUDIO_COLOR = (0.94, 0.38, 0.60)      # the docs' Audio pink


def build_audio(dest):
    """hydra_fft: a.fft[n] as a component. Audio CHOP in, fft_0..fft_n out."""
    old = dest.op('hydra_fft')
    if old:
        old.destroy()
    comp = dest.create(baseCOMP, 'hydra_fft')

    page = comp.appendCustomPage('Hydra')
    specs = [('Bins', 'bins', 4, 1, 8), ('Cutoff', 'cutoff', 2.0, 0.0, 20.0),
             ('Scale', 'scale', 10.0, 0.01, 50.0), ('Smooth', 'smooth', 0.4, 0.0, 0.99),
             ('Buffer', 'buffer size', 512, 128, 4096)]
    for name, label, dflt, lo, hi in specs:
        p = (page.appendInt(name, label=label)[0] if isinstance(dflt, int)
             else page.appendFloat(name, label=label)[0])
        p.default = p.val = dflt
        p.normMin, p.normMax = lo, hi

    audio = comp.create(inCHOP, 'audio')
    audio.nodeX, audio.nodeY = -400, 0
    set_order(audio, 0)

    dat = comp.create(textDAT, 'fft_script')
    dat.par.file = f'{HERE}/fft_chop.py'
    dat.par.syncfile = True
    dat.par.loadonstart = True
    dat.nodeX, dat.nodeY = -200, -200

    script = comp.create(scriptCHOP, 'fft')
    script.nodeX, script.nodeY = 0, 0
    cb = find_par(script, 'callbacks', 'dat', 'script')
    if cb is not None:
        cb.val = dat.name
    script.inputConnectors[0].connect(audio)

    setup = find_par(script, 'setuppars', 'setupparameters')
    if setup is not None:
        setup.pulse()

    # bind the script's own pars to the component's, so users see one page
    for name, _, _, _, _ in specs:
        try:
            script.par[name].expr = f'parent().par.{name}'
        except Exception as e:
            print(f'  !! could not bind {name} on {script.path}: {e}')

    out = comp.create(outCHOP, 'out')
    out.nodeX, out.nodeY = 250, 0
    out.inputConnectors[0].connect(script)

    viewer = find_par(comp, 'opviewer')
    if viewer is not None:
        viewer.val = './out'
    comp.color = AUDIO_COLOR
    comp.tags = {HYDRA_TAG, 'hydra:fft', 'hydra:audio'}
    print('hydra_fft: audio CHOP in, fft_0..fft_n + vol out')
    return comp


LIBRARY_PATH = None        # remembered by build_all, so spawn() needs no argument


def library(hint=None):
    """The container holding the generated components."""
    if hint is not None:
        return hint
    if LIBRARY_PATH and op(LIBRARY_PATH):
        return op(LIBRARY_PATH)
    try:                                   # nothing remembered: go looking
        found = root.findChildren(tags=[HYDRA_TAG], maxDepth=4)
    except Exception:
        found = []
    if found:
        return found[0].parent()
    print('  !! no hydra library found -- pass library=op(...)')
    return None


def unique_name(dest, base):
    if not dest.op(base):
        return base
    i = 1
    while dest.op(f'{base}{i}'):
        i += 1
    return f'{base}{i}'


def spawn(names, dest=None, lib=None, postfix='', spacing=200, x=0, y=0):
    """Copy generated components by name, e.g. spawn(['osc', 'osc', 'rotate']).

    Repeats are fine -- each copy gets a numeric suffix so names stay unique.
    `postfix` goes before that suffix: postfix='_a' gives hydra_osc_a.

    Copies land in `dest`, defaulting to the library's parent rather than the
    library itself: copies inherit the `hydra` tag, and clear()/rebuild() would
    destroy them along with the originals if they sat in the same container.
    """
    lib = library(lib)
    if lib is None:
        return []
    dest = dest or lib.parent()

    made, missing = [], []
    for i, name in enumerate(names):
        original = lib.op(f'hydra_{name.lower()}')
        if not original:
            missing.append(name)
            continue
        copy = dest.copy(original, name=unique_name(dest, f'hydra_{name.lower()}{postfix}'))
        copy.nodeX, copy.nodeY = x + spacing * len(made), y
        made.append(copy)

    print(f'spawned {len(made)} in {dest.path}: ' + ', '.join(c.name for c in made))
    if missing:
        print('  !! not in the library:', ', '.join(missing))
    return made


def set_resolution(dest, width, height):
    """Retune every generated source component's output resolution."""
    n = 0
    for child in dest.children:
        if HYDRA_TAG not in child.tags:
            continue
        pars = sorted(child.pars('Resolution*'), key=lambda p: p.name)
        if len(pars) >= 2:
            pars[0].val, pars[1].val = width, height
            n += 1
    print(f'set {n} source component(s) to {width}x{height}')
    if max(width, height) > NONCOMMERCIAL_MAX:
        print(f'  !! over {NONCOMMERCIAL_MAX}px -- a non-commercial licence will '
              f'clamp this')
    return n


def is_annotate(o):
    return getattr(o, 'OPType', '') == 'annotateCOMP' or o.name.startswith('group_')


def clear(dest, components=True, annotations=True):
    """Destroy generated ops in `dest`.

    DESTRUCTIVE: removes every `hydra_*` component and every Annotate COMP in
    that container, including annotations you added by hand. Scoped to `dest`,
    so keep the hydra components in their own container.
    """
    removed = []
    for child in list(dest.children):
        if components and (HYDRA_TAG in child.tags
                           or child.name.startswith('hydra_')):
            removed.append(child.name)
            child.destroy()
        elif annotations and is_annotate(child):
            removed.append(child.name)
            child.destroy()
    print(f'cleared {len(removed)} ops from {dest.path}')
    return removed


def rebuild(dest, specs=None):
    """Wipe everything generated, then build it all again from the JSON."""
    clear(dest)
    return build_all(dest, specs)


def layout_groups(dest, specs, verbose=True):
    """Arrange components by hydra doc group, each wrapped in an Annotate COMP."""
    for child in list(dest.children):        # stale annotations from earlier runs
        if is_annotate(child):
            child.destroy()

    by_kind = {}
    for s in specs:
        by_kind.setdefault(s['type'], []).append(s)
    for kind, members in EXTRA_MEMBERS.items():          # only if actually built
        present = [m for m in members if dest.op(f"hydra_{m['name'].lower()}")]
        if present:
            by_kind.setdefault(kind, []).extend(present)

    top = 0
    for kind in GROUP_ORDER:
        items = by_kind.get(kind, [])
        if not items:
            continue
        label, col = GROUPS[kind]
        rows = (len(items) + COLS - 1) // COLS
        cols = min(len(items), COLS)

        placed = []
        for i, spec in enumerate(items):
            comp = dest.op(f"hydra_{spec['name'].lower()}")
            if not comp:
                continue
            r, c = divmod(i, COLS)
            comp.nodeX = c * CELL_W
            comp.nodeY = top - r * CELL_H
            placed.append(comp)

        note = dest.create(annotateCOMP, f'group_{kind.lower()}')
        if verbose and kind == GROUP_ORDER[0]:
            print('annotateCOMP pars:', sorted(p.name for p in note.pars()))

        title = find_par(note, 'Title', 'Titletext', 'title', 'titletext', 'header')
        if title is not None:
            title.val = label
        body = find_par(note, 'Bodytext', 'Body', 'Text', 'Notes', 'message')
        if body is not None:
            body.val = f'hydra {label} -- {len(items)} functions'

        note.color = col                      # header keeps the full group colour
        if verbose and kind == GROUP_ORDER[0]:
            print('annotate colour pars:',
                  sorted(p.name for p in note.pars() if 'color' in p.name.lower()))
        for prefix in ('Backcolor', 'Fillcolor', 'Bgcolor', 'Bodycolor'):
            if set_color_par(note, prefix, darken(col, 0.4)):
                break
        else:
            print(f'  !! {note.path}: no fill-colour par found for the body')

        # nodeY is the node's BOTTOM edge, height grows upward -- derive the
        # rectangle from what was actually placed rather than from the grid
        if placed:
            x0 = min(c.nodeX for c in placed)
            x1 = max(c.nodeX + c.nodeWidth for c in placed)
            y0 = min(c.nodeY for c in placed)
            y1 = max(c.nodeY + c.nodeHeight for c in placed)
            note.nodeX, note.nodeY = x0 - PAD, y0 - PAD
            note.nodeWidth = (x1 - x0) + 2 * PAD
            note.nodeHeight = (y1 - y0) + 2 * PAD

        top -= rows * CELL_H + PAD * 3
    print('laid out', len(specs), 'components in', len(GROUP_ORDER), 'groups')


def members(specs):
    """[(group kind, function name)] for everything that should exist."""
    out = [(s['type'], s['name']) for s in specs]
    for kind, extra in EXTRA_MEMBERS.items():
        out += [(kind, m['name']) for m in extra]
    return out


def export_tox(dest, specs=None, root=None, group_case=str.lower):
    """Save each component to <root>/<group>/hydra_<name>.tox.

    Overwrites whatever is there, so the tree always matches the last build.
    """
    specs = specs or load_specs()
    root = root or os.path.join(project.folder, *TOX_ROOT.split('/'))

    saved, missing = 0, []
    for kind, name in members(specs):
        comp = dest.op(f'hydra_{name.lower()}')
        if not comp:
            missing.append(name)
            continue
        folder = os.path.join(root, group_case(GROUPS[kind][0]))
        os.makedirs(folder, exist_ok=True)
        comp.save(os.path.join(folder, f'hydra_{name.lower()}.tox'))
        saved += 1

    print(f'saved {saved} .tox files under {root}')
    if missing:
        print('  not built, skipped:', ', '.join(missing))
    return saved


def palette_root():
    """The user palette folder -- what the Palette browser lists as My Components."""
    config = getattr(app, 'configFolder', None)
    candidates = [getattr(app, 'userPaletteFolder', None),
                  os.path.join(config, 'Palette') if config else None,
                  os.path.expanduser('~/Documents/Derivative/Palette')]
    for c in candidates:
        if c and os.path.isdir(c):
            return c
    fallback = os.path.expanduser('~/Documents/Derivative/Palette')
    print(f'  !! no palette folder among {[c for c in candidates if c]}')
    print(f'  !! creating {fallback} -- pass root=... if your palette is elsewhere')
    return fallback


def export_palette(dest, specs=None, root=None, section='Hydra'):
    """Save into the user palette as My Components/Hydra/<Group>/hydra_<name>."""
    root = os.path.join(root or palette_root(), section)
    n = export_tox(dest, specs, root=root, group_case=lambda g: g)
    print('  refresh the Palette pane to pick up changes')
    return n


def build_all(dest, specs=None, layout=True, audio=True, tox=True, palette=True):
    global LIBRARY_PATH
    LIBRARY_PATH = dest.path           # so spawn() can find the library later
    specs = specs or load_specs()
    for spec in specs:
        build(spec, dest)
    if audio:
        build_audio(dest)
    if layout:
        layout_groups(dest, specs)
    if tox:
        export_tox(dest, specs)
    if palette:
        export_palette(dest, specs)
    print(f'built {len(specs)} components in {dest.path}')
