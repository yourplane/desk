import { describe, expect, it } from 'vitest'
import { classifyRouteHttpStatus } from './webRouteProbe'

describe('classifyRouteHttpStatus', () => {
  it('treats router upstream errors as dead', () => {
    expect(classifyRouteHttpStatus(502)).toBe('dead')
    expect(classifyRouteHttpStatus(504)).toBe('dead')
  })

  it('treats unmatched routes as dead', () => {
    expect(classifyRouteHttpStatus(404)).toBe('dead')
  })

  it('treats success and redirects as live', () => {
    expect(classifyRouteHttpStatus(200)).toBe('live')
    expect(classifyRouteHttpStatus(301)).toBe('live')
  })

  it('treats other HTTP errors as live when the server responded', () => {
    expect(classifyRouteHttpStatus(403)).toBe('live')
    expect(classifyRouteHttpStatus(500)).toBe('live')
  })
})
