"""Propagate a hydra component's Time expression to everything downstream.

Embedded verbatim into each time-using component as a Parameter Execute DAT, so
an exported .tox carries it -- this file is the editable master, not a live
dependency. Re-run the builder after changing it.

Watches the component's `Propagatetime` pulse. On pulse, walks every operator
reachable through the component's outputs and copies this component's Time
parameter (expression if it has one, otherwise its value) onto the ones that are
hydra components -- tagged `hydra` by the builder -- and have a Time parameter.

The tag check keeps the pulse from writing into unrelated operators that happen
to expose a parameter called Time.
"""

HYDRA_TAG = 'hydra'


def _time_source(comp):
    """(is_expression, text) for the component's Time parameter."""
    p = comp.par.Time
    if p.mode == ParMode.EXPRESSION:
        return True, p.expr
    return False, p.eval()


def _downstream(comp):
    """Every operator reachable through comp's outputs, breadth first, no repeats."""
    seen, queue, found = {comp.id}, [comp], []
    while queue:
        cur = queue.pop(0)
        for connector in cur.outputConnectors:
            for conn in connector.connections:
                nxt = conn.owner
                if nxt.id in seen:
                    continue
                seen.add(nxt.id)
                queue.append(nxt)
                found.append(nxt)
    return found


def onPulse(par):
    comp = par.owner
    is_expr, source = _time_source(comp)

    touched, skipped = [], 0
    for o in _downstream(comp):
        if HYDRA_TAG not in o.tags:
            continue
        target = o.par['Time'] if 'Time' in [p.name for p in o.pars()] else None
        if target is None:
            skipped += 1          # a hydra component whose function has no time
            continue
        if is_expr:
            target.expr = source
        else:
            target.mode = ParMode.CONSTANT
            target.val = source
        touched.append(o.name)

    what = source if is_expr else f'constant {source}'
    print(f'{comp.name}: propagated Time = {what} to {len(touched)} hydra op(s)'
          + (': ' + ', '.join(touched) if touched else '')
          + (f' ({skipped} without a Time par)' if skipped else ''))
    return
