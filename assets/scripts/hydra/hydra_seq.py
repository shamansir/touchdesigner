"""hydra's array-argument semantics, for TouchDesigner parameter expressions.

In hydra any numeric argument can be an array that cycles over time:

    osc([10, 20, 30])                       // steps, one value per beat
    osc([10, 20, 30].fast(2))               // twice as fast
    osc([10, 20, 30].smooth())              // linear interpolation
    osc([10, 20, 30].ease('easeInOutCubic'))

The equivalent here goes in any parameter expression field -- typically the
`vecNvaluex` field of a hydra component's GLSL TOP, or the component's own
custom parameter:

    mod('/project1/hydra/hydra_seq').val([10, 20, 30])
    mod('/project1/hydra/hydra_seq').val([10, 20, 30], speed=2)
    mod('/project1/hydra/hydra_seq').val([10, 20, 30], ease='easeInOutCubic')

Reading absTime inside the call registers the time dependency, so the
parameter re-evaluates every frame on its own.

Ported from src/lib/array-utils.js and src/lib/easing-functions.js.
Timing matches hydra: index = time * speed * (bpm/60), default bpm 30
(hydra-synth.js line 55) -- i.e. one step every two seconds at speed 1.
"""

import math

try:                      # available in DAT module scope
    _abs_time = absTime
except NameError:         # ...but be explicit if it is not
    import td
    _abs_time = td.absTime


DEFAULT_BPM = 30


# --- easing-functions.js, verbatim in behaviour --------------------------------
EASING = {
    'linear':         lambda t: t,
    'easeInQuad':     lambda t: t * t,
    'easeOutQuad':    lambda t: t * (2 - t),
    'easeInOutQuad':  lambda t: 2 * t * t if t < .5 else -1 + (4 - 2 * t) * t,
    'easeInCubic':    lambda t: t ** 3,
    'easeOutCubic':   lambda t: (t - 1) ** 3 + 1,
    'easeInOutCubic': lambda t: 4 * t ** 3 if t < .5 else (t - 1) * (2 * t - 2) ** 2 + 1,
    'easeInQuart':    lambda t: t ** 4,
    'easeOutQuart':   lambda t: 1 - (t - 1) ** 4,
    'easeInOutQuart': lambda t: 8 * t ** 4 if t < .5 else 1 - 8 * (t - 1) ** 4,
    'easeInQuint':    lambda t: t ** 5,
    'easeOutQuint':   lambda t: 1 + (t - 1) ** 5,
    'easeInOutQuint': lambda t: 16 * t ** 5 if t < .5 else 1 + 16 * (t - 1) ** 5,
    'sin':            lambda t: (1 + math.sin(math.pi * t - math.pi / 2)) / 2,
}


def _modulo(n, d):
    """JS modulo (always non-negative), not Python's -- matches array-utils.js."""
    return ((n % d) + d) % d


def val(arr, speed=1.0, smooth=0.0, ease=None, offset=0.0, bpm=DEFAULT_BPM, time=None):
    """One value from `arr`, chosen by the clock. Mirrors arrayUtils.getValue.

    speed   -- hydra's .fast(n)
    smooth  -- hydra's .smooth(n); 0 = hard steps
    ease    -- name from EASING, or a callable; implies smooth=1 like hydra does
    offset  -- hydra's .offset(n), fraction of one step
    time    -- override the clock (for testing); defaults to absTime.seconds
    """
    if not arr:
        return 0.0
    if ease is not None and smooth == 0:
        smooth = 1.0

    t = _abs_time.seconds if time is None else time
    n = len(arr)
    index = t * speed * (bpm / 60.0) + (offset % 1.0)

    if smooth == 0:
        return arr[int(math.floor(_modulo(index, n)))]

    fn = ease if callable(ease) else EASING.get(ease or 'linear', EASING['linear'])
    i = index - smooth / 2.0
    curr = arr[int(math.floor(_modulo(i, n)))]
    nxt = arr[int(math.floor(_modulo(i + 1, n)))]
    k = min(_modulo(i, 1.0) / smooth, 1.0)
    return fn(k) * (nxt - curr) + curr


def fit(arr, low=0.0, high=1.0):
    """hydra's .fit() -- rescale an array into [low, high]."""
    lo, hi = min(arr), max(arr)
    if hi == lo:
        return [low for _ in arr]
    return [(v - lo) * (high - low) / (hi - lo) + low for v in arr]
