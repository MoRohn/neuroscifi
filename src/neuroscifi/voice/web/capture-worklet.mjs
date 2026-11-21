// NeuroSciFi Voice Cortex: microphone capture worklet (AudioWorklet, audio thread).
// Downsamples the context's input to 16 kHz mono, packs 20 ms frames as 16-bit PCM and measures each frame's RMS.
// It never sends anything anywhere: frames go to the page's VoiceClient, which alone decides whether to upload.
const TARGET = 16000;
const FRAME = 320; // 20 ms at 16 kHz

class NsfCapture extends AudioWorkletProcessor {
  constructor() {
    super();
    this.ratio = sampleRate / TARGET;
    this.pos = 0;            // fractional read position into the incoming stream
    this.carry = new Float32Array(0);
    this.frame = new Float32Array(FRAME);
    this.fill = 0;
    this.running = true;
    this.port.onmessage = (e) => { if (e.data === "stop") this.running = false; };
  }

  process(inputs) {
    if (!this.running) return false;
    const input = inputs[0];
    if (!input || !input[0]) return true;
    const ch = input[0];
    // linear-interpolation resampler over carry + this block
    const data = new Float32Array(this.carry.length + ch.length);
    data.set(this.carry, 0);
    data.set(ch, this.carry.length);
    let p = this.pos;
    while (p + 1 < data.length) {
      const i = Math.floor(p);
      const f = p - i;
      this.frame[this.fill++] = data[i] * (1 - f) + data[i + 1] * f;
      if (this.fill === FRAME) this.flush();
      p += this.ratio;
    }
    const keep = Math.floor(p);
    this.carry = data.slice(keep);
    this.pos = p - keep;
    return true;
  }

  flush() {
    const pcm = new Int16Array(FRAME);
    let sum = 0;
    for (let i = 0; i < FRAME; i++) {
      const s = Math.max(-1, Math.min(1, this.frame[i]));
      sum += s * s;
      pcm[i] = s < 0 ? s * 0x8000 : s * 0x7fff;
    }
    this.port.postMessage({ pcm: pcm.buffer, rms: Math.sqrt(sum / FRAME) }, [pcm.buffer]);
    this.fill = 0;
  }
}

registerProcessor("nsf-capture", NsfCapture);
