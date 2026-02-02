import React, { useState } from 'react'
import { MicrophoneIcon, ShieldIcon, SpinnerIcon } from './Icons'

function LandingScreen({ onStart, connectionStatus }) {
  const [isStarting, setIsStarting] = useState(false)

  const handleStart = async () => {
    setIsStarting(true)
    try {
      await onStart()
    } catch (error) {
      console.error('Failed to start:', error)
      setIsStarting(false)
    }
  }

  const isConnecting = connectionStatus === 'connecting' || isStarting
  const hasError = connectionStatus === 'error'

  return (
    <div className="chat-container landing-container">
      <div className="landing-screen">
        <div className="landing-content">
          {/* Animated microphone icon */}
          <div
            className={`landing-mic-icon ${isConnecting ? 'connecting' : ''}`}
            aria-hidden="true"
          >
            {isConnecting ? (
              <SpinnerIcon size={64} className="landing-spinner" />
            ) : (
              <MicrophoneIcon size={64} className="landing-mic-svg" />
            )}
          </div>

          <h1 className="landing-title">
            TeleLife Application Assistant
          </h1>

          <p className="landing-description">
            Complete your life insurance application with the help of our AI assistant.
            You can speak naturally or type your responses.
          </p>

          {/* Feature highlights */}
          <div className="landing-features" aria-label="Features">
            <div className="landing-feature">
              <MicrophoneIcon size={20} />
              <span>Voice & Text Input</span>
            </div>
            <div className="landing-feature">
              <ShieldIcon size={20} />
              <span>Secure & Private</span>
            </div>
          </div>

          {/* Error state */}
          {hasError && (
            <div className="landing-status error" role="alert">
              <span className="status-dot error" />
              Connection failed. Please try again.
            </div>
          )}

          {/* Connecting state */}
          {isConnecting && (
            <div className="landing-status connecting" aria-live="polite">
              <span className="status-dot connecting" />
              Connecting to assistant...
            </div>
          )}

          <button
            className="landing-start-btn"
            onClick={handleStart}
            disabled={isConnecting}
            aria-busy={isConnecting}
          >
            {isConnecting ? (
              <>
                <SpinnerIcon size={20} />
                Connecting...
              </>
            ) : (
              'Start Application'
            )}
          </button>

          <p className="landing-subtitle">
            Your conversation is secure and confidential
          </p>
        </div>
      </div>
    </div>
  )
}

export default LandingScreen
