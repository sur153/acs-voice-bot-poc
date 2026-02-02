/**
 * Audio Worklet Processor with Optimized Ring Buffer
 *
 * Uses bulk TypedArray operations for high-performance audio buffering.
 * Implements pre-buffering to handle network jitter and prevent audio underruns.
 */
class RingBufferProcessor extends AudioWorkletProcessor {
  constructor() {
    super();

    // Pre-allocate a large circular buffer (5 seconds at 24kHz)
    this.bufferSize = 24000 * 5;
    this.circularBuffer = new Float32Array(this.bufferSize);
    this.writeIndex = 0;
    this.readIndex = 0;
    this.samplesAvailable = 0;

    // Jitter buffer configuration - tuned for real-time voice
    // With smaller 50ms chunks from backend, we can use smaller thresholds
    // Initial buffer: ~400ms for connection warmup (reduced from 800ms)
    this.initialBufferThreshold = 9600;
    // Minimum buffer: ~150ms for subsequent responses (reduced from 300ms)
    this.minBufferThreshold = 3600;
    // Current threshold (starts high, reduces after first playback)
    this.currentThreshold = this.initialBufferThreshold;
    // Low buffer warning threshold
    this.lowBufferThreshold = 3600;

    // Audio smoothing to prevent clicks
    this.fadeInSamples = 480;  // 20ms fade-in at 24kHz
    this.fadeInCounter = 0;
    this.needsFadeIn = true;

    // Playback state
    this.isBuffering = true;
    this.underrunCount = 0;
    this.hasPlayedOnce = false;
    this.consecutiveUnderruns = 0;

    this.port.onmessage = (e) => {
      if (e.data.pcm) {
        this._writeToBuffer(e.data.pcm);
      } else if (e.data.clear) {
        this._clearBuffer();
      }
    };
  }

  /**
   * Write PCM data to circular buffer using bulk operations
   * Uses TypedArray.set() for ~10-100x faster copying than per-sample loops
   */
  _writeToBuffer(pcm) {
    const samplesToWrite = pcm.length;

    // Handle buffer overflow - drop oldest samples
    if (this.samplesAvailable + samplesToWrite > this.bufferSize - 1024) {
      const overflow = (this.samplesAvailable + samplesToWrite) - (this.bufferSize - 1024);
      this.readIndex = (this.readIndex + overflow) % this.bufferSize;
      this.samplesAvailable -= overflow;
    }

    // Optimized bulk write using TypedArray.set()
    const spaceToEnd = this.bufferSize - this.writeIndex;

    if (samplesToWrite <= spaceToEnd) {
      // Simple case: all data fits before wrap
      this.circularBuffer.set(pcm, this.writeIndex);
      this.writeIndex = (this.writeIndex + samplesToWrite) % this.bufferSize;
    } else {
      // Wrap-around case: split into two writes
      this.circularBuffer.set(pcm.subarray(0, spaceToEnd), this.writeIndex);
      this.circularBuffer.set(pcm.subarray(spaceToEnd), 0);
      this.writeIndex = samplesToWrite - spaceToEnd;
    }

    this.samplesAvailable += samplesToWrite;

    // Check if we've buffered enough to start playback
    if (this.isBuffering && this.samplesAvailable >= this.currentThreshold) {
      this.isBuffering = false;
      this.consecutiveUnderruns = 0;
      // After first successful playback, reduce threshold
      if (!this.hasPlayedOnce) {
        this.hasPlayedOnce = true;
        this.currentThreshold = this.minBufferThreshold;
      }
    }
  }

  /**
   * Clear the buffer (e.g., when stopping audio)
   */
  _clearBuffer() {
    this.writeIndex = 0;
    this.readIndex = 0;
    this.samplesAvailable = 0;
    this.isBuffering = true;
    this.underrunCount = 0;
    this.consecutiveUnderruns = 0;
    this.fadeInCounter = 0;
    this.needsFadeIn = true;
  }

  /**
   * Read samples from circular buffer using bulk operations
   */
  _readFromBuffer(output, count) {
    const spaceToEnd = this.bufferSize - this.readIndex;

    if (count <= spaceToEnd) {
      // Simple case: all data available before wrap
      output.set(this.circularBuffer.subarray(this.readIndex, this.readIndex + count));
      this.readIndex = (this.readIndex + count) % this.bufferSize;
    } else {
      // Wrap-around case: split into two reads
      output.set(this.circularBuffer.subarray(this.readIndex, this.bufferSize));
      output.set(this.circularBuffer.subarray(0, count - spaceToEnd), spaceToEnd);
      this.readIndex = count - spaceToEnd;
    }

    this.samplesAvailable -= count;
  }

  process(inputs, outputs) {
    const output = outputs[0][0];
    const requestedSamples = output.length;

    // Still buffering - output silence
    if (this.isBuffering) {
      output.fill(0);
      return true;
    }

    // Check for underrun
    if (this.samplesAvailable < requestedSamples) {
      this.underrunCount++;
      this.consecutiveUnderruns++;

      if (this.samplesAvailable > 0) {
        // Partial read - read what we have, apply crossfade to silence
        const available = this.samplesAvailable;
        this._readFromBuffer(output, available);

        // Crossfade to silence over last 48 samples (~2ms) to prevent click
        const fadeLength = Math.min(48, available);
        for (let i = 0; i < fadeLength; i++) {
          const fadePos = available - fadeLength + i;
          output[fadePos] *= 1 - (i / fadeLength);
        }

        output.fill(0, available);
        this.samplesAvailable = 0;
      } else {
        output.fill(0);
      }

      // Re-enter buffering mode after consecutive underruns
      // Use adaptive threshold based on underrun frequency
      if (this.consecutiveUnderruns > 3) {
        this.isBuffering = true;
        this.underrunCount = 0;
        // Temporarily increase threshold if we're having repeated issues
        if (this.consecutiveUnderruns > 5) {
          this.currentThreshold = Math.min(this.currentThreshold + 2400, this.initialBufferThreshold);
        }
        this.consecutiveUnderruns = 0;
        this.needsFadeIn = true;
        this.fadeInCounter = 0;
      }

      return true;
    }

    // Normal playback - bulk read from buffer
    this._readFromBuffer(output, requestedSamples);

    // Apply fade-in to prevent clicks when playback starts
    if (this.needsFadeIn && this.fadeInCounter < this.fadeInSamples) {
      const samplesToFade = Math.min(requestedSamples, this.fadeInSamples - this.fadeInCounter);
      for (let i = 0; i < samplesToFade; i++) {
        output[i] *= (this.fadeInCounter + i) / this.fadeInSamples;
      }
      this.fadeInCounter += samplesToFade;
      if (this.fadeInCounter >= this.fadeInSamples) {
        this.needsFadeIn = false;
      }
    }

    // Reset underrun tracking on successful read
    this.underrunCount = 0;
    this.consecutiveUnderruns = 0;

    return true;
  }
}

registerProcessor('audio-processor', RingBufferProcessor);
