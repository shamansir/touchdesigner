import math


def onSetupParameters(scriptOp):
    p = scriptOp.appendCustomPage("Fold")
    c = p.appendInt("Count", label="Count")[0]
    c.default, c.val, c.min, c.clampMin = 50, 50, 1, True
    for name, label, dflt in (
        ("Maxcount", "Max. Count", 200),
        ("Sweep", "Total Sweep (deg)", 360.0),
        ("Endscale", "End Scale", 1.0),
        ("Endtx", "End Translate X", 0.0),
        ("Endty", "End Translate Y", 0.0),
        ("Pivotx", "Pivot X", 0.0),
        ("Pivoty", "Pivot Y", -0.25),
    ):
        f = p.appendFloat(name, label=label)[0]
        f.default, f.val = dflt, dflt
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


def onCook(scriptOp):
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
