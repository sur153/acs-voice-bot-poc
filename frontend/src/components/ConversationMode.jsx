import React, { useState, useRef, useEffect } from 'react'
import {
  SendIcon,
  MicrophoneIcon,
  KeyboardIcon,
  UserIcon,
  HelpIcon,
  WaveformIcon
} from './Icons'

function ConversationMode({
  messages,
  streamingMessage,
  inputMode,
  onToggleInputMode,
  isRecording,
  isAiSpeaking,
  onStartRecording,
  onStopRecording,
  onSendMessage,
  onSwitchTab
}) {
  const [inputText, setInputText] = useState('')
  const messageListRef = useRef(null)
  const textareaRef = useRef(null)

  // Auto-scroll to bottom when messages change
  useEffect(() => {
    if (messageListRef.current) {
      messageListRef.current.scrollTo({
        top: messageListRef.current.scrollHeight,
        behavior: 'smooth'
      })
    }
  }, [messages, streamingMessage])

  // Auto-resize textarea
  const handleInputChange = (e) => {
    setInputText(e.target.value)
    e.target.style.height = 'auto'
    e.target.style.height = Math.min(e.target.scrollHeight, 120) + 'px'
  }

  const handleSubmit = () => {
    if (!inputText.trim()) return
    onSendMessage(inputText)
    setInputText('')
    if (textareaRef.current) {
      textareaRef.current.style.height = 'auto'
    }
  }

  const handleKeyDown = (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      handleSubmit()
    }
  }

  const handleMicClick = () => {
    if (isRecording) {
      onStopRecording()
    } else if (!isAiSpeaking) {
      onStartRecording()
    }
  }

  // Combine messages with streaming message for display
  const displayMessages = streamingMessage
    ? [...messages, streamingMessage]
    : messages

  // Check if we're waiting for AI response
  const isWaitingForResponse = streamingMessage === null &&
    messages.length > 0 &&
    messages[messages.length - 1]?.role === 'user'

  // Get status text for voice input
  const getVoiceStatusText = () => {
    if (isAiSpeaking) return 'AI is speaking...'
    if (isRecording) return 'Listening... speak now'
    return 'Click mic to talk'
  }

  // Get mic button class
  const getMicButtonClass = () => {
    let cls = 'mic-button'
    if (isRecording) cls += ' recording'
    if (isAiSpeaking) cls += ' ai-speaking'
    return cls
  }

  return (
    <div className="conversation-mode">
      {/* Unified message list */}
      <div
        className="unified-message-list"
        ref={messageListRef}
        role="log"
        aria-label="Conversation"
        aria-live="polite"
      >
        {/* Waiting for AI to introduce itself */}
        {messages.length === 0 && !streamingMessage && (
          <div className="waiting-for-ai">
            <div className="waiting-avatar ai-badge" aria-hidden="true">AI</div>
            <div className="waiting-text">
              <div className="typing-indicator" aria-label="AI is preparing">
                <div className="typing-dot" />
                <div className="typing-dot" />
                <div className="typing-dot" />
              </div>
              <p>Connecting to your assistant...</p>
            </div>
          </div>
        )}

        {/* Messages */}
        {displayMessages.map((message) => (
          <div
            key={message.id}
            className={`unified-message ${message.role}`}
          >
            {/* Avatar */}
            <div className={`message-avatar ${message.role === 'ai' ? 'ai-badge' : ''}`}>
              {message.role === 'ai' ? 'AI' : (
                <UserIcon size={18} />
              )}
            </div>

            {/* Content */}
            <div className="message-content">
              <div className={`message-bubble ${message.isStreaming ? 'streaming' : ''}`}>
                {message.content}
                {message.isStreaming && (
                  <span className="cursor" aria-hidden="true">|</span>
                )}
              </div>
              <div className="message-meta">
                <span className="message-sender">
                  {message.role === 'user' ? 'You' : message.role === 'ai' ? 'AI Assistant' : ''}
                </span>
                {message.inputMethod && (
                  <span
                    className="message-input-method"
                    aria-label={`Sent via ${message.inputMethod}`}
                    title={message.inputMethod === 'voice' ? 'Voice input' : 'Text input'}
                  >
                    {message.inputMethod === 'voice' ? (
                      <MicrophoneIcon size={12} />
                    ) : (
                      <KeyboardIcon size={12} />
                    )}
                  </span>
                )}
                {message.hasAudio && (
                  <span
                    className="message-audio-indicator"
                    aria-label="Response played as audio"
                    title="Played as audio"
                  >
                    <WaveformIcon size={12} />
                  </span>
                )}
                {message.timestamp && (
                  <time className="message-time"> {message.timestamp}</time>
                )}
              </div>
            </div>
          </div>
        ))}

        {/* Typing indicator */}
        {isWaitingForResponse && (
          <div className="unified-message ai">
            <div className="message-avatar ai-badge">AI</div>
            <div className="message-content">
              <div className="typing-indicator" aria-label="AI is typing">
                <div className="typing-dot" />
                <div className="typing-dot" />
                <div className="typing-dot" />
              </div>
            </div>
          </div>
        )}
      </div>

      {/* Input area with mode toggle */}
      <div className="conversation-input-area">
        {/* Mode toggle */}
        <div
          className={`mode-toggle ${isAiSpeaking ? 'disabled' : ''}`}
          role="radiogroup"
          aria-label="Input mode"
        >
          <button
            type="button"
            role="radio"
            aria-checked={inputMode === 'voice'}
            className={`toggle-option ${inputMode === 'voice' ? 'active' : ''}`}
            onClick={() => onToggleInputMode('voice')}
            disabled={isAiSpeaking}
          >
            <MicrophoneIcon size={16} />
            Voice
          </button>

          <div
            className="toggle-track"
            onClick={() => !isAiSpeaking && onToggleInputMode(inputMode === 'voice' ? 'text' : 'voice')}
            role="presentation"
          >
            <div className={`toggle-thumb ${inputMode === 'voice' ? 'left' : 'right'}`} />
          </div>

          <button
            type="button"
            role="radio"
            aria-checked={inputMode === 'text'}
            className={`toggle-option ${inputMode === 'text' ? 'active' : ''}`}
            onClick={() => onToggleInputMode('text')}
            disabled={isAiSpeaking}
          >
            <KeyboardIcon size={16} />
            Text
          </button>
        </div>

        {/* Dynamic input area based on mode */}
        {inputMode === 'voice' ? (
          /* Voice input */
          <div className="voice-input-compact">
            <div className="voice-status-indicator">
              <span
                className={`status-dot ${isRecording ? 'recording' : ''} ${isAiSpeaking ? 'speaking' : ''}`}
              />
              <span className="voice-status-text-compact">{getVoiceStatusText()}</span>
            </div>
            <button
              type="button"
              className={getMicButtonClass()}
              onClick={handleMicClick}
              disabled={isAiSpeaking}
              aria-label={isRecording ? 'Stop recording' : 'Start recording'}
              aria-pressed={isRecording}
            >
              {isAiSpeaking ? (
                <WaveformIcon size={24} />
              ) : (
                <MicrophoneIcon size={24} />
              )}
            </button>
          </div>
        ) : (
          /* Text input */
          <form
            className="input-wrapper"
            onSubmit={(e) => { e.preventDefault(); handleSubmit(); }}
          >
            <textarea
              ref={textareaRef}
              className="text-input"
              placeholder="Type your message..."
              value={inputText}
              onChange={handleInputChange}
              onKeyDown={handleKeyDown}
              rows={1}
              aria-label="Message input"
            />
            <button
              type="submit"
              className="action-btn send"
              disabled={!inputText.trim()}
              aria-label="Send message"
            >
              <SendIcon size={20} />
            </button>
          </form>
        )}

        {/* Quick actions */}
        <div className="quick-actions" role="group" aria-label="Quick actions">
          <button
            type="button"
            className="quick-action"
            onClick={() => onSwitchTab('agent')}
            aria-label="Speak to a human agent"
          >
            <UserIcon size={16} />
            Speak to Agent
          </button>
          <button
            type="button"
            className="quick-action"
            onClick={() => onSendMessage('I need help')}
            aria-label="Get help"
          >
            <HelpIcon size={16} />
            Help
          </button>
        </div>
      </div>
    </div>
  )
}

export default ConversationMode
