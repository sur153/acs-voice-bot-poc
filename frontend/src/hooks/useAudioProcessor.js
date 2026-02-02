import { useRef, useCallback, useEffect } from 'react'

const SAMPLE_RATE = 24000

/**
 * Custom hook for audio processing - handles playback and recording
 * Uses scheduled BufferSource for seamless audio playback (simpler than ring buffer)
 * Preserves the original audio-synced transcript reveal functionality
 */
export function useAudioProcessor() {
  const audioContextRef = useRef(null)
  const micWorkletNodeRef = useRef(null)
  const mediaStreamRef = useRef(null)
  const sourceRef = useRef(null)
  const micWorkletLoadedRef = useRef(false)

  // Scheduled playback state - tracks when to start next audio chunk
  const nextPlayTimeRef = useRef(0)
  const activeSourcesRef = useRef([])

  // Audio sync state
  const totalAudioSamplesRef = useRef(0)
  const audioStartTimeRef = useRef(0)
  const revealAnimationIdRef = useRef(null)

  // Initialize audio context (no worklet needed for playback anymore)
  const initAudio = useCallback(async () => {
    if (!audioContextRef.current) {
      audioContextRef.current = new AudioContext({ sampleRate: SAMPLE_RATE })
    }
    return audioContextRef.current
  }, [])

  // Play audio from ArrayBuffer (Int16 PCM)
  // Uses scheduled BufferSource for seamless, sample-accurate playback
  const playAudio = useCallback(async (arrayBuffer) => {
    const audioContext = await initAudio()

    if (audioContext.state === 'suspended') {
      await audioContext.resume()
    }

    // Convert Int16 PCM to Float32
    const int16 = new Int16Array(arrayBuffer)
    const sampleCount = int16.length
    const float32 = new Float32Array(sampleCount)

    const scale = 1 / 32768
    for (let i = 0; i < sampleCount; i++) {
      float32[i] = int16[i] * scale
    }

    // Create audio buffer and fill with samples
    const buffer = audioContext.createBuffer(1, sampleCount, SAMPLE_RATE)
    buffer.getChannelData(0).set(float32)

    // Create source and connect to output
    const src = audioContext.createBufferSource()
    src.buffer = buffer
    src.connect(audioContext.destination)

    // Schedule at exact time for seamless playback
    // If we've fallen behind, start from current time
    nextPlayTimeRef.current = Math.max(nextPlayTimeRef.current, audioContext.currentTime)
    src.start(nextPlayTimeRef.current)
    nextPlayTimeRef.current += buffer.duration

    // Track active sources for cleanup
    activeSourcesRef.current.push(src)
    src.onended = () => {
      const index = activeSourcesRef.current.indexOf(src)
      if (index > -1) {
        activeSourcesRef.current.splice(index, 1)
      }
    }

    // Track audio samples for sync
    if (totalAudioSamplesRef.current === 0) {
      audioStartTimeRef.current = performance.now()
    }
    totalAudioSamplesRef.current += sampleCount

    return {
      audioStartTime: audioStartTimeRef.current,
      totalAudioSamples: totalAudioSamplesRef.current
    }
  }, [initAudio])

  // Stop audio playback - stops all scheduled sources
  const stopPlayback = useCallback(() => {
    // Stop all active audio sources
    activeSourcesRef.current.forEach(src => {
      try {
        src.stop()
        src.disconnect()
      } catch (e) {
        // Source may have already ended
      }
    })
    activeSourcesRef.current = []

    // Reset scheduling time
    nextPlayTimeRef.current = 0

    if (revealAnimationIdRef.current) {
      cancelAnimationFrame(revealAnimationIdRef.current)
      revealAnimationIdRef.current = null
    }

    totalAudioSamplesRef.current = 0
    audioStartTimeRef.current = 0
  }, [])

  // Start microphone capture using AudioWorklet (modern API, runs off main thread)
  const startMicrophone = useCallback(async (onAudioData) => {
    const audioContext = await initAudio()
    await audioContext.resume()

    // Load microphone worklet if not already loaded
    if (!micWorkletLoadedRef.current) {
      await audioContext.audioWorklet.addModule('/static/microphone-processor.js')
      micWorkletLoadedRef.current = true
    }

    mediaStreamRef.current = await navigator.mediaDevices.getUserMedia({
      audio: {
        echoCancellation: true,
        noiseSuppression: true,
        autoGainControl: true
      }
    })

    sourceRef.current = audioContext.createMediaStreamSource(mediaStreamRef.current)

    // Create AudioWorklet node for microphone processing
    micWorkletNodeRef.current = new AudioWorkletNode(audioContext, 'microphone-processor')

    // Handle audio data from worklet
    micWorkletNodeRef.current.port.onmessage = (event) => {
      if (event.data.pcm) {
        onAudioData(event.data.pcm)
      }
    }

    // Connect: microphone -> worklet (no output needed, just processing)
    sourceRef.current.connect(micWorkletNodeRef.current)
  }, [initAudio])

  // Stop microphone capture
  const stopMicrophone = useCallback(() => {
    if (micWorkletNodeRef.current) {
      micWorkletNodeRef.current.disconnect()
      micWorkletNodeRef.current.port.onmessage = null
      micWorkletNodeRef.current = null
    }
    if (sourceRef.current) {
      sourceRef.current.disconnect()
      sourceRef.current = null
    }
    if (mediaStreamRef.current) {
      mediaStreamRef.current.getTracks().forEach(t => t.stop())
      mediaStreamRef.current = null
    }
  }, [])

  // Calculate reveal progress for audio-synced text
  const getRevealProgress = useCallback(() => {
    if (totalAudioSamplesRef.current === 0) return 0

    const elapsed = performance.now() - audioStartTimeRef.current
    const samplesPlayed = (elapsed / 1000) * SAMPLE_RATE
    let progress = samplesPlayed / totalAudioSamplesRef.current
    return Math.max(0, Math.min(progress, 1))
  }, [])

  // Start reveal animation loop
  const startRevealAnimation = useCallback((onProgress) => {
    if (revealAnimationIdRef.current) return

    const updateReveal = () => {
      const progress = getRevealProgress()
      onProgress(progress)

      if (progress >= 1) {
        revealAnimationIdRef.current = null
        totalAudioSamplesRef.current = 0
        audioStartTimeRef.current = 0
        return
      }

      revealAnimationIdRef.current = requestAnimationFrame(updateReveal)
    }

    revealAnimationIdRef.current = requestAnimationFrame(updateReveal)
  }, [getRevealProgress])

  // Reset audio sync state
  const resetAudioSync = useCallback(() => {
    if (revealAnimationIdRef.current) {
      cancelAnimationFrame(revealAnimationIdRef.current)
      revealAnimationIdRef.current = null
    }
    totalAudioSamplesRef.current = 0
    audioStartTimeRef.current = 0
  }, [])

  // Cleanup on unmount
  useEffect(() => {
    return () => {
      stopMicrophone()
      stopPlayback()
      if (audioContextRef.current) {
        audioContextRef.current.close()
      }
    }
  }, [stopMicrophone, stopPlayback])

  return {
    initAudio,
    playAudio,
    stopPlayback,
    startMicrophone,
    stopMicrophone,
    getRevealProgress,
    startRevealAnimation,
    resetAudioSync,
    isRecording: () => !!mediaStreamRef.current
  }
}

export default useAudioProcessor
