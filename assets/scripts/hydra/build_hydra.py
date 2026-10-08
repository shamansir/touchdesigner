"""Build and upgrade one TouchDesigner component per hydra function.

Each is a Base COMP around a GLSL TOP whose shader `compile` (hydra_compile.py,
embedded) generates from the function spec stored on the component. What a
component contains and every command: BUILD.md.

    b = mod('/project1/hydra/build_hydra')
    b.rebuild(op('/project1/hydra'))       # the library
    b.upgrade(op('/project1'))             # placed copies
"""

import hashlib
import json
import os
import re
import time
import types

HERE = 'assets/scripts/hydra'
TOX_ROOT = 'components/hydra'   # exported .tox tree, one folder per group
DUMP_ROOT = 'build/hydra-shaders'   # dump_shaders() writes here
DEFAULT_RES = (1280, 720)       # source components; everything else follows its input
NONCOMMERCIAL_MAX = 1280        # the free licence caps output at 1280x1280

# Shown on every component's Version page. Bump on every change to this file,
# hydra_compile.py or hydra-utils.glsl -- it is how a placed copy says which
# code it was built from. The Compiler hash on that page changes by itself.
VERSION = '0.4.0'

# custom pages, in the order every component should show them
PAGE_ORDER = ('Hydra', 'Pipeline', 'Time Sync', 'Output', 'Version')

MODES = (('compiled', 'Compiled'), ('image', 'Image'))     # first is the default


def _source_path(rel):
    """A file next to this one: through the project in TD, through __file__
    outside it (the tests)."""
    try:
        return os.path.join(project.folder, HERE, rel)
    except NameError:
        return os.path.join(os.path.dirname(os.path.abspath(__file__)), rel)


def _read(rel):
    try:
        with open(_source_path(rel)) as f:
            return f.read()
    except OSError:
        print(f'  !! cannot read {_source_path(rel)}')
        return ''


def _load_core():
    """hydra_compile.py as a module. The facts it and this file both need --
    classes, inputs, naming -- are defined there once and taken from there."""
    m = types.ModuleType('hydra_compile')
    exec(compile(_read('hydra_compile.py'), 'hydra_compile.py', 'exec'), m.__dict__)
    return m


core = _load_core()
HYDRA_TAG, SPEC_KEY = core.HYDRA_TAG, core.SPEC_KEY
CLASS_INPUTS, TEXTURE_ALIAS, UTILS = core.CLASS_INPUTS, core.TEXTURE_ALIAS, core.UTILS
inputs_for, uses_of, par_name = core.inputs_for, core.uses_of, core.par_name


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

# --- small TD helpers -------------------------------------------------------------

def set_menu(par, *needles):
    for i, n in enumerate(par.menuNames):
        if all(x in n.lower() for x in needles):
            par.menuIndex = i
            return n
    print(f'  !! {par.name}: no match for {needles} in {par.menuNames}')


def custom_names(comp):
    return {p.name for p in comp.customPars}


def get_page(comp, name):
    return next((pg for pg in comp.customPages if pg.name == name), None) \
        or comp.appendCustomPage(name)


def ensure_op(comp, optype, name, x, y):
    o = comp.op(name)
    if not o:
        o = comp.create(optype, name)
        o.nodeX, o.nodeY = x, y
    return o


def sort_pages(comp):
    """Same tab order on every component, whichever pages it happens to have."""
    have = [pg.name for pg in comp.customPages]
    ordered = [n for n in PAGE_ORDER if n in have] + [n for n in have
                                                      if n not in PAGE_ORDER]
    try:
        comp.sortCustomPages(*ordered)
    except Exception as e:
        print(f'  !! {comp.path}: could not sort pages ({e})')


def mirror_menu(page, source_par, name, label):
    """A custom menu par cloning an operator's menu, bound back to it.

    TD does not expose a COMP's internal TOP Common parameters, so anything a
    user needs to reach has to be mirrored onto the component like this.
    """
    p = page.appendMenu(name, label=label)[0]
    p.menuNames = list(source_par.menuNames)
    p.menuLabels = list(source_par.menuLabels)
    current = source_par.menuNames[source_par.menuIndex]
    p.default = p.val = current
    source_par.expr = f'parent().par.{p.name}'
    return p


def set_order(o, index):
    """Connect Order on an In OP -- the component's connector order. The only
    parameter here not in TD's offline help, hence the check."""
    if 'connectorder' in {p.name for p in o.pars()}:
        o.par.connectorder = index


# --- specs ------------------------------------------------------------------------

def load_specs():
    with open(_source_path('hydra-functions.json')) as f:
        specs = json.load(f)

    # hydra-extensions.json adds inputs hydra itself does not have (see that
    # file). Kept separate so regenerating the upstream table cannot drop them.
    ext_path = _source_path('hydra-extensions.json')
    if os.path.exists(ext_path):
        with open(ext_path) as f:
            extensions = json.load(f)
        by_name = {s['name']: s for s in specs}
        for name, extra in extensions.items():
            if name.startswith('_') or name not in by_name:
                continue
            by_name[name]['inputs'] = by_name[name]['inputs'] + extra.get('inputs', [])
    return specs


def load_utils():
    """hydra-utils.glsl split on its `// ===== name =====` markers."""
    parts = re.split(r'^// =====\s*(\w+)\s*=====\s*$', _read('hydra-utils.glsl'),
                     flags=re.M)
    return {parts[i]: parts[i + 1].strip() for i in range(1, len(parts) - 1, 2)}


def store_spec(comp, spec, utils):
    """Everything hydra_compile needs to generate this component's shader.

    Storage survives .tox save/load, so a component -- and any chain it is
    compiled into -- needs neither the JSON nor any file, only this.
    """
    body = spec.get('glsl', '')
    comp.store(SPEC_KEY, {
        'name': spec['name'],
        'type': spec['type'],
        'inputs': spec['inputs'],
        'glsl': body,
        'utils': {u: utils[u] for u in UTILS
                  if u in utils and re.search(rf'\b{u}\b', body)},
        'uses': uses_of(body),
        'alias': TEXTURE_ALIAS.get(spec['name']),
    })


# --- versions -----------------------------------------------------------------------

def compiler_hash():
    """Short hash of hydra_compile.py -- what the embedded `compile` DAT holds."""
    return hashlib.sha1(_read('hydra_compile.py').encode()).hexdigest()[:8]


VERSION_PARS = (('Version', 'version'), ('Compiler', 'compiler'), ('Built', 'built'))


def stamp_version(comp, chash=None):
    """Write the read-only Version page. Idempotent -- build and upgrade call it."""
    page = get_page(comp, 'Version')
    have = custom_names(comp)
    for name, label in VERSION_PARS:
        if name not in have:
            page.appendStr(name, label=label)
    values = {'Version': VERSION,
              'Compiler': (chash or compiler_hash()) if comp.op('compile') else '-',
              'Built': time.strftime('%Y-%m-%d %H:%M')}
    for name, value in values.items():
        p = comp.par[name]
        p.readOnly = False              # readOnly guards the UI; lift it to write
        p.val = value
        p.readOnly = True


def version_report(dest, depth=8):
    """List hydra components under `dest` not built from the current code."""
    chash = compiler_hash()
    found = _placed(dest, depth, need_glsl=False)
    stale = []
    for c in found:
        have = custom_names(c)
        v = c.par.Version.eval() if 'Version' in have else '(none)'
        h = c.par.Compiler.eval() if 'Compiler' in have else '(none)'
        if v != VERSION or (h not in ('-', chash)):
            stale.append(c)
            print(f'  {c.path}: version {v}, compiler {h}')
    print(f'current: version {VERSION}, compiler {chash} -- '
          f'{len(stale)} of {len(found)} component(s) stale'
          + (' -- run upgrade()' if stale else ''))
    return stale


# --- callbacks ----------------------------------------------------------------------

TIME_DEFAULT_EXPR = 'absTime.seconds'

def _shim(kind, callbacks):
    """Exec DAT text whose every callback forwards to the `compile` module, so
    all embedded code is hydra_compile.py, under the one hash on Version."""
    lines = [f'# {kind} callbacks -- GENERATED by build_hydra, do not edit.',
             '# The code is in the `compile` DAT (hydra_compile.py).', '']
    for sig, call in callbacks:
        lines += [f'def {sig}:', f"    op('compile').module.{call}", '    return', '']
    return '\n'.join(lines)


CHAIN_EXEC = _shim('OP Execute', [
    ('onWireChange(changeOp)', 'on_wire(parent())'),
    ('onFlagChange(changeOp, *args)', 'on_flag(parent())'),
    ('onNameChange(changeOp)', 'on_rename(parent())'),
    ('onPathChange(changeOp)', 'on_rename(parent())'),
])
PAR_EXEC = _shim('Parameter Execute', [
    ('onValueChange(par, prev)', 'on_par(par)'),
    ('onPulse(par)', 'on_par(par)'),
])


def _exec_dat(comp, optype, name, x, text, pars):
    d = ensure_op(comp, optype, name, x, -450)
    d.text = text
    d.par.op = '..'
    if pars:
        d.par.pars = pars
    return d


def install_callbacks(comp, uses):
    """The `compile` module and the shims that drive it. Idempotent.

    Embedded rather than file-synced, so an exported .tox carries its own code.
    """
    ensure_op(comp, textDAT, 'compile', 200, -450).text = _read('hydra_compile.py')

    chain = _exec_dat(comp, opexecuteDAT, 'chain_exec', 400, CHAIN_EXEC, None)
    for t in ('wirechange', 'flagchange', 'namechange', 'pathchange'):
        chain.par[t] = True

    pars = 'Mode Compile' + (' Propagatetime' if 'time' in uses else '')
    pexec = _exec_dat(comp, parameterexecuteDAT, 'par_exec', 600, PAR_EXEC, pars)
    pexec.par.valuechange = pexec.par.onpulse = True


# --- one component ------------------------------------------------------------------

def _ensure_mode(page, comp):
    if 'Mode' not in custom_names(comp):
        m = page.appendMenu('Mode', label='mode')[0]
        m.menuNames, m.menuLabels = [n for n, _ in MODES], [l for _, l in MODES]
        m.default = m.val = MODES[0][0]


def ensure(comp, spec, utils=None, chash=None):
    """Give `comp` everything its spec needs, repairing what is stale.

    Idempotent: build() runs it on an empty COMP, upgrade() on a placed copy.
    An existing copy keeps its wiring, parameter values and node positions --
    only what is missing is created, and only generated content is rewritten.
    """
    utils = utils if utils is not None else load_utils()
    name, kind = spec['name'], spec['type']
    image_inputs = inputs_for(spec)
    floats = [i for i in spec['inputs'] if i['type'] == 'float']
    vecs = [i for i in spec['inputs'] if i['type'].startswith('vec')]
    uses = uses_of(spec.get('glsl', ''))
    have = custom_names(comp)

    # --- Hydra page: one parameter per argument -------------------------------
    if floats or vecs:
        page = get_page(comp, 'Hydra')
        for inp in floats:
            pn = par_name(inp['name'])
            if pn not in have:
                p = page.appendFloat(pn, label=inp['name'])[0]
                d = float(inp['default'] or 0)
                p.default = p.val = d
                p.normMin, p.normMax = min(0.0, 2 * d), max(1.0, 2 * d)
        for inp in vecs:                # only `sum` today; no CHOP input for these
            pn = par_name(inp['name'])
            if not any(n.startswith(pn) for n in have):
                width = int(inp['type'][3:])
                pars = page.appendFloat(pn, label=inp['name'], size=width)
                for p, v in zip(pars, inp['default'] or [0] * width):
                    p.default = p.val = float(v)

    # --- Time Sync: a parameter, not an input -- rarely rewired ---------------
    if 'time' in uses:
        tpage = get_page(comp, 'Time Sync')
        if 'Time' not in have:
            t = tpage.appendFloat('Time', label='time')[0]
            t.default = 0.0
            t.expr = TIME_DEFAULT_EXPR
        if 'Propagatetime' not in have:
            tpage.appendPulse('Propagatetime', label='Propagate Time Expr')

    # --- inputs: images first, then one CHOP per argument ---------------------
    for i, label in enumerate(image_inputs):
        set_order(ensure_op(comp, inTOP, label, -600, -160 * i), i)

    for k, inp in enumerate(floats):
        hn, y = inp['name'], 200 + 160 * k
        const = comp.op(f'{hn}_default')
        if not const:
            const = ensure_op(comp, constantCHOP, f'{hn}_default', -800, y)
            const.par.const0name = hn
            const.par.const0value.expr = f'parent().par.{par_name(hn)}'
        chop = ensure_op(comp, inCHOP, hn, -600, y)
        set_order(chop, len(image_inputs) + k)
        # The In CHOP's own input is the fallback, used when the component's
        # connector is empty -- the shader reads op('<arg>')[0] and relies on
        # it always having a channel. Builds differ on how many connectors it
        # exposes, so take the last one rather than assuming an index.
        if chop.inputConnectors:
            last = chop.inputConnectors[len(chop.inputConnectors) - 1]
            if not last.connections:
                last.connect(const)
        else:
            print(f'  !! {chop.path}: no input connectors to attach the default to')

    # --- shader DAT, GLSL TOP, Out TOP ---------------------------------------
    dat = ensure_op(comp, textDAT, 'pixel', -200, -300)
    glsl = comp.op('glsl')
    if not glsl:
        glsl = ensure_op(comp, glslTOP, 'glsl', 0, 0)
        set_menu(glsl.par.format, '16', 'float')
        set_menu(glsl.par.outputresolution, 'input' if image_inputs else 'custom')
    if glsl.par.pixeldat.eval() != dat:
        glsl.par.pixeldat = dat
    out = ensure_op(comp, outTOP, 'out', 250, 0)
    if not out.inputConnectors[0].connections:
        out.inputConnectors[0].connect(glsl)

    # --- Output page: mirrors of the GLSL TOP's Common parameters -------------
    opage = get_page(comp, 'Output')
    if not image_inputs:            # a source: nothing upstream to inherit from
        if not any(n.startswith('Resolution') for n in have):
            res_pars = opage.appendInt('Resolution', label='resolution', size=2)
            for p, v in zip(res_pars, DEFAULT_RES):
                p.default = p.val = v
            glsl.par.resolutionw.expr = f'parent().par.{res_pars[0].name}'
            glsl.par.resolutionh.expr = f'parent().par.{res_pars[1].name}'
    elif 'Inputsmoothness' not in have:   # only samplers care how input is read
        mirror_menu(opage, glsl.par.inputfiltertype, 'Inputsmoothness', 'input smoothness')
    if 'Pixelformat' not in have:
        mirror_menu(opage, glsl.par.format, 'Pixelformat', 'pixel format')

    # --- Pipeline page ---------------------------------------------------------
    ppage = get_page(comp, 'Pipeline')
    _ensure_mode(ppage, comp)
    if 'Compile' not in have:
        ppage.appendPulse('Compile', label='Compile')

    # --- identity, code, version ------------------------------------------------
    comp.par.opviewer = './out'
    comp.color = GROUPS[kind][1]
    keep = {t for t in comp.tags if not t.startswith('hydra:') and t != HYDRA_TAG}
    comp.tags = keep | {HYDRA_TAG, f'hydra:fn:{name}', f'hydra:class:{kind}'}
    store_spec(comp, spec, utils)
    install_callbacks(comp, uses)
    stamp_version(comp, chash)
    sort_pages(comp)
    return comp


def compile_now(comp):
    """Generate the shader right away, rather than on the next frame."""
    c = comp.op('compile')
    if c:
        c.module.compile_now(comp)


def build(spec, dest, utils=None, chash=None):
    comp_name = f"hydra_{spec['name'].lower()}"
    old = dest.op(comp_name)
    if old:
        old.destroy()
    comp = ensure(dest.create(baseCOMP, comp_name), spec, utils, chash)
    compile_now(comp)
    print(f"{comp_name}: {spec['type']}, {len(inputs_for(spec))} TOP in, "
          f"{sum(i['type'] == 'float' for i in spec['inputs'])} CHOP in")
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
    script.par.callbacks = dat.name
    script.inputConnectors[0].connect(audio)
    script.par.setuppars.pulse()

    # bind the script's own pars to the component's, so users see one page
    for name, _, _, _, _ in specs:
        try:
            script.par[name].expr = f'parent().par.{name}'
        except Exception as e:
            print(f'  !! could not bind {name} on {script.path}: {e}')

    out = comp.create(outCHOP, 'out')
    out.nodeX, out.nodeY = 250, 0
    out.inputConnectors[0].connect(script)

    comp.par.opviewer = './out'
    comp.color = AUDIO_COLOR
    comp.tags = {HYDRA_TAG, 'hydra:fn:fft', 'hydra:class:audio'}
    stamp_version(comp)
    sort_pages(comp)
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


def expand_names(names):
    """'osc*3 rotate' or ['osc*3', 'rotate'] -> ['osc', 'osc', 'osc', 'rotate']."""
    if isinstance(names, str):
        names = names.split()
    out = []
    for n in names:
        name, _, count = n.partition('*')
        out += [name] * int(count or 1)
    return out


def spawn(names, dest=None, lib=None, postfix='', spacing=200, x=0, y=0):
    """Copies of library components: spawn('osc*2 rotate'). They land in the
    library's parent by default -- clear()/rebuild() of the library would take
    copies inside it along."""
    lib = library(lib)
    if lib is None:
        return []
    dest = dest or lib.parent()

    made, missing = [], []
    for i, name in enumerate(expand_names(names)):
        original = lib.op(f'hydra_{name.lower()}')
        if not original:
            missing.append(name)
            continue
        copy = dest.copy(original, name=unique_name(dest, f'hydra_{name.lower()}{postfix}'))
        copy.nodeX, copy.nodeY = x + spacing * len(made), y
        compile_now(copy)                  # its own shader, not the original's
        made.append(copy)

    print(f'spawned {len(made)} in {dest.path}: ' + ', '.join(c.name for c in made))
    if missing:
        print('  !! not in the library:', ', '.join(missing))
    return made

def spec_name(comp, by_name):
    """Which hydra function a placed component is, whatever it is named: the
    spec stored on it, else its `hydra:fn:` tag."""
    stored = comp.fetch(SPEC_KEY, None, search=False)
    names = [stored['name']] if stored else []
    names += [t.split(':', 2)[2] for t in comp.tags if t.startswith('hydra:fn:')]
    return next((n.lower() for n in names if n.lower() in by_name), None)


def _placed(dest, depth=8, need_glsl=True):
    """Hydra components under `dest` -- with a GLSL TOP unless need_glsl=False."""
    found = dest.findChildren(tags=[HYDRA_TAG], type=COMP, maxDepth=depth)
    return [c for c in found if c.op('glsl') or not need_glsl]


def upgrade(dest, specs=None, depth=8):
    """Run ensure() on every hydra component under `dest`, recursively: a
    placed copy ends up as a fresh build would make it, keeping its wiring,
    parameter values and node positions. Then regenerate its shader."""
    specs = specs or load_specs()
    by_name = {s['name'].lower(): s for s in specs}
    utils, chash = load_utils(), compiler_hash()

    fixed, skipped, failed = [], [], []
    for comp in _placed(dest, depth, need_glsl=False):
        if 'hydra:fn:fft' in comp.tags:
            stamp_version(comp, chash)          # built by build_audio, has no spec
            sort_pages(comp)
            continue
        name = spec_name(comp, by_name)
        if name is None:
            skipped.append(comp.path)
            continue
        try:
            ensure(comp, by_name[name], utils, chash)
            compile_now(comp)
            fixed.append(comp.name)
        except Exception as e:                  # never let one bad comp stop the sweep
            failed.append(f'{comp.path} ({e})')

    print(f'upgraded {len(fixed)} component(s) under {dest.path} to {VERSION}')
    if skipped:
        print('  !! could not identify:', ', '.join(skipped))
    if failed:
        print('  !! errored:', '; '.join(failed))
    return fixed


def set_mode(dest, mode, depth=8):
    """Set Mode on every hydra component under `dest`: 'compiled' or 'image'.

    Each change triggers its own recompile, collapsed to one per frame.
    """
    changed = []
    for c in _placed(dest, depth):
        if 'Mode' in custom_names(c) and mode in c.par.Mode.menuNames:
            c.par.Mode = mode
            changed.append(c.name)
    print(f'Mode = {mode} on {len(changed)} component(s)')
    return changed


def cook_report(dest, depth=8):
    """Last-frame CPU and GPU cook times of every hydra GLSL TOP under `dest`.

    Components that did not cook this frame report 0 -- that is the saving of
    Compiled mode: only outputs and viewed components cook.
    """
    rows, cpu, gpu = [], 0.0, 0.0
    for c in _placed(dest, depth):
        g = c.op('glsl')
        ct, gt = getattr(g, 'cookTime', 0.0), getattr(g, 'gpuCookTime', 0.0)
        if getattr(g, 'cookFrame', -1) < absTime.frame - 1:
            ct = gt = 0.0
        cpu, gpu = cpu + ct, gpu + gt
        mode = c.par.Mode.eval() if 'Mode' in custom_names(c) else '-'
        rows.append((c.path, mode, ct, gt))
    for path, mode, ct, gt in sorted(rows, key=lambda r: -r[3]):
        print(f'{gt:8.3f} ms gpu {ct:8.3f} ms cpu  {mode:9} {path}')
    print(f'total: {gpu:.3f} ms gpu, {cpu:.3f} ms cpu over {len(rows)} component(s)')
    return gpu, cpu


def set_resolution(dest, width, height, depth=8):
    """Retune every hydra source component's output resolution under `dest`."""
    n = 0
    for child in _placed(dest, depth):
        pars = sorted(child.pars('Resolution*'), key=lambda p: p.name)
        if len(pars) >= 2:
            pars[0].val, pars[1].val = width, height
            n += 1
    print(f'set {n} source component(s) to {width}x{height}')
    if max(width, height) > NONCOMMERCIAL_MAX:
        print(f'  !! over {NONCOMMERCIAL_MAX}px -- a non-commercial licence will '
              f'clamp this')
    return n


def set_output(dest, smoothness=None, pixel_format=None, depth=8):
    """Input Smoothness / Pixel Format on every hydra component under `dest`,
    matched by substring of the menu text: set_output(dest, smoothness='nearest')."""
    smoothed = formatted = 0
    for child in _placed(dest, depth):
        if smoothness and 'Inputsmoothness' in {p.name for p in child.pars()}:
            set_menu(child.par.Inputsmoothness, smoothness.lower())
            smoothed += 1
        if pixel_format and 'Pixelformat' in {p.name for p in child.pars()}:
            set_menu(child.par.Pixelformat, *pixel_format.lower().split())
            formatted += 1
    if smoothness:
        print(f'set input smoothness on {smoothed} component(s)')
    if pixel_format:
        print(f'set pixel format on {formatted} component(s)')


def dump_shaders(dest, root=None, depth=8):
    """Write each hydra component's generated shader under `dest` to
    <root>/<path below dest>.frag. Export only; see BUILD.md."""
    root = root or _source_path(os.path.join('..', '..', '..', DUMP_ROOT))
    root = os.path.normpath(root)
    os.makedirs(root, exist_ok=True)
    written = 0
    for c in _placed(dest, depth):
        dat = c.op('pixel')
        if not dat or not dat.text.strip():
            continue
        rel = c.path[len(dest.path):].strip('/').replace('/', '__') or c.name
        with open(os.path.join(root, f'{rel}.frag'), 'w') as f:
            f.write(dat.text)
        written += 1
    print(f'wrote {written} shader(s) to {root}')
    return written


def is_annotate(o):
    return getattr(o, 'OPType', '') == 'annotateCOMP' or o.name.startswith('group_')


def clear(dest, components=True, annotations=True):
    """DESTRUCTIVE: every hydra_* COMP and every Annotate COMP directly in
    `dest`, hand-made annotations included. DATs survive (`hydra_seq`)."""
    removed = []
    for child in list(dest.children):
        if components and child.isCOMP and (HYDRA_TAG in child.tags
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


def layout_groups(dest, specs):
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
        note.par.Titletext = label
        note.par.Bodytext = f'hydra {label} -- {len(items)} functions'
        note.color = col                      # header keeps the full group colour
        # body: same hue at 60% value -- scaling RGB uniformly keeps hue/saturation
        note.par.Backcolorr, note.par.Backcolorg, note.par.Backcolorb = \
            (c * 0.6 for c in col)

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
    utils, chash = load_utils(), compiler_hash()
    for spec in specs:
        build(spec, dest, utils, chash)
    if audio:
        build_audio(dest)
    if layout:
        layout_groups(dest, specs)
    if tox:
        export_tox(dest, specs)
    if palette:
        export_palette(dest, specs)
    print(f'built {len(specs)} components in {dest.path}')
