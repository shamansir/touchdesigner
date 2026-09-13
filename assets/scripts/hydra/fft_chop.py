"""hydra's `a.fft[n]` as a Script CHOP.

Port of src/lib/audio.js (which uses Meyda's `loudness` feature). The chain:

    windowed FFT -> amplitude spectrum
    -> 24 Bark bands, each summed then raised to ^0.23   (specific loudness)
    -> grouped into `Bins` sums                          (hydra's spacing reduce)
    -> one-pole smoothing against the previous frame     (hydra's smooth)
    -> max(0, (bin - cutoff) / scale)                    (hydra's fft[])

Input: any audio-rate CHOP (Audio Device In, Audio File In, ...). All input
channels are mixed to mono.

Output: one sample, channels `fft_0` .. `fft_<Bins-1>`, plus `vol` (hydra's
`a.vol`, the total loudness before cutoff/scale).

Attach as the callbacks DAT of a Script CHOP, then pulse Setup Parameters.

Note on absolute levels: hydra's cutoff=2 / scale=10 defaults are tuned to
Meyda's spectrum scaling, which depends on its windowing and normalisation.
The *shape* here matches; the absolute magnitudes may not, so expect to retune
Cutoff and Scale for your input. Watch the `vol` channel to pick them.
"""

import numpy as np

NUM_BARK_BANDS = 24


def onSetupParameters(scriptOp):
    p = scriptOp.appendCustomPage('Fft')

    b = p.appendInt('Bins', label='Bins')[0]
    b.default, b.val = 4, 4
    b.min, b.clampMin = 1, True
    b.max, b.clampMax = NUM_BARK_BANDS, True
    b.normMin, b.normMax = 1, 8

    for name, label, dflt, lo, hi in (
        ('Cutoff', 'Cutoff', 2.0, 0.0, 20.0),
        ('Scale', 'Scale', 10.0, 0.01, 50.0),
        ('Smooth', 'Smooth', 0.4, 0.0, 0.99),
    ):
        f = p.appendFloat(name, label=label)[0]
        f.default, f.val = dflt, dflt
        f.normMin, f.normMax = lo, hi

    s = p.appendInt('Buffer', label='Buffer Size')[0]
    s.default, s.val = 512, 512          # Meyda's default analysis window
    s.min, s.clampMin = 64, True
    s.normMin, s.normMax = 128, 4096
    return


def _bark_limits(size, rate, store):
    """Bin index boundaries for 24 equal divisions of the Bark range.

    Mirrors Meyda's createBarkScale + bbLimits: the range is split evenly in
    Bark units, not at the textbook band edges.
    """
    key = (size, round(rate))
    if store.get('bark_key') == key:
        return store['bark']

    n = size // 2
    freqs = np.arange(n) * rate / size
    bark = 13 * np.arctan(freqs / 1315.8) + 3.5 * np.arctan((freqs / 7518.0) ** 2)

    limits = [0] * (NUM_BARK_BANDS + 1)
    step = bark[-1] / NUM_BARK_BANDS
    current, end = 1, step
    for i in range(n):
        while bark[i] > end and current < NUM_BARK_BANDS:
            limits[current] = i
            current += 1
            end = current * step
    limits[NUM_BARK_BANDS] = n - 1

    store['bark_key'], store['bark'] = key, limits
    return limits


def onCook(scriptOp):
    nbins = int(scriptOp.par.Bins)
    size = int(scriptOp.par.Buffer)
    smooth = float(scriptOp.par.Smooth)
    cutoff = float(scriptOp.par.Cutoff)
    scale = max(float(scriptOp.par.Scale), 1e-6)

    # An audio input is Time Sliced and the Script CHOP inherits that, but this
    # emits one analysis value per frame, so take control of the sample count.
    scriptOp.isTimeSlice = False

    scriptOp.clear()
    chans = [scriptOp.appendChan(f'fft_{i}') for i in range(nbins)]
    vol_chan = scriptOp.appendChan('vol')
    scriptOp.numSamples = 1

    src = scriptOp.inputs[0] if scriptOp.inputs else None
    if src is None or src.numChans == 0 or src.numSamples == 0:
        for c in chans:
            c[0] = 0.0
        vol_chan[0] = 0.0
        return

    store = scriptOp.storage

    # --- rolling buffer: audio arrives in per-frame blocks, Meyda wants a window
    block = np.mean([np.array(ch.vals, dtype=np.float64) for ch in src.chans()],
                    axis=0)
    buf = store.get('buf')
    if buf is None or len(buf) != size:
        buf = np.zeros(size, dtype=np.float64)
    buf = np.concatenate([buf, block])[-size:]
    store['buf'] = buf

    # --- amplitude spectrum -> specific loudness over 24 Bark bands
    rate = src.rate or 44100.0
    limits = _bark_limits(size, rate, store)
    amp = np.abs(np.fft.rfft(buf * np.hanning(size)))
    specific = np.array([
        amp[limits[i]:limits[i + 1]].sum() ** 0.23
        for i in range(NUM_BARK_BANDS)
    ])

    # --- reduce to `nbins`, exactly as hydra does (trailing bands are dropped
    #     when 24 is not divisible by nbins -- floor(24/nbins) is hydra's spacing)
    spacing = NUM_BARK_BANDS // nbins
    raw = np.array([specific[i * spacing:(i + 1) * spacing].sum()
                    for i in range(nbins)])

    prev = store.get('bins')
    if prev is None or len(prev) != nbins:
        prev = np.zeros(nbins)
    bins = raw * (1.0 - smooth) + prev * smooth
    store['bins'] = bins

    fft = np.maximum(0.0, (bins - cutoff) / scale)
    for i, c in enumerate(chans):
        c[0] = float(fft[i])
    vol_chan[0] = float(specific.sum())
    return
