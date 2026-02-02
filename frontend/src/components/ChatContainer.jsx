import React, { useState } from 'react'
import ConversationMode from './ConversationMode'
import AgentMode from './AgentMode'
import DataPanel from './DataPanel'
import { ChatIcon, UserIcon, DatabaseIcon } from './Icons'

function ChatContainer({
  activeTab,
  inputMode,
  onSwitchTab,
  onToggleInputMode,
  messages,
  streamingMessage,
  isConnected,
  isRecording,
  isAiSpeaking,
  onStartRecording,
  onStopRecording,
  onSendMessage,
  onEndSession,
  onRequestTransfer,
  transferStatus,
  transferError,
  sessionData,
  onProgressUpdate
}) {
  const [isDataPanelCollapsed, setIsDataPanelCollapsed] = useState(false)
  const tabs = [
    { id: 'conversation', label: 'Conversation', icon: ChatIcon },
    { id: 'agent', label: 'Agent', icon: UserIcon }
  ]

  return (
    <div className={`chat-container-wrapper ${isDataPanelCollapsed ? 'panel-collapsed' : 'panel-expanded'}`}>
      <div className="chat-container">
        {/* Tab Navigation - 2 tabs only */}
        <nav className="mode-tabs" role="tablist" aria-label="Interaction modes">
          {tabs.map(({ id, label, icon: Icon }) => (
            <button
              key={id}
              type="button"
              role="tab"
              className={`mode-tab ${activeTab === id ? 'active' : ''}`}
              onClick={() => onSwitchTab(id)}
              aria-selected={activeTab === id}
              aria-controls={`${id}-panel`}
              id={`${id}-tab`}
            >
              <Icon size={18} />
              <span>{label}</span>
              {id === 'conversation' && (
                <span
                  className={`connection-dot ${isConnected ? 'connected' : ''}`}
                  aria-hidden="true"
                />
              )}
            </button>
          ))}
          {/* Data Panel Toggle in Header */}
          <button
            type="button"
            className={`mode-tab data-toggle ${!isDataPanelCollapsed ? 'active' : ''}`}
            onClick={() => setIsDataPanelCollapsed(!isDataPanelCollapsed)}
            title={isDataPanelCollapsed ? 'Show Data Panel' : 'Hide Data Panel'}
          >
            <DatabaseIcon size={18} />
            <span>Data</span>
          </button>
        </nav>

        {/* Tab Content */}
        <div className="mode-content-wrapper">
          {activeTab === 'conversation' && (
            <div
              role="tabpanel"
              id="conversation-panel"
              aria-labelledby="conversation-tab"
              className="tab-panel"
            >
              <ConversationMode
                messages={messages}
                streamingMessage={streamingMessage}
                inputMode={inputMode}
                onToggleInputMode={onToggleInputMode}
                isRecording={isRecording}
                isAiSpeaking={isAiSpeaking}
                onStartRecording={onStartRecording}
                onStopRecording={onStopRecording}
                onSendMessage={onSendMessage}
                onSwitchTab={onSwitchTab}
              />
            </div>
          )}

          {activeTab === 'agent' && (
            <div
              role="tabpanel"
              id="agent-panel"
              aria-labelledby="agent-tab"
              className="tab-panel"
            >
              <AgentMode
                onSwitchTab={onSwitchTab}
                onRequestTransfer={onRequestTransfer}
                transferStatus={transferStatus}
                transferError={transferError}
              />
            </div>
          )}
        </div>
      </div>

      {/* Data Panel - Side Panel */}
      <DataPanel
        messages={messages}
        sessionData={sessionData}
        isCollapsed={isDataPanelCollapsed}
        onToggle={() => setIsDataPanelCollapsed(!isDataPanelCollapsed)}
        onProgressUpdate={onProgressUpdate}
      />
    </div>
  )
}

export default ChatContainer
