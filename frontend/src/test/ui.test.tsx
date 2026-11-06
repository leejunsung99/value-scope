import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { fmtDate, ScoreBar } from '../ui'

describe('ScoreBar', () => {
  it('shows 평가 보류 instead of 0 when score is unavailable', () => {
    render(<ScoreBar score={null} coverage={0.1} />)
    expect(screen.getByText(/평가 보류/)).toBeInTheDocument()
    expect(screen.queryByText('0.0')).not.toBeInTheDocument()
  })
  it('renders numeric score', () => {
    render(<ScoreBar score={87.5} coverage={0.75} />)
    expect(screen.getByText('87.5')).toBeInTheDocument()
  })
})

describe('fmtDate', () => {
  it('formats DART yyyymmdd', () => {
    expect(fmtDate('20250318')).toBe('2025.03.18')
  })
})
