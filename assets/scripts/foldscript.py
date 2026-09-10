import math


def onSetupParameters(scriptOp):
    return onSetupParameters_v5(scriptOp)


def onSetupParameters_v4(scriptOp):
    p = scriptOp.appendCustomPage("Fold")
    c = p.appendInt("Count", label="Count")[0]
    cm = p.appendInt("Maxcount", label="Max. Count")[0]
    c.default, c.val, c.min, c.clampMin = 50, 50, 1, True
    cm.default, cm.val, cm.min, cm.max, cm.clampMin = 200, 200, 1, 5000, True
    for name, label, dflt, min, max in (
        ("Sweep", "Total Sweep (deg)", 360.0, 0.0, 360.0),
        ("Startscale", "Start Scale", 1.0, 0.0, 3.0),
        ("Endscale", "End Scale", 1.0, 0.0, 3.0),
        ("Starttx", "Start Translate X", 0.0, 0.0, 5000.0),
        ("Startty", "Start Translate Y", 0.0, 0.0, 5000.0),
        ("Endtx", "End Translate X", 0.0, 0.0, 5000.0),
        ("Endty", "End Translate Y", 0.0, 0.0, 5000.0),
        ("Pivotx", "Pivot X", 0.0, -1.0, 1.0),
        ("Pivoty", "Pivot Y", -0.25, -1.0, 1.0),
    ):
        f = p.appendFloat(name, label=label)[0]
        f.default, f.val, f.min, f.max = dflt, dflt, min, max
    return


def onSetupParameters_v5(scriptOp):
    p = scriptOp.appendCustomPage("Fold")

    m = p.appendInt("Maxcount", label="Max Count")[0]
    m.default, m.val, m.min, m.clampMin = 200, 200, 1, True
    m.normMin, m.normMax = 1, 5000

    n = p.appendFloat("Count", label="Count")[0]
    n.default, n.val, n.min, n.clampMin = 50, 50, 1, True
    n.normMin, n.normMax = 1, 5000

    #  name           label                default  soft lo  soft hi  hard min
    for name, label, dflt, lo, hi, hmin in (
        ("Startx", "Start X", 0.0, -1.0, 1.0, None),
        ("Starty", "Start Y", 0.25, -1.0, 1.0, None),
        ("Startrotate", "Start Rotate (deg)", 0.0, -180.0, 180.0, None),
        ("Startscale", "Start Scale", 1.0, 0.0, 4.0, 0.0),
        ("Sweep", "Total Sweep (deg)", 360.0, -720.0, 720.0, None),
        ("Endscale", "End Scale", 1.0, 0.0, 4.0, 0.0),
        ("Endtx", "End Drift X", 0.0, -1.0, 1.0, None),
        ("Endty", "End Drift Y", 0.0, -1.0, 1.0, None),
        ("Spin", "Spin With Orbit", 1.0, -2.0, 2.0, None),
    ):
        f = p.appendFloat(name, label=label)[0]
        f.default, f.val = dflt, dflt
        f.normMin, f.normMax = lo, hi
        if hmin is not None:
            f.min, f.clampMin = hmin, True
    return


def onCook(scriptOp):
    return onCook_v5(scriptOp)


def onCook_v5(scriptOp):
    M = int(scriptOp.par.Maxcount)
    Nf = max(scriptOp.par.Count.eval(), 1.0)
    span = max(Nf - 1.0, 1.0)

    s0 = max(scriptOp.par.Startscale.eval(), 1e-6)
    s1 = max(scriptOp.par.Endscale.eval(), 1e-6)
    step = math.radians(scriptOp.par.Sweep.eval() / span)
    k_mul = (s1 / s0) ** (1.0 / span)  # ratio between neighbours
    tx = scriptOp.par.Endtx.eval() / span
    ty = scriptOp.par.Endty.eval() / span
    spin = scriptOp.par.Spin.eval()

    scriptOp.clear()
    ch = {n: scriptOp.appendChan(n) for n in ("tx", "ty", "rz", "sx", "sy", "u")}
    scriptOp.numSamples = M

    # --- the seed: absolute, never divided by span ---
    x = scriptOp.par.Startx.eval()
    y = scriptOp.par.Starty.eval()
    ang = scriptOp.par.Startrotate.eval()
    k = s0

    for i in range(M):
        on = min(max(Nf - i, 0.0), 1.0)

        ch["tx"][i] = x
        ch["ty"][i] = y
        ch["rz"][i] = ang
        ch["sx"][i] = k * on
        ch["sy"][i] = k * on
        ch["u"][i] = min(i, span) / span

        # --- the step: rotate about the origin, scale toward it, then drift ---
        ca, sa = math.cos(step), math.sin(step)
        x, y = (x * ca - y * sa) * k_mul + tx, (x * sa + y * ca) * k_mul + ty
        ang += math.degrees(step) * spin
        k *= k_mul
    return


def onCook_v4(scriptOp):
    M = int(scriptOp.par.Maxcount)
    Nf = max(scriptOp.par.Count.eval(), 1.0)
    span = max(Nf - 1.0, 1.0)
    step = math.radians(scriptOp.par.Sweep.eval() / span)
    # ik = max(scriptOp.par.Startscale.eval(), 1e-6) ** (1.0 / span)
    ik = max(scriptOp.par.Startscale.eval(), 1e-6) ** (1.0 / span)
    k_mul = max(scriptOp.par.Endscale.eval(), 1e-6) ** (1.0 / span)
    itx = scriptOp.par.Starttx.eval() / span
    ity = scriptOp.par.Startty.eval() / span
    tx = scriptOp.par.Endtx.eval() / span
    ty = scriptOp.par.Endty.eval() / span
    px = scriptOp.par.Pivotx.eval()
    py = scriptOp.par.Pivoty.eval()

    scriptOp.clear()
    ch = {n: scriptOp.appendChan(n) for n in ("tx", "ty", "rz", "sx", "sy", "u")}
    scriptOp.numSamples = M  # constant -- never reallocates

    x, y, ang, k = itx, ity, 0.0, ik

    for i in range(M):
        on = min(max(Nf - i, 0.0), 1.0)  # 1 inside, 0 outside, fractional at the edge

        ch["tx"][i] = x
        ch["ty"][i] = y
        ch["rz"][i] = ang
        ch["sx"][i] = k * on  # on == 0 -> degenerate quad, no pixels
        ch["sy"][i] = k * on
        ch["u"][i] = min(i, span) / span

        dx, dy = x - px, y - py
        ca, sa = math.cos(step), math.sin(step)
        x = px + (dx * ca - dy * sa) * k_mul + tx
        y = py + (dx * sa + dy * ca) * k_mul + ty
        ang += math.degrees(step)
        k *= k_mul
    return


def onCook_v3(scriptOp):
    M = int(scriptOp.par.Maxcount)
    Nf = max(scriptOp.par.Count.eval(), 1.0)
    span = max(Nf - 1.0, 1.0)
    step = math.radians(scriptOp.par.Sweep.eval() / span)
    k_mul = max(scriptOp.par.Endscale.eval(), 1e-6) ** (1.0 / span)
    tx = scriptOp.par.Endtx.eval() / span
    ty = scriptOp.par.Endty.eval() / span
    px = scriptOp.par.Pivotx.eval()
    py = scriptOp.par.Pivoty.eval()

    scriptOp.clear()
    ch = {n: scriptOp.appendChan(n) for n in ("tx", "ty", "rz", "sx", "sy", "u")}
    scriptOp.numSamples = M  # constant -- never reallocates

    x, y, ang, k = 0.0, 0.0, 0.0, 1.0

    for i in range(M):
        on = min(max(Nf - i, 0.0), 1.0)  # 1 inside, 0 outside, fractional at the edge

        ch["tx"][i] = x
        ch["ty"][i] = y
        ch["rz"][i] = ang
        ch["sx"][i] = k * on  # on == 0 -> degenerate quad, no pixels
        ch["sy"][i] = k * on
        ch["u"][i] = min(i, span) / span

        dx, dy = x - px, y - py
        ca, sa = math.cos(step), math.sin(step)
        x = px + (dx * ca - dy * sa) * k_mul + tx
        y = py + (dx * sa + dy * ca) * k_mul + ty
        ang += math.degrees(step)
        k *= k_mul
    return


def onCook_v2(scriptOp):
    M = int(scriptOp.par.Maxcount)
    Nf = max(scriptOp.par.Count.eval(), 1.0)
    span = max(Nf - 1.0, 1.0)
    step = math.radians(scriptOp.par.Sweep.eval() / span)
    k_mul = max(scriptOp.par.Endscale.eval(), 1e-6) ** (1.0 / span)
    tx = scriptOp.par.Endtx.eval() / span
    ty = scriptOp.par.Endty.eval() / span
    px = scriptOp.par.Pivotx.eval()
    py = scriptOp.par.Pivoty.eval()

    scriptOp.clear()
    ch = {n: scriptOp.appendChan(n) for n in ("tx", "ty", "rz", "sx", "sy", "u")}
    scriptOp.numSamples = M  # constant -- never reallocates

    x, y, ang, k = 0.0, 0.0, 0.0, 1.0

    for i in range(M):
        on = min(max(Nf - i, 0.0), 1.0)  # 1 inside, 0 outside, fractional at the edge

        ch["tx"][i] = x
        ch["ty"][i] = y
        ch["rz"][i] = ang
        ch["sx"][i] = k * on  # on == 0 -> degenerate quad, no pixels
        ch["sy"][i] = k * on
        ch["u"][i] = min(i, span) / span

        dx, dy = x - px, y - py
        ca, sa = math.cos(step), math.sin(step)
        x = px + (dx * ca - dy * sa) * k_mul + tx
        y = py + (dx * sa + dy * ca) * k_mul + ty
        ang += math.degrees(step)
        k *= k_mul
    return


def onCook_v1(scriptOp):
    # N = int(scriptOp.par.Count)
    # step = math.radians(scriptOp.par.Rotate.eval())
    # k_mul = scriptOp.par.Scalestep.eval()
    # tx = scriptOp.par.Tx.eval()
    # ty = scriptOp.par.Ty.eval()
    # px = scriptOp.par.Pivotx.eval()
    # py = scriptOp.par.Pivoty.eval()

    N = int(scriptOp.par.Count)
    span = max(N - 1, 1)
    step = math.radians(scriptOp.par.Sweep.eval() / span)
    k_mul = max(scriptOp.par.Endscale.eval(), 1e-6) ** (1.0 / span)
    tx = scriptOp.par.Endtx.eval() / span
    ty = scriptOp.par.Endty.eval() / span
    px = scriptOp.par.Pivotx.eval()
    py = scriptOp.par.Pivoty.eval()

    scriptOp.clear()
    ch = {n: scriptOp.appendChan(n) for n in ("tx", "ty", "rz", "sx", "sy")}
    scriptOp.numSamples = N

    # the accumulator: position, cumulative angle, cumulative scale
    x, y, ang, k = 0.0, 0.0, 0.0, 1.0

    for i in range(N):
        ch["tx"][i] = x
        ch["ty"][i] = y
        ch["rz"][i] = ang
        ch["sx"][i] = k
        ch["sy"][i] = k

        # ------------------------------------------------------------------
        # THE FOLD STEP -- the only part you change. Everything above and
        # below is bookkeeping. This applies M once: rotate the current
        # position about the pivot, scale it toward the pivot, then translate.
        # ------------------------------------------------------------------
        dx, dy = x - px, y - py
        ca, sa = math.cos(step), math.sin(step)
        x = px + (dx * ca - dy * sa) * k_mul + tx
        y = py + (dx * sa + dy * ca) * k_mul + ty
        # ang += scriptOp.par.Rotate.eval()
        ang += math.degrees(step)
        k *= k_mul
        # ------------------------------------------------------------------
    return


def onCook_v0(scriptOp):
    N = int(scriptOp.par.Count)
    step = math.radians(scriptOp.par.Rotate.eval())
    k_mul = scriptOp.par.Scalestep.eval()
    tx = scriptOp.par.Tx.eval()
    ty = scriptOp.par.Ty.eval()
    px = scriptOp.par.Pivotx.eval()
    py = scriptOp.par.Pivoty.eval()

    scriptOp.clear()
    ch = {n: scriptOp.appendChan(n) for n in ("tx", "ty", "rz", "sx", "sy")}
    scriptOp.numSamples = N

    # the accumulator: position, cumulative angle, cumulative scale
    x, y, ang, k = 0.0, 0.0, 0.0, 1.0

    for i in range(N):
        ch["tx"][i] = x
        ch["ty"][i] = y
        ch["rz"][i] = ang
        ch["sx"][i] = k
        ch["sy"][i] = k

        # ------------------------------------------------------------------
        # THE FOLD STEP -- the only part you change. Everything above and
        # below is bookkeeping. This applies M once: rotate the current
        # position about the pivot, scale it toward the pivot, then translate.
        # ------------------------------------------------------------------
        dx, dy = x - px, y - py
        ca, sa = math.cos(step), math.sin(step)
        x = px + (dx * ca - dy * sa) * k_mul + tx
        y = py + (dx * sa + dy * ca) * k_mul + ty
        ang += scriptOp.par.Rotate.eval()
        k *= k_mul
        # ------------------------------------------------------------------
    return
