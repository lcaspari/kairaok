// Microphone: capture, karaoke vocal effect chain, and live pitch.
//
//   mic -> rumble filter -> [pitch detector]
//                        -> compressor -> presence EQ -+-> dry -----------+
//                                                      +-> reverb --------+-> volume -> monitor -> speakers
//                                                      +-> echo (delay) --+

import { createDetector, hzToMidi } from "./pitch.js";

const SIZE = 2048;

function makeImpulse(ctx, seconds = 2.2, decay = 2.6) {
  // a synthetic hall: stereo noise that fades out
  const len = Math.floor(ctx.sampleRate * seconds);
  const buf = ctx.createBuffer(2, len, ctx.sampleRate);
  for (let ch = 0; ch < 2; ch++) {
    const data = buf.getChannelData(ch);
    for (let i = 0; i < len; i++) {
      const t = i / len;
      const pre = i < ctx.sampleRate * 0.012 ? 0 : 1;           // short pre-delay
      data[i] = (Math.random() * 2 - 1) * Math.pow(1 - t, decay) * pre;
    }
  }
  return buf;
}

export class Mic {
  constructor(ctx, output) {
    this.ctx = ctx;
    this.stream = null;
    this.source = null;
    this.active = false;
    this.buf = new Float32Array(SIZE);
    this.detect = createDetector(ctx.sampleRate, SIZE);
    this.recent = [];

    // Audio interfaces often deliver two channels with the microphone on the
    // first one only, which would play on the left speaker alone. Add all input
    // channels into one, so the voice is mono and comes out of both speakers.
    this.splitter = new ChannelSplitterNode(ctx, { numberOfOutputs: 2 });
    this.mono = new GainNode(ctx, { gain: 1, channelCount: 1, channelCountMode: "explicit", channelInterpretation: "discrete" });
    this.setChannel("sum");

    this.highpass = new BiquadFilterNode(ctx, { type: "highpass", frequency: 90, Q: 0.7 });
    this.analyser = new AnalyserNode(ctx, { fftSize: SIZE, smoothingTimeConstant: 0 });
    this.comp = new DynamicsCompressorNode(ctx, { threshold: -24, knee: 18, ratio: 4, attack: 0.005, release: 0.18 });
    this.presence = new BiquadFilterNode(ctx, { type: "peaking", frequency: 3200, Q: 0.9, gain: 3 });
    this.air = new BiquadFilterNode(ctx, { type: "highshelf", frequency: 9000, gain: 2 });

    this.dry = new GainNode(ctx, { gain: 1 });
    this.reverb = new ConvolverNode(ctx, { buffer: makeImpulse(ctx) });
    this.reverbGain = new GainNode(ctx, { gain: 0.3 });
    this.delay = new DelayNode(ctx, { delayTime: 0.24, maxDelayTime: 1 });
    this.feedback = new GainNode(ctx, { gain: 0.3 });
    this.echoTone = new BiquadFilterNode(ctx, { type: "lowpass", frequency: 3500 });
    this.echoGain = new GainNode(ctx, { gain: 0.15 });
    this.volume = new GainNode(ctx, { gain: 1 });
    this.monitor = new GainNode(ctx, { gain: 0 });

    this.mono.connect(this.highpass);
    this.highpass.connect(this.analyser);
    this.highpass.connect(this.comp).connect(this.presence).connect(this.air);
    this.air.connect(this.dry).connect(this.volume);
    this.air.connect(this.reverb).connect(this.reverbGain).connect(this.volume);
    this.air.connect(this.delay).connect(this.echoTone).connect(this.echoGain).connect(this.volume);
    this.echoTone.connect(this.feedback).connect(this.delay);
    this.volume.connect(this.monitor).connect(output);
  }

  // Which input of the device to listen to: "sum" (all of them), 0 or 1.
  setChannel(channel) {
    this.splitter.disconnect();
    if (channel === "sum" || channel === 0) this.splitter.connect(this.mono, 0);
    if (channel === "sum" || channel === 1) this.splitter.connect(this.mono, 1);
    this.channel = channel;
  }

  // strict: fail instead of falling back to the default microphone
  async start(deviceId, speakerMode, channel = this.channel, strict = false) {
    this.stop();
    this.setChannel(channel);
    const audio = {
      channelCount: { ideal: 2 },                               // keep both inputs of an interface apart
      // raw voice for headphones; filtered when the song plays over speakers
      echoCancellation: !!speakerMode,
      noiseSuppression: !!speakerMode,
      autoGainControl: false,
    };
    if (deviceId) audio.deviceId = { exact: deviceId };
    try {
      this.stream = await navigator.mediaDevices.getUserMedia({ audio });
    } catch (err) {
      if (!deviceId || strict || err.name === "NotAllowedError") throw err;
      delete audio.deviceId;                                    // saved mic is gone: use the default
      this.stream = await navigator.mediaDevices.getUserMedia({ audio });
    }
    this.source = this.ctx.createMediaStreamSource(this.stream);
    this.source.connect(this.splitter);
    this.active = true;
    return this.stream.getAudioTracks()[0]?.getSettings().deviceId || "";
  }

  stop() {
    if (this.source) this.source.disconnect();
    if (this.stream) this.stream.getTracks().forEach((t) => t.stop());
    this.source = this.stream = null;
    this.active = false;
    this.recent.length = 0;
  }

  static async devices() {
    const all = await navigator.mediaDevices.enumerateDevices();
    return all.filter((d) => d.kind === "audioinput" && d.deviceId);
  }

  ramp(param, value) {
    param.setTargetAtTime(value, this.ctx.currentTime, 0.02);
  }
  setMonitor(on) { this.ramp(this.monitor.gain, on ? 1 : 0); }
  setVolume(v) { this.ramp(this.volume.gain, v); }
  setReverb(v) { this.ramp(this.reverbGain.gain, v * 0.9); }
  setEcho(v) {
    this.ramp(this.echoGain.gain, v * 0.6);
    this.ramp(this.feedback.gain, 0.2 + v * 0.35);
  }

  // Current sung pitch. Returns { midi, level } with midi = null when not singing.
  read() {
    if (!this.active) return { midi: null, level: 0 };
    this.analyser.getFloatTimeDomainData(this.buf);
    const r = this.detect(this.buf);
    const level = Math.min(1, r.rms * 6);
    if (!r.hz || r.clarity < 0.8) {
      this.recent.length = 0;
      return { midi: null, level };
    }
    // median of the last three readings removes single-frame octave slips
    this.recent.push(hzToMidi(r.hz));
    if (this.recent.length > 3) this.recent.shift();
    const sorted = [...this.recent].sort((a, b) => a - b);
    return { midi: sorted[sorted.length >> 1], level };
  }
}
