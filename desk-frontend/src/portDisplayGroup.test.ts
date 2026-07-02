import { describe, expect, it } from 'vitest'
import { resolvePortDisplayGroup } from './portDisplayGroup'

describe('resolvePortDisplayGroup', () => {
  it('treats a loaded favicon as active', () => {
    expect(
      resolvePortDisplayGroup({
        faviconLoaded: true,
        probeDone: false,
        reachability: null,
        lastSettledGroup: 'broken',
      }),
    ).toBe('active')
  })

  it('groups dead probes as broken', () => {
    expect(
      resolvePortDisplayGroup({
        faviconLoaded: false,
        probeDone: true,
        reachability: 'dead',
        lastSettledGroup: null,
      }),
    ).toBe('broken')
  })

  it('keeps live and unknown probes active', () => {
    expect(
      resolvePortDisplayGroup({
        faviconLoaded: false,
        probeDone: true,
        reachability: 'live',
        lastSettledGroup: null,
      }),
    ).toBe('active')
    expect(
      resolvePortDisplayGroup({
        faviconLoaded: false,
        probeDone: true,
        reachability: 'unknown',
        lastSettledGroup: null,
      }),
    ).toBe('active')
  })

  it('stays active while probing when there is no prior state', () => {
    expect(
      resolvePortDisplayGroup({
        faviconLoaded: false,
        probeDone: false,
        reachability: null,
        lastSettledGroup: null,
      }),
    ).toBe('active')
  })

  it('stays active while re-probing when previously active', () => {
    expect(
      resolvePortDisplayGroup({
        faviconLoaded: false,
        probeDone: false,
        reachability: null,
        lastSettledGroup: 'active',
      }),
    ).toBe('active')
  })

  it('stays hidden in broken while re-probing when previously broken', () => {
    expect(
      resolvePortDisplayGroup({
        faviconLoaded: false,
        probeDone: false,
        reachability: null,
        lastSettledGroup: 'broken',
      }),
    ).toBe('broken')
  })
})
