import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter } from 'react-router'
import './theme.css'
import App from './App.tsx'
import { AuthProvider } from './auth/AuthProvider.tsx'

// The router sits outside `AuthProvider`, so the URL survives the sign-in gate.
// `App` renders `<Auth/>` in place of the shell while signed out without
// navigating anywhere, which means signing in returns you to the address you
// arrived on rather than to the roster.
createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <BrowserRouter>
      <AuthProvider>
        <App />
      </AuthProvider>
    </BrowserRouter>
  </StrictMode>,
)
