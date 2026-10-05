import { createContext, useContext, useState, type ReactNode } from 'react'

const STORAGE_KEY = 'coe-wizard:edit-mode'

interface EditModeValue {
  editing: boolean
  setEditing: (on: boolean) => void
}

const EditModeContext = createContext<EditModeValue>({ editing: false, setEditing: () => {} })

function readInitial(): boolean {
  try {
    return localStorage.getItem(STORAGE_KEY) === '1'
  } catch {
    return false
  }
}

export function EditModeProvider({ children }: { children: ReactNode }) {
  const [editing, setEditingState] = useState(readInitial)
  const setEditing = (on: boolean) => {
    setEditingState(on)
    try {
      localStorage.setItem(STORAGE_KEY, on ? '1' : '0')
    } catch {
      // storage unavailable; the toggle still works for this tab
    }
  }
  return <EditModeContext value={{ editing, setEditing }}>{children}</EditModeContext>
}

// eslint-disable-next-line react-refresh/only-export-components
export function useEditMode() {
  return useContext(EditModeContext)
}
