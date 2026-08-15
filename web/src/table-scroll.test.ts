// A wide table must scroll inside its own box rather than widening the page.
//
// This is checked by reading the source rather than by rendering, because the
// defect it guards is invisible to every other test in this suite: jsdom
// applies no stylesheet and performs no layout, so `getBoundingClientRect()`
// returns zeroes and a table that overruns its card measures exactly the same
// as one that fits. The rendering tests cannot fail on it, and did not — the
// seven-column vendor list on the item screen shipped 790px wide inside a
// 598px card, and the whole document scrolled sideways with it, while every
// test stayed green.
//
// What jsdom *cannot* see is the overflow. What a file *can* see is a missing
// wrapper, which is the thing that actually causes it. So the assertion is
// structural: every `.table` is wrapped, and the wrapper still scrolls.
//
// Scanning source also catches the case a per-page render test never would —
// a table added to a page that has no test, or to one whose test does not
// happen to render that branch. Six of the seventeen tables here sit behind an
// RFQ stage that most fixtures never reach.
import { describe, expect, it } from 'vitest'
import { readFileSync, readdirSync } from 'node:fs'
import { join, relative, sep } from 'node:path'

// Anchored to the Vitest root (the `web` package) rather than to
// `import.meta.url`: the suite runs in the jsdom environment, where
// `import.meta.url` is an `http://localhost/` URL and resolves to no file at
// all. The `finds the tables` case below is what catches this going wrong —
// a bad root reads an empty directory and would otherwise pass silently.
const SRC = join(process.cwd(), 'src')

function sourceFiles(dir: string): string[] {
  return readdirSync(dir, { withFileTypes: true }).flatMap((entry) => {
    const full = join(dir, entry.name)
    if (entry.isDirectory()) return sourceFiles(full)
    return entry.isFile() && entry.name.endsWith('.tsx') && !entry.name.endsWith('.test.tsx')
      ? [full]
      : []
  })
}

/** Every `<table className="table">` in the app, with the line above it. */
function tableSites(): { file: string; line: number; previous: string }[] {
  return sourceFiles(SRC).flatMap((file) => {
    const lines = readFileSync(file, 'utf8').split(/\r?\n/)
    return lines.flatMap((text, i) =>
      /<table className="table">/.test(text)
        ? [
            {
              file: relative(SRC, file).split(sep).join('/'),
              line: i + 1,
              previous: (lines[i - 1] ?? '').trim(),
            },
          ]
        : [],
    )
  })
}

describe('wide tables scroll inside their card, not the page', () => {
  it('finds the tables it is meant to be guarding', () => {
    // Guards the guard: a rename of the class, or a refactor that moves every
    // table behind a shared component, would otherwise leave this file
    // asserting nothing at all and still passing.
    expect(tableSites().length).toBeGreaterThan(0)
  })

  it('wraps every one of them in .table-scroll', () => {
    const unwrapped = tableSites()
      .filter((site) => site.previous !== '<div className="table-scroll">')
      .map((site) => `${site.file}:${site.line} (preceded by ${site.previous || 'blank line'})`)

    expect(unwrapped).toEqual([])
  })

  it('keeps .table-scroll a horizontal scroll container', () => {
    // The wrapper is only load-bearing while this rule exists. Deleting it
    // would leave all seventeen wrappers in place and the pages broken again.
    const theme = readFileSync(join(SRC, 'theme.css'), 'utf8')
    expect(theme).toMatch(/\.table-scroll\s*\{[^}]*overflow-x:\s*auto/)
  })

  it('keeps .table-scroll a containing block for its .sr-only spans', () => {
    // Not cosmetic, and the reason this case is separate: `.sr-only` is
    // absolutely positioned, so without `position` on the wrapper it resolves
    // against the initial containing block, escapes the clipping, and widens
    // the document on its own — with the table itself scrolling perfectly.
    // Dropping this declaration reintroduces the original bug in a form where
    // every table still looks correct.
    const theme = readFileSync(join(SRC, 'theme.css'), 'utf8')
    expect(theme).toMatch(/\.table-scroll\s*\{[^}]*position:\s*relative/)
  })
})
