import React from 'react'

function Header({ progress = 15 }) {
  return (
    <header className="header">
      <div className="logo">
        <div className="logo-icon">P</div>
        <div className="logo-text">
          Protective <span>TeleLife</span>
        </div>
      </div>

      <div className="progress-bar">
        <span className="progress-label">Application Progress</span>
        <div className="progress-track">
          <div
            className="progress-fill"
            style={{ width: `${progress}%` }}
          />
        </div>
        <span className="progress-text">{progress}%</span>
      </div>
    </header>
  )
}

export default Header
