/**
 * Microphone AudioWorklet Processor
 *
 * Captures audio from the microphone and converts Float32 to Int16 PCM
 * for sending to the backend. Runs off the main thread to prevent
 * interference with UI and playback audio.
 */
class MicrophoneProcessor extends AudioWorkletProcessor {
  constructor() {
    super()
    // Buffer to accumulate samples before sending
    // Matches the old ScriptProcessorNode buffer size of 2048
    this.bufferSize = 2048
    this.buffer = new Float32Array(this.bufferSize)
    this.bufferIndex = 0
  }

  process(inputs, outputs, parameters) {
    const input = inputs[0]

    // No input connected
    if (!input || input.length === 0) {
      return true
    }

    const inputChannel = input[0]
    if (!inputChannel) {
      return true
    }

    // Accumulate samples into buffer
    for (let i = 0; i < inputChannel.length; i++) {
      this.buffer[this.bufferIndex++] = inputChannel[i]

      // Buffer full - convert and send
      if (this.bufferIndex >= this.bufferSize) {
        // Convert Float32 to Int16 PCM
        const int16 = new Int16Array(this.bufferSize)
        for (let j = 0; j < this.bufferSize; j++) {
          const s = Math.max(-1, Math.min(1, this.buffer[j]))
          int16[j] = s < 0 ? s * 0x8000 : s * 0x7FFF
        }

        // Send to main thread
        this.port.postMessage({ pcm: int16.buffer }, [int16.buffer])

        // Reset buffer
        this.bufferIndex = 0
      }
    }

    return true
  }
}

registerProcessor('microphone-processor', MicrophoneProcessor)
