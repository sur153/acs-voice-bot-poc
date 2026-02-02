import React, { useState, useCallback, useRef, useEffect } from 'react'
import Header from './components/Header'
import LandingScreen from './components/LandingScreen'
import ChatContainer from './components/ChatContainer'
import useWebSocket from './hooks/useWebSocket'
import useAudioProcessor from './hooks/useAudioProcessor'

function App() {
  // App state
  const [isSessionStarted, setIsSessionStarted] = useState(false)
  const [activeTab, setActiveTab] = useState('conversation') // 'conversation' | 'agent'
  const [inputMode, setInputMode] = useState('voice') // 'voice' | 'text'
  const [progress, setProgress] = useState(0)

  // Estimated total questions in the interview
  const TOTAL_QUESTIONS = 80

  // Message state - single source of truth for all messages
  const [messages, setMessages] = useState([])
  const [isAiSpeaking, setIsAiSpeaking] = useState(false)
  const [isRecording, setIsRecording] = useState(false)

  // Session data for the data panel
  // Check URL for existing session ID to view
  const urlParams = new URLSearchParams(window.location.search)
  const urlSessionId = urlParams.get('session')

  const [sessionData, setSessionData] = useState({
    meta: {
      session_id: urlSessionId || null,
      status: urlSessionId ? 'Viewing' : 'Connecting',
      interview_date: new Date().toISOString()
    },
    applicant: {
      phone: null
    }
  })

  // Streaming state - accumulates the full AI response
  const streamingTextRef = useRef('')
  const [streamingMessage, setStreamingMessage] = useState(null)

  // Track if we received audio for this response
  const hasAudioRef = useRef(false)

  // Track AI speaking state in ref to avoid redundant React updates
  const isAiSpeakingRef = useRef(false)

  // Transfer state
  const [transferStatus, setTransferStatus] = useState('idle')
  const [transferError, setTransferError] = useState(null)

  // Track current streaming message ID for smooth transitions
  const streamingIdRef = useRef(null)

  // Hooks
  const {
    connect,
    sendJSON,
    sendBinary,
    disconnect,
    isConnected,
    connectionStatus
  } = useWebSocket()

  const {
    initAudio,
    playAudio,
    stopPlayback,
    startMicrophone,
    stopMicrophone
  } = useAudioProcessor()

  // Handle incoming WebSocket messages
  const handleWebSocketMessage = useCallback((event) => {
    if (typeof event.data === 'string') {
      const msg = JSON.parse(event.data)

      switch (msg.Kind) {
        case 'StopAudio':
          stopPlayback()
          isAiSpeakingRef.current = false
          setIsAiSpeaking(false)
          break

        case 'TranscriptDelta':
          // Accumulate text and show it immediately
          // Generate consistent ID for this streaming session to avoid flicker
          if (!streamingIdRef.current) {
            streamingIdRef.current = Date.now()
          }
          streamingTextRef.current += msg.Text
          setStreamingMessage({
            id: streamingIdRef.current,
            role: 'ai',
            content: streamingTextRef.current,
            isStreaming: true
          })
          break

        case 'TranscriptDone':
          // Text complete - use msg.Text (complete transcript) as authoritative source
          // Fall back to accumulated streamingTextRef only if msg.Text unavailable
          const finalText = msg.Text || streamingTextRef.current
          if (finalText) {
            // Use same ID as streaming message for smooth transition (no flicker)
            const messageId = streamingIdRef.current || Date.now()
            setMessages(prev => [...prev, {
              id: messageId,
              role: 'ai',
              content: finalText,
              hasAudio: hasAudioRef.current,
              timestamp: new Date().toLocaleTimeString()
            }])
          }
          // Clear streaming state
          streamingTextRef.current = ''
          streamingIdRef.current = null
          hasAudioRef.current = false
          setStreamingMessage(null)
          break

        case 'UserTranscription':
          // User's speech transcribed - add to messages
          setMessages(prev => [...prev, {
            id: Date.now(),
            role: 'user',
            content: msg.Text,
            inputMethod: 'voice',
            timestamp: new Date().toLocaleTimeString()
          }])
          break

        case 'SessionCreated':
          // Session established with Voice Live - update session data for DataPanel
          console.log('Session created:', msg.SessionId)
          setSessionData(prev => ({
            ...prev,
            meta: {
              ...prev.meta,
              session_id: msg.SessionId,
              status: 'Active'
            },
            applicant: {
              ...prev.applicant,
              phone: msg.PhoneNumber
            }
          }))
          break

        case 'ResponseDone':
          // Fallback: finalize any remaining streaming text not caught by TranscriptDone
          if (streamingTextRef.current) {
            const messageId = streamingIdRef.current || Date.now()
            setMessages(prev => [...prev, {
              id: messageId,
              role: 'ai',
              content: streamingTextRef.current,
              hasAudio: hasAudioRef.current,
              timestamp: new Date().toLocaleTimeString()
            }])
          }
          // Always clean up state regardless of whether there was text
          streamingTextRef.current = ''
          streamingIdRef.current = null
          hasAudioRef.current = false
          setStreamingMessage(null)
          isAiSpeakingRef.current = false
          setIsAiSpeaking(false)
          break

        // Transfer status messages
        case 'TransferInitiated':
          console.log('Transfer initiated for session:', msg.SessionId)
          setTransferStatus('initiating')
          setTransferError(null)
          break

        case 'TransferInProgress':
          console.log('Transfer in progress, agent phone:', msg.AgentPhone)
          setTransferStatus('in_progress')
          break

        case 'TransferComplete':
          console.log('Transfer complete:', msg.Message)
          setTransferStatus('completed')
          break

        case 'TransferFailed':
          console.error('Transfer failed:', msg.Error)
          setTransferStatus('failed')
          setTransferError(msg.Error || 'Unknown error occurred')
          break

        case 'ConnectionLost':
          // Voice Live connection lost - notify user
          console.error('Voice connection lost:', msg.Message)
          alert('Voice connection lost. Please refresh the page to continue.')
          break

        default:
          console.log('Unhandled message:', msg)
      }
    } else if (event.data instanceof ArrayBuffer) {
      // Binary audio data - play it
      hasAudioRef.current = true

      // Only update state if not already speaking (avoid redundant React dispatches)
      if (!isAiSpeakingRef.current) {
        isAiSpeakingRef.current = true
        setIsAiSpeaking(true)
      }

      playAudio(event.data).catch(err => {
        console.error('Audio playback error:', err)
      })
    }
  }, [playAudio, stopPlayback])

  // Start session
  const startSession = useCallback(async () => {
    try {
      await initAudio()
      await connect(
        handleWebSocketMessage,
        () => {
          console.log('Session started')
          setIsSessionStarted(true)
          // AI introduction is auto-triggered by backend when Voice Live session is ready
        },
        () => {
          setIsSessionStarted(false)
          setIsRecording(false)
        },
        (error) => {
          console.error('Connection error:', error)
        }
      )
    } catch (error) {
      console.error('Failed to start session:', error)
    }
  }, [connect, initAudio, handleWebSocketMessage])

  // Start voice recording
  const startVoiceRecording = useCallback(async () => {
    try {
      await startMicrophone((audioData) => {
        sendBinary(audioData)
      })
      setIsRecording(true)
    } catch (error) {
      console.error('Failed to start microphone:', error)
    }
  }, [startMicrophone, sendBinary])

  // Stop voice recording
  const stopVoiceRecording = useCallback(() => {
    stopMicrophone()
    setIsRecording(false)
  }, [stopMicrophone])

  // Send chat message (text input)
  const sendChatMessage = useCallback((text) => {
    if (!text.trim()) return

    // Add user message
    setMessages(prev => [...prev, {
      id: Date.now(),
      role: 'user',
      content: text,
      inputMethod: 'text',
      timestamp: new Date().toLocaleTimeString()
    }])

    // Send to server
    sendJSON({
      type: 'text_input',
      text: text
    })

    // Progress is now updated by DataPanel based on actual answered questions
  }, [sendJSON])

  // Update progress based on answered questions from DataPanel
  const handleProgressUpdate = useCallback((answeredCount) => {
    const calculatedProgress = Math.min(100, Math.round((answeredCount / TOTAL_QUESTIONS) * 100))
    setProgress(calculatedProgress)
  }, [TOTAL_QUESTIONS])

  // Request transfer to human agent
  const requestTransfer = useCallback((reason, customPhone = null) => {
    console.log('Requesting transfer:', reason, customPhone ? `to ${customPhone}` : '(default phone)')
    setTransferStatus('initiating')
    setTransferError(null)

    const payload = {
      type: 'transfer_to_agent',
      reason: reason
    }

    // Include custom phone number if provided (for demo purposes)
    if (customPhone) {
      payload.agent_phone = customPhone
    }

    sendJSON(payload)
  }, [sendJSON])

  // Switch active tab
  const switchTab = useCallback((tab) => {
    setActiveTab(tab)

    // Stop recording if switching away from conversation
    if (tab !== 'conversation' && isRecording) {
      stopVoiceRecording()
    }
  }, [isRecording, stopVoiceRecording])

  // Toggle input mode
  const toggleInputMode = useCallback((mode) => {
    // Don't switch while AI is speaking
    if (isAiSpeaking) return

    const prevMode = inputMode
    setInputMode(mode)

    // Stop recording if switching from voice to text
    if (prevMode === 'voice' && mode === 'text' && isRecording) {
      stopVoiceRecording()
    }
  }, [inputMode, isRecording, isAiSpeaking, stopVoiceRecording])

  // End session
  const endSession = useCallback(() => {
    stopVoiceRecording()
    stopPlayback()
    disconnect()
    setIsSessionStarted(false)
    setMessages([])
    setStreamingMessage(null)
    streamingTextRef.current = ''
    streamingIdRef.current = null
    hasAudioRef.current = false
    // Reset transfer state
    setTransferStatus('idle')
    setTransferError(null)
  }, [stopVoiceRecording, stopPlayback, disconnect])

  // Track previous AI speaking state to detect when it stops
  const wasAiSpeakingRef = useRef(false)
  const micStartTimeoutRef = useRef(null)

  // Auto-start listening after AI finishes speaking (in voice mode)
  useEffect(() => {
    // Clear any pending timeout when dependencies change
    if (micStartTimeoutRef.current) {
      clearTimeout(micStartTimeoutRef.current)
      micStartTimeoutRef.current = null
    }

    // Detect transition from speaking to not speaking
    if (wasAiSpeakingRef.current && !isAiSpeaking) {
      // AI just finished speaking - auto-start recording if in voice mode
      if (isSessionStarted && inputMode === 'voice' && !isRecording) {
        // Delay mic start to let speaker echo dissipate and prevent false VAD triggers
        // Using 1000ms to ensure echo fully clears and avoid phantom transcriptions
        console.log('AI finished speaking - waiting before auto-starting microphone')
        micStartTimeoutRef.current = setTimeout(() => {
          console.log('Starting microphone after delay')
          startVoiceRecording()
          micStartTimeoutRef.current = null
        }, 1000)  // 1000ms delay for echo to clear (increased from 600ms)
      }
    }
    wasAiSpeakingRef.current = isAiSpeaking

    // Cleanup on unmount
    return () => {
      if (micStartTimeoutRef.current) {
        clearTimeout(micStartTimeoutRef.current)
      }
    }
  }, [isAiSpeaking, isSessionStarted, inputMode, isRecording, startVoiceRecording])

  return (
    <div className="app">
      <Header progress={progress} />

      <main className="main-container">
        {!isSessionStarted ? (
          <LandingScreen
            onStart={startSession}
            connectionStatus={connectionStatus}
          />
        ) : (
          <ChatContainer
            activeTab={activeTab}
            inputMode={inputMode}
            onSwitchTab={switchTab}
            onToggleInputMode={toggleInputMode}
            messages={messages}
            streamingMessage={streamingMessage}
            isConnected={isConnected}
            isRecording={isRecording}
            isAiSpeaking={isAiSpeaking}
            onStartRecording={startVoiceRecording}
            onStopRecording={stopVoiceRecording}
            onSendMessage={sendChatMessage}
            onEndSession={endSession}
            onRequestTransfer={requestTransfer}
            transferStatus={transferStatus}
            transferError={transferError}
            sessionData={sessionData}
            onProgressUpdate={handleProgressUpdate}
          />
        )}
      </main>

      <footer className="footer">
        Powered by Azure AI &bull; <a href="#">Privacy Policy</a>
      </footer>
    </div>
  )
}

export default App
