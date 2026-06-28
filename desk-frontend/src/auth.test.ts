import { describe, expect, it } from 'vitest'
import {
  PROACTIVE_REFRESH_BEFORE_MS,
  getProactiveRefreshDelayMs,
  idTokenCookieMaxAgeSeconds,
} from './auth'

function fakeJwt(expSec: number): string {
  const header = btoa(JSON.stringify({ alg: 'none', typ: 'JWT' }))
  const payload = btoa(JSON.stringify({ exp: expSec }))
  return `${header}.${payload}.sig`
}

describe('session keeper scheduling', () => {
  it('schedules refresh five minutes before JWT exp', () => {
    const nowMs = 1_700_000_000_000
    const expSec = Math.floor(nowMs / 1000) + 3600
    const token = fakeJwt(expSec)
    const delay = getProactiveRefreshDelayMs(token, nowMs)
    expect(delay).toBe(3600 * 1000 - PROACTIVE_REFRESH_BEFORE_MS)
  })

  it('returns zero delay when inside the proactive refresh window', () => {
    const nowMs = 1_700_000_000_000
    const expSec = Math.floor(nowMs / 1000) + 120
    const token = fakeJwt(expSec)
    expect(getProactiveRefreshDelayMs(token, nowMs)).toBe(0)
  })

  it('derives cookie max-age from JWT exp', () => {
    const nowSec = 1_700_000_000
    const token = fakeJwt(nowSec + 1800)
    expect(idTokenCookieMaxAgeSeconds(token, nowSec)).toBe(1800)
  })
})
