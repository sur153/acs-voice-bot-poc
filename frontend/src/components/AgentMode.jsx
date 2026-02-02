import React, { useState, useEffect, useCallback } from 'react'
import { UserIcon, ChatIcon, CheckIcon, SpinnerIcon, PhoneIcon, AlertIcon, SettingsIcon } from './Icons'

// Transfer status constants
const TRANSFER_STATUS = {
  IDLE: 'idle',
  INITIATING: 'initiating',
  IN_PROGRESS: 'in_progress',
  COMPLETED: 'completed',
  FAILED: 'failed'
}

function AgentMode({ onSwitchTab, onRequestTransfer, transferStatus, transferError }) {
  const [agentName, setAgentName] = useState('Human Agent')
  const [statusMessage, setStatusMessage] = useState('')
  const [showSettings, setShowSettings] = useState(false)
  const [agentPhone, setAgentPhone] = useState(() => {
    // Load from localStorage for demo persistence
    return localStorage.getItem('demoAgentPhone') || ''
  })

  // Update status message based on transfer status
  useEffect(() => {
    switch (transferStatus) {
      case TRANSFER_STATUS.IDLE:
        setStatusMessage('Click below to speak with a human agent')
        break
      case TRANSFER_STATUS.INITIATING:
        setStatusMessage('Preparing your conversation summary...')
        break
      case TRANSFER_STATUS.IN_PROGRESS:
        setStatusMessage('Connecting you to an agent...')
        break
      case TRANSFER_STATUS.COMPLETED:
        setAgentName('Request Submitted')
        setStatusMessage('An agent will review your conversation and contact you shortly.')
        break
      case TRANSFER_STATUS.FAILED:
        setStatusMessage(`Transfer failed: ${transferError || 'Please try again'}`)
        break
      default:
        setStatusMessage('Click below to speak with a human agent')
    }
  }, [transferStatus, transferError])

  // Save phone number to localStorage
  const handlePhoneChange = useCallback((e) => {
    const phone = e.target.value
    setAgentPhone(phone)
    localStorage.setItem('demoAgentPhone', phone)
  }, [])

  // Handle transfer request
  const handleRequestTransfer = useCallback(() => {
    if (transferStatus === TRANSFER_STATUS.IDLE || transferStatus === TRANSFER_STATUS.FAILED) {
      onRequestTransfer?.('User requested to speak with a human agent', agentPhone || null)
    }
  }, [transferStatus, onRequestTransfer, agentPhone])

  // Determine avatar state
  const isLoading = transferStatus === TRANSFER_STATUS.INITIATING ||
                    transferStatus === TRANSFER_STATUS.IN_PROGRESS
  const isCompleted = transferStatus === TRANSFER_STATUS.COMPLETED
  const isFailed = transferStatus === TRANSFER_STATUS.FAILED
  const canRequest = transferStatus === TRANSFER_STATUS.IDLE ||
                     transferStatus === TRANSFER_STATUS.FAILED

  return (
    <div className="agent-mode">
      <div className="agent-content">
        {/* Settings toggle */}
        <button
          type="button"
          className="settings-toggle"
          onClick={() => setShowSettings(!showSettings)}
          aria-label="Demo settings"
          title="Configure demo settings"
        >
          <SettingsIcon size={18} />
        </button>

        {/* Demo settings panel */}
        {showSettings && (
          <div className="demo-settings">
            <h4>Demo Settings</h4>
            <div className="setting-field">
              <label htmlFor="agent-phone">Agent Phone Number</label>
              <input
                id="agent-phone"
                type="tel"
                placeholder="+1234567890"
                value={agentPhone}
                onChange={handlePhoneChange}
                aria-describedby="phone-hint"
              />
              <small id="phone-hint">E.164 format (e.g., +919953216992)</small>
            </div>
            <a
              href="/agent"
              target="_blank"
              rel="noopener noreferrer"
              className="dashboard-link"
            >
              Open Agent Dashboard →
            </a>
          </div>
        )}

        {/* Agent avatar */}
        <div
          className={`agent-avatar ${isCompleted ? 'connected' : ''} ${isFailed ? 'failed' : ''}`}
          aria-hidden="true"
        >
          {isLoading ? (
            <SpinnerIcon size={48} className="agent-spinner" />
          ) : isCompleted ? (
            <CheckIcon size={48} className="agent-icon" />
          ) : isFailed ? (
            <AlertIcon size={48} className="agent-icon error" />
          ) : (
            <PhoneIcon size={48} className="agent-icon" />
          )}
        </div>

        {/* Agent info */}
        <div className="agent-info">
          <h2 className="agent-name">{agentName}</h2>
          <p className="agent-status" aria-live="polite">{statusMessage}</p>
          {agentPhone && !isLoading && !isCompleted && (
            <p className="agent-phone-display">Will call: {agentPhone}</p>
          )}
        </div>

        {/* Status indicator */}
        <div className="transfer-status" role="status" aria-live="polite">
          {isCompleted ? (
            <div className="connected-status">
              <CheckIcon size={32} className="check-icon" />
              <span className="connected-label">Request Received</span>
              <span className="connected-hint">
                Your conversation history has been saved
              </span>
            </div>
          ) : isFailed ? (
            <div className="error-status">
              <AlertIcon size={32} className="error-icon" />
              <span className="error-label">Transfer Failed</span>
              <span className="error-hint">
                Please try again or continue with the AI assistant
              </span>
            </div>
          ) : isLoading ? (
            <div className="loading-status">
              <div className="loading-dots">
                <span></span>
                <span></span>
                <span></span>
              </div>
              <span className="loading-label">Processing...</span>
            </div>
          ) : null}
        </div>

        {/* Transfer button */}
        {canRequest && (
          <button
            type="button"
            className="transfer-button"
            onClick={handleRequestTransfer}
            disabled={isLoading}
            aria-label="Request to speak with a human agent"
          >
            <PhoneIcon size={20} />
            <span>Speak to Agent</span>
          </button>
        )}

        {/* Info message */}
        <p className="agent-connected-message">
          {isCompleted
            ? 'Thank you for your patience. An agent will reach out to assist you.'
            : isFailed
            ? 'There was an issue connecting to an agent. You can try again or continue with our AI assistant.'
            : 'An agent will receive your full conversation history to provide personalized assistance.'
          }
        </p>

        {/* Quick actions */}
        <div className="quick-actions" role="group" aria-label="Navigation options">
          <button
            type="button"
            className="quick-action"
            onClick={() => onSwitchTab('conversation')}
            aria-label="Return to conversation"
          >
            <ChatIcon size={16} />
            Return to Conversation
          </button>
        </div>
      </div>
    </div>
  )
}

export default AgentMode
