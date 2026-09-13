"""Build one Base COMP per hydra function, wrapping the GLSL TOP and its shader.

Each component:

    hydra_osc/
      source, modulator   In TOPs    (class-dependent; none for `src`)
      args                In CHOP    (channel per hydra input; overrides the par)
      pixel               Text DAT   (file-synced to assets/scripts/hydra/<name>.frag)
      glsl                GLSL TOP   (internals -- users never open this)
      out                 Out TOP
    + custom page 'Hydra', one float par per hydra input

Argument resolution, per input, in order:
    1. a channel named after the hydra input on the `args` In CHOP
    2. the component's custom parameter -- which may itself hold any expression,
       including mod('...hydra_seq').val([...]) for hydra's array arguments

Run from the Textport once a Text DAT is synced to this file:

    mod('/project1/base1/build_hydra').build_all(op('/project1/base1'))
"""

import json
import os

HERE = 'assets/scripts/hydra'

# image inputs per hydra function class; `source` is always input 0
CLASS_INPUTS = {
    'src':          [],
    'coord':        ['source'],
    'color':        ['source'],
    'combine':      ['source', 'with'],
    'combineCoord': ['source', 'modulator'],   # modulator is hydra's _c0
}

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


def shader_text(name):
    """Read the .frag from disk to see which built-in uniforms it declares."""
    path = os.path.join(project.folder, HERE, f'{name}.frag')
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


def _uniforms(spec, text):
    """[(uniform name, (expr per component,))] in Vectors-page order."""
    out = []
    if 'uniform float time' in text:
        out.append(('time', ('absTime.seconds',)))
    if 'uniform vec2 resolution' in text:
        out.append(('resolution', ('me.width', 'me.height')))
    for inp in spec['inputs']:
        if inp['type'] != 'float':
            continue                      # sampler2D / vec4 are handled as TOP inputs
        n = inp['name']
        out.append((n, (
            f"op('args').chan('{n}').eval() if op('args').chan('{n}') "
            f"else parent().par.{par_name(n)}",
        )))
    return out


def build(spec, dest):
    name, kind = spec['name'], spec['type']
    comp_name = f'hydra_{name.lower()}'

    old = dest.op(comp_name)
    if old:
        old.destroy()
    comp = dest.create(baseCOMP, comp_name)

    # --- custom parameters ----------------------------------------------------
    floats = [i for i in spec['inputs'] if i['type'] == 'float']
    if floats:
        page = comp.appendCustomPage('Hydra')
        for inp in floats:
            p = page.appendFloat(par_name(inp['name']), label=inp['name'])[0]
            d = float(inp['default'] or 0)
            p.default = p.val = d
            p.normMin, p.normMax = min(0.0, 2 * d), max(1.0, 2 * d)

    # --- internals ------------------------------------------------------------
    text = shader_text(name)

    dat = comp.create(textDAT, 'pixel')
    dat.par.file = f'{HERE}/{name}.frag'
    dat.par.syncfile = True
    dat.par.loadonstart = True
    dat.nodeX, dat.nodeY = -200, -250

    in_tops = []
    for i, label in enumerate(CLASS_INPUTS[kind]):
        t = comp.create(inTOP, label)
        t.nodeX, t.nodeY = -400, -160 * i
        in_tops.append(t)

    args = comp.create(inCHOP, 'args')
    args.nodeX, args.nodeY = -400, 160

    glsl = comp.create(glslTOP, 'glsl')
    glsl.nodeX, glsl.nodeY = 0, 0
    glsl.par.pixeldat = dat
    set_menu(glsl.par.format, '16', 'float')
    set_menu(glsl.par.outputresolution, *(('input',) if in_tops else ('custom',)))
    for i, t in enumerate(in_tops):
        glsl.inputConnectors[i].connect(t)

    out = comp.create(outTOP, 'out')
    out.nodeX, out.nodeY = 250, 0
    out.inputConnectors[0].connect(glsl)

    # --- uniforms -------------------------------------------------------------
    uniforms = _uniforms(spec, text)
    glsl.seq.vec.numBlocks = max(len(uniforms), 1)
    for i, (uname, exprs) in enumerate(uniforms):
        glsl.par[f'vec{i}name'] = uname
        for c in 'xyzw':
            p = glsl.par[f'vec{i}value{c}']
            p.mode = ParMode.CONSTANT
            p.val = 0
        for c, expr in zip('xyzw', exprs):
            glsl.par[f'vec{i}value{c}'].expr = expr

    print(f'{comp_name}: {kind}, {len(in_tops)} TOP in, {len(uniforms)} uniforms')
    return comp


def build_all(dest, specs=None):
    specs = specs or load_specs()
    for i, spec in enumerate(specs):
        comp = build(spec, dest)
        comp.nodeX, comp.nodeY = 250 * (i % 8), -250 * (i // 8)
    print(f'built {len(specs)} components in {dest.path}')
