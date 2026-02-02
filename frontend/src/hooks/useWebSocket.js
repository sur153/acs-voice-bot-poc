import { useRef, useCallback, useState } from 'react'

/**
 * Custom hook for WebSocket connection management
 * Handles unified WebSocket for both chat and voice modes
 */
export function useWebSocket() {
  const socketRef = useRef(null)
  const [isConnected, setIsConnected] = useState(false)
  const [connectionStatus, setConnectionStatus] = useState('disconnected')

  // Connect to WebSocket
  const connect = useCallback((onMessage, onOpen, onClose, onError) => {
    if (socketRef.current?.readyState === WebSocket.OPEN) {
      return Promise.resolve(socketRef.current)
    }

    return new Promise((resolve, reject) => {
      setConnectionStatus('connecting')

      const wsProtocol = window.location.protocol === 'https:' ? 'wss' : 'ws'
      const wsHost = window.location.host
      const socket = new WebSocket(`${wsProtocol}://${wsHost}/web/ws`)
      socket.binaryType = 'arraybuffer'

      socket.onopen = () => {
        console.log('WebSocket connected')
        setIsConnected(true)
        setConnectionStatus('connected')
        socketRef.current = socket
        onOpen?.()
        resolve(socket)
      }

      socket.onmessage = (event) => {
        onMessage?.(event)
      }

      socket.onclose = () => {
        console.log('WebSocket closed')
        setIsConnected(false)
        setConnectionStatus('disconnected')
        socketRef.current = null
        onClose?.()
      }

      socket.onerror = (error) => {
        console.error('WebSocket error:', error)
        setConnectionStatus('error')
        onError?.(error)
        reject(error)
      }
    })
  }, [])

  // Send data through WebSocket
  const send = useCallback((data) => {
    if (socketRef.current?.readyState === WebSocket.OPEN) {
      socketRef.current.send(data)
      return true
    }
    return false
  }, [])

  // Send JSON message
  const sendJSON = useCallback((data) => {
    return send(JSON.stringify(data))
  }, [send])

  // Send binary data (audio)
  const sendBinary = useCallback((arrayBuffer) => {
    return send(arrayBuffer)
  }, [send])

  // Close WebSocket connection
  const disconnect = useCallback(() => {
    if (socketRef.current) {
      socketRef.current.close()
      socketRef.current = null
    }
    setIsConnected(false)
    setConnectionStatus('disconnected')
  }, [])

  // Check if connected
  const getIsConnected = useCallback(() => {
    return socketRef.current?.readyState === WebSocket.OPEN
  }, [])

  return {
    connect,
    send,
    sendJSON,
    sendBinary,
    disconnect,
    isConnected,
    connectionStatus,
    getIsConnected
  }
}

export default useWebSocket
