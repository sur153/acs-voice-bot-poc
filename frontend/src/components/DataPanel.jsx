import React, { useState, useEffect, useCallback } from 'react'
import { ChevronRightIcon, ChevronDownIcon, DatabaseIcon, RefreshIcon } from './Icons'

/**
 * DataPanel - Real-time display of collected conversation data
 * Fetches official data from Cosmos DB (MCP-collected) and displays it
 */
function DataPanel({ messages, sessionData, isCollapsed, onToggle, onProgressUpdate }) {
  const [expandedSections, setExpandedSections] = useState({
    personal: true,
    employment: false,
    health: false,
    family: false,
    medical: false,
    session: true
  })

  const [viewMode, setViewMode] = useState('table') // 'table' or 'json'
  const [officialData, setOfficialData] = useState(null)
  const [isLoading, setIsLoading] = useState(false)
  const [lastFetch, setLastFetch] = useState(null)

  // Get session ID from sessionData
  const sessionId = sessionData?.meta?.session_id

  // Fetch official data from Cosmos
  const fetchOfficialData = useCallback(async () => {
    if (!sessionId) return

    setIsLoading(true)
    try {
      const response = await fetch(`/api/session/${sessionId}/collected-data`)
      const result = await response.json()

      if (result.status === 'success' && result.data) {
        setOfficialData(result.data)
        // Report progress based on navigation_path length (answered questions)
        if (onProgressUpdate && result.data.navigation_path) {
          onProgressUpdate(result.data.navigation_path.length)
        }
      }
      setLastFetch(new Date())
    } catch (error) {
      console.error('Failed to fetch official data:', error)
    } finally {
      setIsLoading(false)
    }
  }, [sessionId, onProgressUpdate])

  // Poll for updates every 4 seconds while session is active
  useEffect(() => {
    if (!sessionId) return

    // Initial fetch
    fetchOfficialData()

    // Set up polling
    const interval = setInterval(fetchOfficialData, 4000)

    return () => clearInterval(interval)
  }, [sessionId, fetchOfficialData])

  const toggleSection = (section) => {
    setExpandedSections(prev => ({
      ...prev,
      [section]: !prev[section]
    }))
  }

  // Transform responses into a flat list for counting
  const getResponseCount = () => {
    if (!officialData?.responses) return { answered: 0, total: 0 }

    let answered = 0
    Object.values(officialData.responses).forEach(category => {
      if (category && typeof category === 'object') {
        answered += Object.keys(category).length
      }
    })
    return { answered, total: answered } // Total unknown, just show answered
  }

  const { answered } = getResponseCount()

  // Render a response category section
  const renderResponseSection = (categoryKey, categoryName) => {
    const responses = officialData?.responses?.[categoryKey]
    if (!responses || Object.keys(responses).length === 0) return null

    const questionCount = Object.keys(responses).length
    const navPath = officialData?.navigation_path || []

    // Sort responses by their position in navigation_path (answered order)
    const sortedEntries = Object.entries(responses).sort(([qIdA], [qIdB]) => {
      const indexA = navPath.indexOf(qIdA)
      const indexB = navPath.indexOf(qIdB)
      // If not in navPath, put at end
      if (indexA === -1 && indexB === -1) return 0
      if (indexA === -1) return 1
      if (indexB === -1) return -1
      return indexA - indexB
    })

    return (
      <div className="data-section" key={categoryKey}>
        <button
          className="section-header"
          onClick={() => toggleSection(categoryKey)}
        >
          {expandedSections[categoryKey] ? <ChevronDownIcon size={16} /> : <ChevronRightIcon size={16} />}
          <span>{categoryName}</span>
          <span className="section-count">{questionCount} answers</span>
        </button>
        {expandedSections[categoryKey] && (
          <div className="section-content">
            <table className="data-table">
              <tbody>
                {sortedEntries.map(([qId, q]) => (
                  <tr key={qId} className="filled">
                    <td className="field-name" title={q.question}>
                      {q.field_name || qId}
                    </td>
                    <td className="field-value">
                      <span className="value">{formatAnswer(q.answer)}</span>
                      {q.confirmation_status === 'CONFIRMED' && (
                        <span className="confirmed-badge" title="Confirmed by user">✓</span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    )
  }

  if (isCollapsed) {
    return (
      <div className="data-panel collapsed" onClick={onToggle}>
        <div className="data-panel-collapsed-content">
          <DatabaseIcon size={20} />
          <span className="collapsed-label">Data</span>
          <span className="collapsed-count">
            {officialData ? answered : '—'}
          </span>
        </div>
      </div>
    )
  }

  return (
    <div className="data-panel">
      {/* Header */}
      <div className="data-panel-header">
        <div className="data-panel-title">
          <DatabaseIcon size={18} />
          <span>Collected Data</span>
          {officialData && (
            <span className="data-count">{answered} answers</span>
          )}
          {isLoading && <span className="loading-indicator">•</span>}
        </div>
        <div className="data-panel-actions">
          <button
            className="refresh-btn"
            onClick={fetchOfficialData}
            disabled={isLoading}
            title="Refresh data"
          >
            <RefreshIcon size={14} />
          </button>
          <div className="view-toggle">
            <button
              className={`view-btn ${viewMode === 'table' ? 'active' : ''}`}
              onClick={() => setViewMode('table')}
              title="Table View"
            >
              Table
            </button>
            <button
              className={`view-btn ${viewMode === 'json' ? 'active' : ''}`}
              onClick={() => setViewMode('json')}
              title="JSON View"
            >
              JSON
            </button>
          </div>
          <button className="collapse-btn" onClick={onToggle} title="Collapse panel">
            <ChevronRightIcon size={16} />
          </button>
        </div>
      </div>

      {/* Content */}
      <div className="data-panel-content">
        {!officialData ? (
          <div className="empty-state">
            <p>{sessionId ? 'Waiting for data...' : 'No active session'}</p>
            {sessionId && <p className="hint">Data appears as questions are answered</p>}
          </div>
        ) : viewMode === 'table' ? (
          <>
            {/* Applicant Info Section - only show if we have any applicant data */}
            {(officialData.applicant?.full_name || officialData.applicant?.phone_number) && (
              <div className="data-section">
                <button
                  className="section-header"
                  onClick={() => toggleSection('personal')}
                >
                  {expandedSections.personal ? <ChevronDownIcon size={16} /> : <ChevronRightIcon size={16} />}
                  <span>Applicant</span>
                </button>
                {expandedSections.personal && (
                  <div className="section-content">
                    <table className="data-table">
                      <tbody>
                        {officialData.applicant?.full_name && (
                          <tr className="filled">
                            <td className="field-name">Full Name</td>
                            <td className="field-value">{officialData.applicant.full_name}</td>
                          </tr>
                        )}
                        {officialData.applicant?.phone_number && (
                          <tr className="filled">
                            <td className="field-name">Phone</td>
                            <td className="field-value">{officialData.applicant.phone_number}</td>
                          </tr>
                        )}
                      </tbody>
                    </table>
                  </div>
                )}
              </div>
            )}

            {/* Consent Section */}
            {officialData.consent && Object.keys(officialData.consent).length > 0 && (
              <div className="data-section">
                <button
                  className="section-header"
                  onClick={() => toggleSection('consent')}
                >
                  {expandedSections.consent ? <ChevronDownIcon size={16} /> : <ChevronRightIcon size={16} />}
                  <span>Consent</span>
                </button>
                {expandedSections.consent && (
                  <div className="section-content">
                    <table className="data-table">
                      <tbody>
                        <tr className="filled">
                          <td className="field-name">Recording Consent</td>
                          <td className="field-value">
                            {officialData.consent.recording_consent ? '✓ Yes' : '✗ No'}
                          </td>
                        </tr>
                        <tr className="filled">
                          <td className="field-name">HIPAA Acknowledged</td>
                          <td className="field-value">
                            {officialData.consent.hipaa_acknowledged ? '✓ Yes' : '✗ No'}
                          </td>
                        </tr>
                      </tbody>
                    </table>
                  </div>
                )}
              </div>
            )}

            {/* Response Categories */}
            {renderResponseSection('personal', 'Personal Information')}
            {renderResponseSection('employment', 'Employment')}
            {renderResponseSection('health_history', 'Health History')}
            {renderResponseSection('family_history', 'Family History')}
            {renderResponseSection('medical', 'Medical')}

            {/* Session Info Section */}
            <div className="data-section">
              <button
                className="section-header"
                onClick={() => toggleSection('session')}
              >
                {expandedSections.session ? <ChevronDownIcon size={16} /> : <ChevronRightIcon size={16} />}
                <span>Session Info</span>
              </button>
              {expandedSections.session && (
                <div className="section-content">
                  <table className="data-table">
                    <tbody>
                      <tr className="filled">
                        <td className="field-name">Session ID</td>
                        <td className="field-value">
                          <span className="value mono">{officialData.meta?.session_id || 'N/A'}</span>
                        </td>
                      </tr>
                      <tr className="filled">
                        <td className="field-name">Status</td>
                        <td className="field-value">
                          <span className={`status-badge ${(officialData.meta?.status || 'active').toLowerCase()}`}>
                            {officialData.meta?.status || 'Active'}
                          </span>
                        </td>
                      </tr>
                      {officialData.meta?.agent_name && (
                        <tr className="filled">
                          <td className="field-name">Agent</td>
                          <td className="field-value">
                            {officialData.meta.agent_name}{officialData.meta.agent_version ? ` v${officialData.meta.agent_version}` : ''}
                          </td>
                        </tr>
                      )}
                      <tr className="filled">
                        <td className="field-name">Questions Answered</td>
                        <td className="field-value">
                          {officialData.navigation_path?.length || 0}
                        </td>
                      </tr>
                    </tbody>
                  </table>
                </div>
              )}
            </div>
          </>
        ) : (
          /* JSON View */
          <div className="json-view">
            <pre className="json-content">
              {JSON.stringify(officialData, null, 2)}
            </pre>
          </div>
        )}
      </div>

      {/* Footer */}
      <div className="data-panel-footer">
        <span className="update-time">
          {lastFetch ? `Updated: ${lastFetch.toLocaleTimeString()}` : 'Not yet fetched'}
        </span>
        <span className="data-source">Source: Cosmos DB</span>
      </div>
    </div>
  )
}

// Helper function to format answer values
function formatAnswer(value) {
  if (value === null || value === undefined) return '—'
  if (typeof value === 'boolean') return value ? 'Yes' : 'No'
  if (typeof value === 'number') return value.toString()
  return value
}

export default DataPanel
