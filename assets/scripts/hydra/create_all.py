# hydra slice builder -- right-click -> Run Script  (Textport open)
root = me.parent()

SPEC = {
    #  name        class     uniforms: (name, value-or-expression, is_expr)
    "osc": (
        "src",
        [
            ("time", "absTime.seconds", True),
            ("frequency", 60, False),
            ("sync", 0.1, False),
            ("offset", 0, False),
        ],
    ),
    "noise": (
        "src",
        [("time", "absTime.seconds", True), ("scale", 10, False), ("offset", 0, False)],
    ),
    "rotate": (
        "coord",
        [("time", "absTime.seconds", True), ("angle", 10, False), ("speed", 0, False)],
    ),
    "modulate": ("mod", [("amount", 0.1, False)]),
}

WIRING = {"rotate": [("osc", 0)], "modulate": [("rotate", 0), ("noise", 1)]}


def set_menu(par, *needles):
    for i, n in enumerate(par.menuNames):
        if all(x in n.lower() for x in needles):
            par.menuIndex = i
            return n
    print(f"  !! {par.name}: no match for {needles} in {par.menuNames}")


def fill_vec(top, uniforms):
    top.seq.vec.numBlocks = max(len(uniforms), 1)
    for i, (uname, val, is_expr) in enumerate(uniforms):
        top.par[f"vec{i}name"] = uname
        for c in "xyzw":  # reset all four components first
            p = top.par[f"vec{i}value{c}"]
            p.mode = ParMode.CONSTANT
            p.val = 0
        p0 = top.par[f"vec{i}valuex"]
        if is_expr:
            p0.expr = val  # setting .expr switches mode automatically
        else:
            p0.val = val


# --- 1. wipe -----------------------------------------------------------------
for n in SPEC:
    for name in (n, f"{n}_pixel"):
        existing = root.op(name)
        if existing:
            existing.destroy()

# --- 2. build ----------------------------------------------------------------
tops = {}
for i, (n, (kind, uniforms)) in enumerate(SPEC.items()):
    dat = root.create(textDAT, f"{n}_pixel")
    dat.par.file = f"assets/scripts/hydra/{n}.frag"
    dat.par.syncfile = True
    dat.par.loadonstart = True
    dat.nodeX, dat.nodeY = 200 * i, -200

    top = root.create(glslTOP, n)
    top.nodeX, top.nodeY = 200 * i, 0
    top.par.pixeldat = dat
    set_menu(top.par.format, "16", "float")
    set_menu(top.par.outputresolution, *(("custom",) if kind == "src" else ("input",)))
    fill_vec(top, uniforms)
    tops[n] = top
    print(f"{n}: {len(uniforms)} uniforms, shader {dat.par.file.eval()}")

# --- 3. wire -----------------------------------------------------------------
for dst, srcs in WIRING.items():
    for src, idx in srcs:
        tops[dst].inputConnectors[idx].connect(tops[src])

print("done:", ", ".join(tops))
