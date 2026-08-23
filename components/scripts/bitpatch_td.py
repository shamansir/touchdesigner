"""
bitpatch_td.py -- builds a "bit patch bay" COMP inside TouchDesigner.

Run once from the TouchDesigner textport:

    p = '/Users/shamansir/Workspace/touchdesigner/components/scripts/bitpatch_td.py'
    exec(open(p).read())
    build()          # creates /project1/bitpatch
    build_demo()     # same, plus a Video Device In and a Null already wired up
    repair()         # update the code inside an existing one, keeping its patch

repair(), diag() and preset() locate the COMP by its contents, not by name, so
renaming it is fine. Pass one explicitly to disambiguate:

    repair(op('/project1/bitpatch_v2'))

Using it:

    Feed anything into the COMP's input and take the result off its output.
    The COMP's node viewer is the grid itself -- click cells right there, or
    open it bigger in a Panel pane.

    It starts on the `identity` patch, which routes every input bit to its own
    position: a deliberate passthrough. Pick another Preset, or start clicking,
    to see anything happen.

    Grid: one row per OUTPUT bit (highest on top), one column per source.
    Click a cell to toggle that source into or out of that output bit's XOR
    set. Click "clr" to zero an output bit entirely. The Patch Spec parameter
    shows the current patch as a spec string, and accepts one (edit it, then
    pulse Apply Spec) -- the same syntax bitpatch.py's --patch takes.

What it creates (a Container COMP -- its node viewer is the patch bay grid):

    in1            In TOP        -- feed it a Video Device In TOP (or anything)
    patch          Table DAT     -- the routing matrix, rows = out bits, cols = sources
    mask           Script TOP    -- compiles the matrix into a 4-channel bitfield texture
    mask_cb        Text DAT      -- Script TOP callbacks
    shader         Text DAT      -- the GLSL fragment shader
    bend           GLSL TOP      -- does the actual bit routing, on the GPU
    out1           Out TOP
    bay            List COMP     -- the clickable patch bay grid (open its panel)
    bay_cb         Text DAT      -- List COMP callbacks
    lib            Text DAT      -- shared patch logic (presets, spec parsing)
    parexec        Parameter Execute DAT

Model (same as bitpatch.py):

    out_bit[b] = src1 XOR src2 XOR ... XOR srcN

    iN  input bit N          cN  column-index bit N      p  parity of (row+col)
    rN  row-index bit N      1   constant one            n  static noise bit

Differences from the Raspberry Pi script, and why:

  * Sources are per COLOUR CHANNEL, not per Bayer sample. Video Device In gives
    8-bit RGB, so the default is 8 bits and the same patch runs on R, G and B
    independently. Per-channel enable toggles let you bend only one channel.
  * ~iN (inverted source) is gone because it is redundant: XOR is commutative,
    so inverting any single source in an output bit's set is exactly the same
    as adding the constant `1` source. One toggle state, not two.
  * Everything runs in a GLSL TOP, not numpy, so it is realtime at 1080p.
    The LUT/positional-mask split from the original is unnecessary on a GPU --
    the shader evaluates the routing per pixel directly, reading the matrix
    from a tiny bits x 1 texture.
"""

# ---------------------------------------------------------------------------
# lib DAT -- patch logic shared by the callbacks
# ---------------------------------------------------------------------------

LIB = '''
# Shared patch-bay logic. Reached as parent().op('lib').module

KIND_COLOR = {
    'i': (0.47, 0.86, 0.47),
    'c': (0.43, 0.70, 0.86),
    'r': (0.78, 0.55, 0.86),
    'p': (0.94, 0.78, 0.47),
    '1': (0.70, 0.70, 0.70),
    'n': (0.55, 0.55, 0.94),
}


def source_list(bits):
    """All selectable sources, in grid-column order."""
    s = [('i', k) for k in range(bits)]
    s += [('c', k) for k in range(bits)]
    s += [('r', k) for k in range(bits)]
    s += [('p', 0), ('1', 0), ('n', 0)]
    return s


def source_label(src):
    kind, k = src
    return '%s%d' % (kind, k) if kind in ('i', 'c', 'r') else kind


def parse_source(tok, bits):
    tok = tok.strip().lstrip('~')      # ~ is accepted but folded away, see below
    if not tok or tok == '0':
        return None
    if len(tok) > 1 and tok[0] in 'icr' and tok[1:].isdigit():
        k = int(tok[1:])
        if k >= bits:
            raise ValueError('source %s out of range for %d bits' % (tok, bits))
        return (tok[0], k)
    if tok in ('p', '1', 'n'):
        return (tok, 0)
    raise ValueError('unknown source token: %r' % tok)


def empty_matrix(bits):
    return [[0] * len(source_list(bits)) for _ in range(bits)]


def spec_to_matrix(spec, bits):
    """'7=i0^c7,0=i7' -> matrix[out_bit][source_index] of 0/1.

    A '~' prefix on a source is folded into the constant `1` source, which is
    the same thing under XOR.
    """
    srcs = source_list(bits)
    idx = {s: i for i, s in enumerate(srcs)}
    one = idx[('1', 0)]
    mat = empty_matrix(bits)
    for part in spec.split(','):
        part = part.strip()
        if not part:
            continue
        if '=' not in part:
            raise ValueError('malformed entry %r, expected outbit=sources' % part)
        lhs, rhs = part.split('=', 1)
        b = int(lhs)
        if not 0 <= b < bits:
            raise ValueError('output bit %d out of range for %d bits' % (b, bits))
        row = [0] * len(srcs)
        for tok in rhs.split('^'):
            inverted = tok.strip().startswith('~')
            s = parse_source(tok, bits)
            if s is None:
                continue
            row[idx[s]] ^= 1
            if inverted:
                row[one] ^= 1
        mat[b] = row
    return mat


def matrix_to_spec(mat, bits):
    srcs = source_list(bits)
    parts = []
    for b in range(bits - 1, -1, -1):
        on = [source_label(srcs[j]) for j, v in enumerate(mat[b]) if v]
        if on:
            parts.append('%d=%s' % (b, '^'.join(on)))
    return ','.join(parts)      # empty string == every output bit is 0


def presets(bits):
    half = bits // 2
    p = {}
    p['identity'] = ','.join('%d=i%d' % (b, b) for b in range(bits))
    p['bitreverse'] = ','.join('%d=i%d' % (b, bits - 1 - b) for b in range(bits))
    p['rotate'] = ','.join('%d=i%d' % (b, (b - half) % bits) for b in range(bits))
    p['xorcolumn'] = ','.join('%d=i%d^c%d' % (b, b, b) for b in range(bits))
    p['xorrow'] = ','.join('%d=i%d^r%d' % (b, b, b) for b in range(bits))
    p['xorshift3'] = ','.join(
        ('%d=i%d^i%d' % (b, b, b + 3)) if b + 3 < bits else ('%d=i%d' % (b, b))
        for b in range(bits))
    p['parityflip'] = ','.join(
        ('%d=i%d^p' % (b, b)) if b % 2 else ('%d=i%d' % (b, b)) for b in range(bits))
    p['invert'] = ','.join('%d=i%d^1' % (b, b) for b in range(bits))
    p['nibbleswap'] = ','.join('%d=i%d' % (b, (b + half) % bits) for b in range(bits))
    p['noise'] = ','.join('%d=i%d^n' % (b, b) for b in range(bits))
    p['clear'] = ''
    return p


PRESET_ORDER = ['identity', 'bitreverse', 'rotate', 'xorcolumn', 'xorrow',
                'xorshift3', 'parityflip', 'invert', 'nibbleswap', 'noise', 'clear']


# -- storage: the matrix lives in the patch Table DAT --------------------------

def bits_of(comp):
    return int(comp.par.Bits.eval())


def menu_name(par):
    """The selected menu entry as a string.

    Par.eval() on a menu parameter is not dependably a string across builds,
    and an int here silently KeyErrors inside presets(). menuIndex always is
    an int we can look up ourselves, so go through that.
    """
    try:
        return par.menuNames[par.menuIndex]
    except Exception:
        return str(par.eval())


def read_matrix(comp):
    bits = bits_of(comp)
    nsrc = len(source_list(bits))
    dat = comp.op('patch')
    if dat.numRows != bits or dat.numCols != nsrc:
        return empty_matrix(bits)
    return [[1 if dat[b, j].val.strip() == '1' else 0 for j in range(nsrc)]
            for b in range(bits)]


def write_matrix(comp, mat):
    bits = bits_of(comp)
    dat = comp.op('patch')
    dat.clear()
    dat.setSize(bits, len(source_list(bits)))
    for b in range(bits):
        for j, v in enumerate(mat[b]):
            dat[b, j] = str(v)
    comp.par.Spec.val = matrix_to_spec(mat, bits)
    write_labeled(comp, mat)
    comp.op('mask').cook(force=True)


def write_labeled(comp, mat):
    """Mirror the matrix into a self-describing table for the COMP's DAT out.

    Same 0/1 values, plus a header row of source names and a leading column of
    output-bit names, so anything downstream can index it by label instead of
    having to know source_list()'s ordering.
    """
    dat = comp.op('patch_labeled')
    if dat is None:
        return
    bits = bits_of(comp)
    srcs = source_list(bits)
    dat.clear()
    dat.appendRow(['outbit'] + [source_label(s) for s in srcs])
    for b in range(bits - 1, -1, -1):        # highest bit first, like the grid
        dat.appendRow(['out%d' % b] + [str(v) for v in mat[b]])


def cell_target(comp, name, index=None):
    """Map a MIDI channel to a (out_bit, source_index) cell, or None.

    Two conventions, tried in this order:
      by name      'b3_i5', 'b0_p'  -- output bit 3, source i5
      by position  channel index i  -- row-major, i = out_bit * nsources + src
    Naming is what you want for hand-wired controls; position is what you want
    when a grid controller's pads already come in as an ordered block.
    """
    bits = bits_of(comp)
    srcs = source_list(bits)
    if name and '_' in name:
        head, _, tail = name.partition('_')
        if head.startswith('b') and head[1:].isdigit():
            b = int(head[1:])
            for j, s in enumerate(srcs):
                if source_label(s) == tail and b < bits:
                    return b, j
            return None
    if index is None:
        return None
    b, j = divmod(int(index), len(srcs))
    return (b, j) if b < bits else None


def set_cell(comp, out_bit, src_index, value):
    """Returns True if the matrix actually changed."""
    mat = read_matrix(comp)
    value = 1 if value else 0
    if mat[out_bit][src_index] == value:
        return False
    mat[out_bit][src_index] = value
    write_matrix(comp, mat)
    return True


def refresh_bay(comp):
    """Redraw the grid without resizing it."""
    comp.op('bay').par.reset.pulse()


def toggle(comp, out_bit, src_index):
    mat = read_matrix(comp)
    mat[out_bit][src_index] ^= 1
    write_matrix(comp, mat)
    return mat


def clear_row(comp, out_bit):
    mat = read_matrix(comp)
    mat[out_bit] = [0] * len(mat[out_bit])
    write_matrix(comp, mat)
    return mat


def load_spec(comp, spec):
    write_matrix(comp, spec_to_matrix(spec, bits_of(comp)))
    rebuild_bay(comp)


def load_preset(comp, name):
    load_spec(comp, presets(bits_of(comp))[name])


def rebuild_bay(comp):
    """Resize the grid to the current bit depth and re-run its init callbacks."""
    bits = bits_of(comp)
    bay = comp.op('bay')
    bay.par.rows = bits + 1
    bay.par.cols = len(source_list(bits)) + 2
    bay.par.reset.pulse()
'''


# ---------------------------------------------------------------------------
# mask Script TOP callbacks -- matrix -> bitfield texture
# ---------------------------------------------------------------------------

MASK_CB = '''
# Compiles the routing matrix into a (bits x 1) RGBA 32-bit float texture.
#
#   R = bitfield of which iN sources feed this output bit
#   G = bitfield of which cN sources feed it
#   B = bitfield of which rN sources feed it
#   A = 1:parity  2:constant one  4:noise
#
# Each field stays under 2**16 so it is exactly representable in a float32,
# which lets the shader do one texelFetch per output bit instead of one per
# (output bit, source) pair.

import numpy as np


def onCook(scriptOp):
    comp = scriptOp.parent()
    lib = comp.op('lib').module
    bits = lib.bits_of(comp)
    mat = lib.read_matrix(comp)
    srcs = lib.source_list(bits)

    arr = np.zeros((1, bits, 4), dtype=np.float32)
    for b in range(bits):
        acc = [0, 0, 0, 0]
        for j, (kind, k) in enumerate(srcs):
            if not mat[b][j]:
                continue
            if kind == 'i':
                acc[0] |= 1 << k
            elif kind == 'c':
                acc[1] |= 1 << k
            elif kind == 'r':
                acc[2] |= 1 << k
            elif kind == 'p':
                acc[3] |= 1
            elif kind == '1':
                acc[3] |= 2
            elif kind == 'n':
                acc[3] |= 4
        arr[0, b] = acc

    scriptOp.copyNumpyArray(arr)
    return
'''


# ---------------------------------------------------------------------------
# The shader
# ---------------------------------------------------------------------------

SHADER = '''
// bitpatch -- XOR routing of input/positional bits to output bit positions.
//
// out_bit[b] = XOR of every source enabled for b, evaluated independently for
// R, G and B. The routing matrix arrives as a (bits x 1) texture of bitfields
// on input 1, so the whole patch costs one texelFetch per output bit.

uniform float uBits;        // bit depth to operate at (8 for 8-bit video)
uniform float uSeed;        // noise seed
uniform vec4  uChan;        // xyz = per-channel enable, w = dry/wet mix

layout(location = 0) out vec4 fragColor;

int hashbit(ivec2 p, int seed)
{
    // static per pixel, not per frame -- matches the `n` source in bitpatch.py
    uint x = uint(p.x) * 73856093u ^ uint(p.y) * 19349663u ^ uint(seed) * 83492791u;
    x ^= x >> 13; x *= 0x5bd1e995u; x ^= x >> 15;
    return int(x & 1u);
}

void main()
{
    int bits = int(uBits + 0.5);
    int maxv = (1 << bits) - 1;

    // texelFetch, not texture(): no filtering can ever blend two samples and
    // hand us a value that is not a real quantisation step.
    ivec2 xy  = ivec2(gl_FragCoord.xy);
    vec4  src = texelFetch(sTD2DInputs[0], xy, 0);
    ivec3 iv  = ivec3(round(clamp(src.rgb, 0.0, 1.0) * float(maxv)));

    int   par = (xy.x + xy.y) & 1;
    int   nz  = hashbit(xy, int(uSeed));

    ivec3 ov = ivec3(0);
    for (int b = 0; b < 16; ++b)
    {
        if (b >= bits) break;

        vec4 m  = texelFetch(sTD2DInputs[1], ivec2(b, 0), 0);
        int  mi = int(m.r + 0.5);     // input-bit sources
        int  mc = int(m.g + 0.5);     // column-bit sources
        int  mr = int(m.b + 0.5);     // row-bit sources
        int  mx = int(m.a + 0.5);     // parity / const / noise

        ivec3 acc = ivec3(0);
        for (int k = 0; k < 16; ++k)
        {
            if (k >= bits) break;
            if (((mi >> k) & 1) == 1) acc ^= (iv >> k) & 1;
            if (((mc >> k) & 1) == 1) acc ^= ivec3((xy.x >> k) & 1);
            if (((mr >> k) & 1) == 1) acc ^= ivec3((xy.y >> k) & 1);
        }
        if ((mx & 1) != 0) acc ^= ivec3(par);
        if ((mx & 2) != 0) acc ^= ivec3(1);
        if ((mx & 4) != 0) acc ^= ivec3(nz);

        ov |= acc << b;
    }

    vec3 bent = vec3(ov) / float(maxv);
    vec3 res  = mix(src.rgb, bent, clamp(uChan.rgb * uChan.w, 0.0, 1.0));
    fragColor = TDOutputSwizzle(vec4(res, src.a));
}
'''


# ---------------------------------------------------------------------------
# List COMP callbacks -- the clickable grid
# ---------------------------------------------------------------------------

BAY_CB = '''
# Patch bay grid.
#
#   row 0            source labels
#   rows 1..bits     output bits, highest bit on top
#   col 0            "outN" label
#   col 1            "clr"  -- clears that output bit (sets it to constant 0)
#   cols 2..         one per source; click toggles it into/out of the XOR set

CELL = 24
LABEL_W = 46
CLR_W = 34

OFF_BG = (0.13, 0.13, 0.14, 1.0)
HDR_BG = (0.09, 0.09, 0.10, 1.0)
LBL_BG = (0.17, 0.17, 0.18, 1.0)
GRID = (0.26, 0.26, 0.27, 1.0)


def _ctx(comp):
    p = comp.parent()
    return p, p.op('lib').module


def _cell(lib, mat, bits, row, col):
    """Returns (text, textColor, bgColor) for a grid cell."""
    srcs = lib.source_list(bits)
    if row == 0:
        if col == 0:
            return ('out', (0.6, 0.6, 0.6), HDR_BG)
        if col == 1:
            return ('', (0.6, 0.6, 0.6), HDR_BG)
        s = srcs[col - 2]
        return (lib.source_label(s), lib.KIND_COLOR[s[0]], HDR_BG)

    b = bits - row                      # highest output bit on top
    if col == 0:
        return ('out%d' % b, (0.85, 0.85, 0.85), LBL_BG)
    if col == 1:
        return ('clr', (0.75, 0.45, 0.45), LBL_BG)

    j = col - 2
    s = srcs[j]
    if mat[b][j]:
        return ('\\u25cf', lib.KIND_COLOR[s[0]], OFF_BG)
    return ('\\u00b7', (0.32, 0.32, 0.33), OFF_BG)


def onInitTable(comp, attribs):
    attribs.textJustify = JustifyType.CENTER
    attribs.bgColor = OFF_BG
    attribs.leftBorderOutColor = GRID
    attribs.topBorderOutColor = GRID
    return


def onInitCol(comp, col, attribs):
    attribs.colWidth = LABEL_W if col == 0 else (CLR_W if col == 1 else CELL)
    attribs.colStretch = 0
    return


def onInitRow(comp, row, attribs):
    attribs.rowHeight = CELL
    return


def onInitCell(comp, row, col, attribs):
    p, lib = _ctx(comp)
    bits = lib.bits_of(p)
    mat = lib.read_matrix(p)
    text, tcol, bg = _cell(lib, mat, bits, row, col)
    attribs.text = text
    attribs.textColor = list(tcol) + [1.0]
    attribs.bgColor = bg
    return


def _refresh_row(comp, lib, mat, bits, row):
    for col in range(2, comp.par.cols.eval()):
        text, tcol, bg = _cell(lib, mat, bits, row, col)
        a = comp.cellAttribs[row, col]
        a.text = text
        a.textColor = list(tcol) + [1.0]
    return


def onSelect(comp, startRow, startCol, startCoords, endRow, endCol, endCoords,
             start, end):
    if not start or startRow < 1 or startCol < 0:
        return
    p, lib = _ctx(comp)
    bits = lib.bits_of(p)
    b = bits - startRow

    if startCol == 0:
        return
    if startCol == 1:
        mat = lib.clear_row(p, b)
    else:
        mat = lib.toggle(p, b, startCol - 2)
    _refresh_row(comp, lib, mat, bits, startRow)
    return


def onRollover(comp, row, col, coords, prevRow, prevCol, prevCoords):
    return


def onRadio(comp, row, col, prevRow, prevCol):
    return


def onFocus(comp, row, col, prevRow, prevCol):
    return


def onEdit(comp, row, col, val):
    return
'''


# ---------------------------------------------------------------------------
# Parameter Execute DAT
# ---------------------------------------------------------------------------

PAREXEC = '''
def onValueChange(par, prev):
    comp = par.owner
    lib = comp.op('lib').module
    try:
        if par.name == 'Bits':
            try:
                lib.load_spec(comp, comp.par.Spec.eval())
            except Exception:
                lib.load_preset(comp, 'identity')
        elif par.name == 'Preset':
            lib.load_preset(comp, lib.menu_name(par))
    except Exception as e:
        print('bitpatch: %s change failed: %s' % (par.name, e))
    return


def onPulse(par):
    comp = par.owner
    lib = comp.op('lib').module
    try:
        if par.name == 'Applyspec':
            lib.load_spec(comp, comp.par.Spec.eval())
        elif par.name == 'Reload':
            lib.rebuild_bay(comp)
            comp.op('mask').cook(force=True)
    except Exception as e:
        print('bitpatch: %s failed: %s' % (par.name, e))
    return
'''


# ---------------------------------------------------------------------------
# CHOP Execute DAT -- MIDI (or any CHOP) driving the grid
# ---------------------------------------------------------------------------

CHOP_EXEC = '''
# Any CHOP wired into the COMP's CHOP input can flip grid cells.
#
# Channel -> cell mapping, see lib.cell_target():
#   named     'b3_i5' -> output bit 3, source i5.  Rename channels with a
#             MIDI In Map CHOP (or a Rename CHOP) and wire only what you need.
#   positional  channel index i -> out_bit i // nsources, source i % nsources.
#             For a pad grid that already arrives as one ordered block.
#
# MIDI Mode:
#   toggle  flip the cell on each rising edge -- what momentary pads want,
#           since they send 127 on press and 0 on release
#   set     cell follows the value directly (>0.5 on), for latching controls


def onValueChange(channel, sampleIndex, val, prev):
    comp = me.parent()
    if not comp.par.Midienable.eval():
        return
    lib = comp.op('lib').module
    try:
        t = lib.cell_target(comp, channel.name, channel.index)
        if t is None:
            return
        b, j = t
        if lib.menu_name(comp.par.Midimode) == 'toggle':
            if val > 0.5 and (prev is None or prev <= 0.5):
                lib.toggle(comp, b, j)
            else:
                return
        else:
            if not lib.set_cell(comp, b, j, val > 0.5):
                return
        lib.refresh_bay(comp)
    except Exception as e:
        print('bitpatch: MIDI %s failed: %s' % (channel.name, e))
    return
'''


# ---------------------------------------------------------------------------
# builder
# ---------------------------------------------------------------------------

def build(dest=None, name='bitpatch'):
    dest = dest or op('/project1') or root
    if dest.op(name):
        dest.op(name).destroy()
    # a Container, not a Base, so the patch bay grid IS the node's viewer --
    # otherwise the UI is invisible until you dig into the COMP by hand
    comp = dest.create(containerCOMP, name)

    # -- custom parameters ---------------------------------------------------
    page = comp.appendCustomPage('Bitpatch')

    pbits = page.appendInt('Bits', label='Bits')[0]
    pbits.normMin, pbits.normMax = 1, 16
    pbits.clampMin = pbits.clampMax = True
    pbits.min, pbits.max = 1, 16
    pbits.default = 8
    pbits.val = 8

    ppre = page.appendMenu('Preset', label='Preset')[0]
    names = ['identity', 'bitreverse', 'rotate', 'xorcolumn', 'xorrow',
             'xorshift3', 'parityflip', 'invert', 'nibbleswap', 'noise', 'clear']
    ppre.menuNames = names
    ppre.menuLabels = names

    pspec = page.appendStr('Spec', label='Patch Spec')[0]
    page.appendPulse('Applyspec', label='Apply Spec')
    page.appendPulse('Reload', label='Rebuild Bay')

    for ch, dflt in (('Chanr', 1), ('Chang', 1), ('Chanb', 1)):
        t = page.appendToggle(ch, label='Bend ' + ch[-1].upper())[0]
        t.default = dflt
        t.val = dflt

    pmix = page.appendFloat('Mix', label='Mix')[0]
    pmix.normMin, pmix.normMax = 0, 1
    pmix.default = 1
    pmix.val = 1

    pseed = page.appendInt('Seed', label='Noise Seed')[0]
    pseed.default = 0
    pseed.val = 0

    # -- DATs ----------------------------------------------------------------
    lib = comp.create(textDAT, 'lib')
    lib.text = LIB
    lib.nodeX, lib.nodeY = -400, 200

    mask_cb = comp.create(textDAT, 'mask_cb')
    mask_cb.text = MASK_CB
    mask_cb.nodeX, mask_cb.nodeY = -400, 100

    shader = comp.create(textDAT, 'shader')
    shader.text = SHADER
    shader.nodeX, shader.nodeY = -400, 0

    bay_cb = comp.create(textDAT, 'bay_cb')
    bay_cb.text = BAY_CB
    bay_cb.nodeX, bay_cb.nodeY = -400, -100

    patch = comp.create(tableDAT, 'patch')
    patch.clear()
    patch.nodeX, patch.nodeY = -400, -200

    pex = comp.create(parameterexecuteDAT, 'parexec')
    pex.text = PAREXEC
    _wire_parexec(comp, pex)
    pex.nodeX, pex.nodeY = -400, -300

    # -- TOP chain -----------------------------------------------------------
    tin = comp.create(inTOP, 'in1')
    tin.nodeX, tin.nodeY = -200, 0

    mask = comp.create(scriptTOP, 'mask')
    mask.par.callbacks = 'mask_cb'
    _try(mask, 'format', 'rgba32float')
    mask.nodeX, mask.nodeY = -200, -120

    bend = comp.create(glslTOP, 'bend')
    bend.par.pixeldat = 'shader'
    bend.nodeX, bend.nodeY = 0, 0
    bend.inputConnectors[0].connect(tin)
    bend.inputConnectors[1].connect(mask)
    # 8-bit fixed is exact for 8-bit routing; above that we need real precision
    # or the /maxv normalisation loses low bits on the way out.
    _expr(bend, 'format', "'rgba32float' if parent().par.Bits > 8 else 'rgba8fixed'")

    try:
        bend.seq.vec.numBlocks = 3
    except Exception:
        pass
    _try(bend, 'vec0name', 'uBits')
    _expr(bend, 'vec0valuex', 'parent().par.Bits')
    _try(bend, 'vec1name', 'uSeed')
    _expr(bend, 'vec1valuex', 'parent().par.Seed')
    _try(bend, 'vec2name', 'uChan')
    _expr(bend, 'vec2valuex', 'parent().par.Chanr')
    _expr(bend, 'vec2valuey', 'parent().par.Chang')
    _expr(bend, 'vec2valuez', 'parent().par.Chanb')
    _expr(bend, 'vec2valuew', 'parent().par.Mix')

    tout = comp.create(outTOP, 'out1')
    tout.nodeX, tout.nodeY = 200, 0
    tout.inputConnectors[0].connect(bend)

    # -- the patch bay panel -------------------------------------------------
    bay = comp.create(listCOMP, 'bay')
    bay.par.callbacks = 'bay_cb'
    bay.par.lockfirstrow = True
    bay.par.lockfirstcol = True
    bay.nodeX, bay.nodeY = 0, -250

    # size the grid (and the container around it) to fit 8 bits' worth of
    # columns: out label + clr + (3 * bits + 3) sources, plus the header row
    bay_w = 46 + 34 + (3 * 8 + 3) * 24 + 4
    bay_h = 24 * 9 + 4
    bay.par.w, bay.par.h = bay_w, bay_h
    bay.par.x, bay.par.y = 0, 0
    comp.par.w, comp.par.h = bay_w, bay_h

    # -- DAT output and MIDI input -------------------------------------------
    _ensure_extras(comp)

    # -- initial state -------------------------------------------------------
    m = lib.module
    m.load_preset(comp, 'identity')
    comp.par.Preset.val = 'identity'

    comp.viewer = True
    print('built %s' % comp.path)
    print('  the node viewer IS the patch bay -- click cells there')
    print('  custom parameters (Bits, Preset, Patch Spec, Mix) are on the COMP')
    print('  it starts on the identity patch, which is a deliberate passthrough')
    return comp


def _page(comp, name='Bitpatch'):
    for p in comp.customPages:
        if p.name == name:
            return p
    return comp.appendCustomPage(name)


def _ensure_extras(comp):
    """Add the DAT output and the MIDI input, on a new or an existing COMP.

    Split out of build() so repair() can retrofit them onto a bitpatch you
    already have wired into a project and saved as a .tox.
    """
    page = _page(comp)

    if not hasattr(comp.par, 'Midienable'):
        t = page.appendToggle('Midienable', label='MIDI Enable')[0]
        t.default = 1
        t.val = 1
    if not hasattr(comp.par, 'Midimode'):
        m = page.appendMenu('Midimode', label='MIDI Mode')[0]
        m.menuNames = ['toggle', 'set']
        m.menuLabels = ['Toggle on rising edge', 'Set from value']
        m.val = 'toggle'

    lab = comp.op('patch_labeled')
    if lab is None:
        lab = comp.create(tableDAT, 'patch_labeled')
        lab.clear()
        lab.nodeX, lab.nodeY = 0, -400
    if comp.op('outdat') is None:
        o = comp.create(outDAT, 'outdat')
        o.nodeX, o.nodeY = 200, -400
        o.inputConnectors[0].connect(lab)

    if comp.op('inchop') is None:
        c = comp.create(inCHOP, 'inchop')
        c.nodeX, c.nodeY = -400, -500
    ce = comp.op('chopexec')
    if ce is None:
        ce = comp.create(chopexecuteDAT, 'chopexec')
        ce.nodeX, ce.nodeY = -200, -500
    ce.text = CHOP_EXEC
    _try(ce, 'active', True)
    _try(ce, 'chop', 'inchop')
    _try(ce, 'channel', '*')
    _try(ce, 'valuechange', True)

    # repopulate the labeled table from whatever patch is currently loaded
    m = comp.op('lib').module
    m.write_labeled(comp, m.read_matrix(comp))


def _wire_parexec(comp, pex):
    """Point the Parameter Execute DAT at the COMP that owns the custom pars.

    A bare '.' in the OPs field is a relative path that these OP-specifier
    fields do not reliably resolve, and when it fails the DAT simply watches
    nothing -- no error, no callbacks. An expression yielding the absolute path
    is unambiguous, and it follows the COMP if it gets renamed or moved.
    """
    _try(pex, 'active', True)
    _expr(pex, 'op', 'me.parent().path')
    _try(pex, 'pars', 'Bits Preset Applyspec Reload')
    _try(pex, 'valuechange', True)
    _try(pex, 'onpulse', True)


def find(comp=None):
    """Resolve the bitpatch COMP to work on.

    Accepts an OP, a path string, or nothing. With nothing, searches for a COMP
    that looks like a bitpatch (has the lib and bay children) rather than
    assuming a name -- renaming the COMP is normal and should not break the
    tooling. Ambiguity is reported instead of guessed at.
    """
    if comp is not None:
        found = comp if hasattr(comp, 'path') else op(comp)
        if found is None:
            raise RuntimeError('no such operator: %r' % (comp,))
        return found
    hits = []
    for base in (op('/project1'), root):
        if base is None:
            continue
        for c in base.findChildren(type=COMP, depth=4):
            if c.op('lib') and c.op('bay') and c.op('patch'):
                if c not in hits:
                    hits.append(c)
    if not hits:
        raise RuntimeError('no bitpatch COMP found; pass one, e.g. '
                           "repair(op('/project1/bitpatch_v2'))")
    if len(hits) > 1:
        print('note: %d bitpatch COMPs found (%s), using the first'
              % (len(hits), ', '.join(c.path for c in hits)))
    return hits[0]


def diag(comp=None):
    """Print what the parameter plumbing is actually resolving to."""
    comp = find(comp)
    pex = comp.op('parexec')
    print('comp        : %s (%s)' % (comp.path, comp.type))
    print('parexec     : %s' % (pex.path if pex else 'MISSING'))
    if pex:
        print('  active    : %r' % pex.par.active.eval())
        print('  op raw    : %r  expr %r' % (pex.par.op.val, pex.par.op.expr))
        print('  op eval   : %r' % pex.par.op.eval())
        try:
            print('  op -> OPs : %r' % [o.path for o in pex.par.op.evalOPs()])
        except Exception as e:
            print('  op -> OPs : FAILED (%s)' % e)
        print('  pars      : %r' % pex.par.pars.eval())
        print('  valuechange %r  onpulse %r'
              % (pex.par.valuechange.eval(), pex.par.onpulse.eval()))
        print('  errors    : %r' % pex.errors())
    p = comp.par.Preset
    print('Preset      : val %r  menuIndex %r  eval %r (%s)'
          % (p.val, p.menuIndex, p.eval(), type(p.eval()).__name__))
    print('  menuNames : %r' % (p.menuNames,))
    print('Spec        : %r' % comp.par.Spec.eval())
    print('patch DAT   : %d rows x %d cols' % (comp.op('patch').numRows,
                                               comp.op('patch').numCols))
    print('bay         : rows %r cols %r' % (comp.op('bay').par.rows.eval(),
                                             comp.op('bay').par.cols.eval()))
    return


def preset(name, comp=None):
    """Load a preset directly, bypassing the parameter callbacks entirely."""
    comp = find(comp)
    comp.op('lib').module.load_preset(comp, name)
    comp.par.Preset.val = name
    print('loaded %r -> %s' % (name, comp.par.Spec.eval()))
    return comp


def repair(comp=None):
    """Update the scripts inside an EXISTING bitpatch without rebuilding it.

    Use this on a COMP you already saved as a .tox and wired into a project --
    build() would destroy and replace it, losing your current patch. This only
    rewrites the code DATs, so the patch table, parameters and connections
    survive.
    """
    comp = find(comp)
    for name, text in (('lib', LIB), ('mask_cb', MASK_CB), ('shader', SHADER),
                       ('bay_cb', BAY_CB), ('parexec', PAREXEC)):
        d = comp.op(name)
        if d is None:
            print('  missing %s, skipped' % name)
            continue
        d.text = text
    if comp.op('parexec'):
        _wire_parexec(comp, comp.op('parexec'))
    _ensure_extras(comp)
    comp.op('lib').module.rebuild_bay(comp)
    comp.op('mask').cook(force=True)
    print('repaired %s' % comp.path)
    return comp


def build_demo(dest=None):
    """Wires a Video Device In straight into a fresh bitpatch, for trying it out."""
    dest = dest or op('/project1') or root
    comp = build(dest)
    cam = dest.op('bitpatch_cam') or dest.create(videodeviceinTOP, 'bitpatch_cam')
    cam.nodeX, cam.nodeY = comp.nodeX - 200, comp.nodeY
    comp.inputConnectors[0].connect(cam)
    nul = dest.op('bitpatch_out') or dest.create(nullTOP, 'bitpatch_out')
    nul.nodeX, nul.nodeY = comp.nodeX + 200, comp.nodeY
    # a COMP has to be connected by its output connector, not by the COMP itself
    nul.inputConnectors[0].connect(comp.outputConnectors[0])
    nul.viewer = True
    return comp


def _try(o, parname, val):
    try:
        getattr(o.par, parname).val = val
    except Exception as e:
        print('  note: could not set %s.%s = %r (%s)' % (o.name, parname, val, e))


def _expr(o, parname, expr):
    try:
        getattr(o.par, parname).expr = expr
    except Exception as e:
        print('  note: could not set expr %s.%s (%s)' % (o.name, parname, e))
