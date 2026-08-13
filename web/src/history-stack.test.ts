import { describe, expect, it } from 'vitest'
import {
  EMPTY,
  canGoBack,
  canGoForward,
  visit,
  type HistoryModel,
} from './history-stack'

/** Walk a sequence of visits from empty, so a test reads as the navigation it
 *  describes rather than as a pile of intermediate models. */
function walk(...steps: Array<[string, 'PUSH' | 'POP' | 'REPLACE']>): HistoryModel {
  return steps.reduce((m, [key, type]) => visit(m, key, type), EMPTY)
}

describe('history-stack', () => {
  describe('the first entry', () => {
    // React Router reports the initial render as a POP, so the empty model has
    // to absorb one without treating it as a step backwards from nothing.
    it.each(['PUSH', 'POP', 'REPLACE'] as const)(
      'a %s into the empty model seeds a single entry',
      (type) => {
        const m = visit(EMPTY, 'a', type)
        expect(m).toEqual({ keys: ['a'], index: 0 })
      },
    )

    it('offers neither direction', () => {
      const m = walk(['a', 'POP'])
      expect(canGoBack(m)).toBe(false)
      expect(canGoForward(m)).toBe(false)
    })
  })

  describe('PUSH', () => {
    it('appends and moves to the new entry', () => {
      const m = walk(['a', 'POP'], ['b', 'PUSH'])
      expect(m).toEqual({ keys: ['a', 'b'], index: 1 })
    })

    it('opens back and leaves forward closed', () => {
      const m = walk(['a', 'POP'], ['b', 'PUSH'])
      expect(canGoBack(m)).toBe(true)
      expect(canGoForward(m)).toBe(false)
    })

    // The invariant this file exists for: `keys` holds exactly what is
    // reachable. Going back and then somewhere new abandons the old forward
    // branch, and an implementation that appends without truncating leaves
    // Forward enabled pointing into it.
    it('truncates the forward tail it just abandoned', () => {
      const m = walk(
        ['a', 'POP'],
        ['b', 'PUSH'],
        ['c', 'PUSH'],
        ['b', 'POP'], // back to b
        ['d', 'PUSH'], // somewhere new — c is now unreachable
      )
      expect(m).toEqual({ keys: ['a', 'b', 'd'], index: 2 })
      expect(m.keys).not.toContain('c')
      expect(canGoForward(m)).toBe(false)
    })
  })

  describe('POP', () => {
    it('re-indexes backwards without dropping the forward entries', () => {
      const m = walk(['a', 'POP'], ['b', 'PUSH'], ['c', 'PUSH'], ['b', 'POP'])
      expect(m).toEqual({ keys: ['a', 'b', 'c'], index: 1 })
      expect(canGoBack(m)).toBe(true)
      expect(canGoForward(m)).toBe(true)
    })

    it('re-indexes forwards', () => {
      const m = walk(
        ['a', 'POP'],
        ['b', 'PUSH'],
        ['a', 'POP'],
        ['b', 'POP'], // forward again
      )
      expect(m).toEqual({ keys: ['a', 'b'], index: 1 })
    })

    // The browser's own history menu can jump several entries at once, which is
    // why POP resolves by lookup rather than by stepping one position.
    it('absorbs a multi-step jump', () => {
      const m = walk(
        ['a', 'POP'],
        ['b', 'PUSH'],
        ['c', 'PUSH'],
        ['d', 'PUSH'],
        ['a', 'POP'],
      )
      expect(m).toEqual({ keys: ['a', 'b', 'c', 'd'], index: 0 })
      expect(canGoBack(m)).toBe(false)
      expect(canGoForward(m)).toBe(true)
    })

    // An entry from before this application loaded. We cannot know what sits on
    // either side of it, and reporting "no" for an unknown is honest — guessing
    // produces a button that does nothing when pressed.
    it('resets on a key it has never seen', () => {
      const m = walk(['a', 'POP'], ['b', 'PUSH'], ['c', 'PUSH'], ['stranger', 'POP'])
      expect(m).toEqual({ keys: ['stranger'], index: 0 })
      expect(canGoBack(m)).toBe(false)
      expect(canGoForward(m)).toBe(false)
    })
  })

  describe('REPLACE', () => {
    // What keeps a guard redirect from lengthening the history: the redirecting
    // URL is overwritten rather than appended, so Back skips past it.
    it('overwrites in place, changing neither length nor position', () => {
      const m = walk(['a', 'POP'], ['b', 'PUSH'], ['c', 'REPLACE'])
      expect(m).toEqual({ keys: ['a', 'c'], index: 1 })
    })

    it('leaves the entries behind it reachable', () => {
      const m = walk(['a', 'POP'], ['b', 'PUSH'], ['c', 'REPLACE'])
      expect(canGoBack(m)).toBe(true)
      expect(canGoForward(m)).toBe(false)
    })

    it('does not disturb a forward tail', () => {
      const m = walk(
        ['a', 'POP'],
        ['b', 'PUSH'],
        ['c', 'PUSH'],
        ['b', 'POP'],
        ['x', 'REPLACE'],
      )
      expect(m).toEqual({ keys: ['a', 'x', 'c'], index: 1 })
    })
  })

  describe('canGoBack / canGoForward', () => {
    it('are both false at a lone entry', () => {
      const m = walk(['a', 'POP'])
      expect([canGoBack(m), canGoForward(m)]).toEqual([false, false])
    })

    it('are both true in the middle', () => {
      const m = walk(['a', 'POP'], ['b', 'PUSH'], ['c', 'PUSH'], ['b', 'POP'])
      expect([canGoBack(m), canGoForward(m)]).toEqual([true, true])
    })

    it('report false on the empty model', () => {
      expect(canGoBack(EMPTY)).toBe(false)
      expect(canGoForward(EMPTY)).toBe(false)
    })
  })

  it('never mutates the model it is given', () => {
    const before = walk(['a', 'POP'], ['b', 'PUSH'])
    const snapshot = { keys: [...before.keys], index: before.index }
    visit(before, 'c', 'PUSH')
    visit(before, 'c', 'REPLACE')
    visit(before, 'a', 'POP')
    expect(before).toEqual(snapshot)
  })
})
