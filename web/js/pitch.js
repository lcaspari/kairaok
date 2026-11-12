// Pitch detection for a singing voice (YIN algorithm).
// Given a short slice of microphone audio it returns the sung frequency.

export function hzToMidi(hz) {
  return 69 + 12 * Math.log2(hz / 440);
}

export function createDetector(sampleRate, size = 2048, minHz = 70, maxHz = 1050) {
  const tauMin = Math.max(2, Math.floor(sampleRate / maxHz));
  const tauMax = Math.min((size >> 1) - 1, Math.ceil(sampleRate / minHz));
  const win = size - tauMax;
  const cmnd = new Float32Array(tauMax + 1);
  const THRESHOLD = 0.15;

  return function detect(buf) {
    let energy = 0;
    for (let i = 0; i < size; i++) energy += buf[i] * buf[i];
    const rms = Math.sqrt(energy / size);
    if (rms < 0.006) return { hz: 0, rms, clarity: 0 };

    // cumulative mean normalised difference function
    cmnd[0] = 1;
    let running = 0;
    for (let tau = 1; tau <= tauMax; tau++) {
      let sum = 0;
      for (let i = 0; i < win; i++) {
        const d = buf[i] - buf[i + tau];
        sum += d * d;
      }
      running += sum;
      cmnd[tau] = running > 0 ? (sum * tau) / running : 1;
    }

    // first dip under the threshold, followed down to its local minimum
    let tau = -1;
    for (let t = tauMin; t <= tauMax; t++) {
      if (cmnd[t] < THRESHOLD) {
        while (t + 1 <= tauMax && cmnd[t + 1] < cmnd[t]) t++;
        tau = t;
        break;
      }
    }
    if (tau < 0) {
      let best = tauMin;
      for (let t = tauMin + 1; t <= tauMax; t++) if (cmnd[t] < cmnd[best]) best = t;
      if (cmnd[best] > 0.3) return { hz: 0, rms, clarity: 1 - cmnd[best] };
      tau = best;
    }

    // parabolic interpolation for sub-sample accuracy
    let refined = tau;
    if (tau > 1 && tau < tauMax) {
      const a = cmnd[tau - 1], b = cmnd[tau], c = cmnd[tau + 1];
      const denom = a + c - 2 * b;
      if (denom > 1e-9) refined = tau + (a - c) / (2 * denom);
    }
    return { hz: sampleRate / refined, rms, clarity: 1 - cmnd[tau] };
  };
}
