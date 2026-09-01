import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { SidebarDateDivider, SidebarSectionAddButton } from './chrome'

afterEach(cleanup)

it('keeps the project creation control visible at rest and opens its existing flow once', () => {
  const onPlainClick = vi.fn()
  render(
    <SidebarSectionAddButton
      ariaLabel="New project"
      onNewProjectDrag={{ onArm: vi.fn() }}
      onPlainClick={onPlainClick}
    />
  )
  const button = screen.getByRole('button', { name: 'New project' })
  expect(button.classList.contains('opacity-0')).toBe(false)
  expect(button.classList.contains('opacity-70')).toBe(true)
  fireEvent.click(button)
  expect(onPlainClick).toHaveBeenCalledOnce()
})

describe('SidebarDateDivider', () => {
  it('collapses the group when the caption is clicked', () => {
    const onToggle = vi.fn()

    render(
      <SidebarDateDivider label="Yesterday" toggle={{ ariaLabel: 'Hide Yesterday sessions', onToggle, open: true }} />
    )

    fireEvent.click(screen.getByRole('button', { name: 'Hide Yesterday sessions' }))
    expect(onToggle).toHaveBeenCalledOnce()
    expect(screen.getByRole('button', { name: 'Hide Yesterday sessions' }).getAttribute('aria-expanded')).toBe('true')
  })

  it('stays a static caption when it is not collapsible', () => {
    render(<SidebarDateDivider label="Yesterday" />)

    expect(screen.queryByRole('button')).toBeNull()
    expect(screen.getByText('Yesterday')).toBeTruthy()
  })
})
