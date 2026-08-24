### Title
Unicode "Trojan Source" bidi-character detection is silently bypassed for large diffs, letting hidden malicious content be committed/pushed without warning - ([File: app/src/lib/git/diff.ts])

### Summary
GitHub Desktop specifically parses diffs for invisible Unicode bidirectional-override characters (`HiddenBidiCharsRegex`) so it can warn users their file may render differently than it executes/compiles ("Trojan Source" style attacks). This is the closest structural analog to the wallet report's "user can't see all critical parameters before approving an action" bug class: instead of `data`/`gas`/`nonce` fields hidden from a transaction approval, here the diff content itself — including a security-relevant hidden-character warning — can be entirely withheld from the user before they stage, commit, and push it.

### Finding Description
`buildDiff()` first checks `isValidBuffer(buffer)` — a hard limit of `MaxDiffBufferSize = 70e6` bytes [1](#0-0) . If the buffer exceeds this, the function returns immediately with `{ kind: DiffType.Unrenderable }` [2](#0-1)  — **before** `diffFromRawDiffOutput`/`DiffParser.parse()` is ever invoked. The `hasHiddenBidiChars` computation only happens inside `DiffParser.parse()` via `HiddenBidiCharsRegex.test(text)` [3](#0-2) , so for an oversized diff this check never runs at all.

Confirming the data model: `IUnrenderableDiff` carries **no fields whatsoever** — not even a `hasHiddenBidiChars` flag — unlike `ITextDiff`/`ILargeTextDiff` which both carry it [4](#0-3) [5](#0-4) . The renderer for this state, `renderUnrenderableDiff()`, shows only "The diff is too large to be displayed" with no content and no warning banner [6](#0-5) . The `DiffContentsWarning` component — the only place `hasHiddenBidiChars` is surfaced to the user — is only ever rendered for `ITextDiff` in the side-by-side/unified diff view [7](#0-6) [8](#0-7) , so a file that never reaches `ITextDiff`/`ILargeTextDiff` state can never trigger this warning.

Critically, whether the *diff panel* can render a file is independent of whether the file can be **staged and committed**: file inclusion in a commit is controlled by the checkbox in the changes list, not by having actually viewed/expanded the diff. A user can therefore select "include all changes," write a commit message, and push — never having any indication that one of the files contains invisible bidirectional-override or homoglyph characters that visually hide malicious code, because the size guard silently disabled the only mechanism designed to detect that.

### Impact Explanation
An attacker who controls a cloned/fetched repository (e.g., a shared branch, a PR checked out locally, or a large generated/vendored file) can pad a file past the 70MB `MaxDiffBufferSize` threshold while embedding Trojan-Source-style bidi override characters that reorder how code visually appears versus how it executes. Because the size check short-circuits before the bidi-detection regex runs, Desktop gives **zero warning** — not even the generic "diff too large" bidi caveat that smaller `LargeText` diffs eventually surface once expanded. The victim reviewer stages, commits, and pushes content whose real semantics differ from what any tooling in Desktop indicated, i.e., silent corruption of what the user commits/pushes — directly matching the accepted impact category.

### Likelihood Explanation
Medium. It requires an attacker to control repository content the victim will pull/checkout (a normal, unprivileged git workflow — no local access, malware, or leaked credentials needed) and to craft or include a single file exceeding 70MB, which is a large but realistic size for build artifacts, data files, or intentionally bloated payloads in a compromised dependency/vendor drop. No user action beyond the normal "stage → commit → push" flow is required, and no exotic multi-step social engineering is needed.

### Recommendation
Compute the `hasHiddenBidiChars` check independently of the size-based rendering decision — i.e., always run `HiddenBidiCharsRegex` against the raw buffer/text even when returning `DiffType.Unrenderable`, and add a `hasHiddenBidiChars` (or a generic "unable to fully scan for hidden content") flag to `IUnrenderableDiff` so the UI can surface a warning banner even when the diff body itself cannot be rendered. Consider blocking or requiring explicit confirmation before staging files that fail this scan.

### Proof of Concept
1. Create a text file in a test repository whose content is padded to exceed 70,000,000 bytes (`MaxDiffBufferSize`) and includes a Unicode bidi override sequence (e.g., `U+202E`) surrounding a malicious code fragment so it displays differently than it executes.
2. Commit this file to a remote branch/repo that the victim will fetch/clone.
3. In GitHub Desktop, have the victim check out or fetch this branch and open the Changes tab; select the file and view its diff — Desktop shows `renderUnrenderableDiff()` ("The diff is too large to be displayed"), with no bidi-character warning.
4. The victim selects the file for inclusion (checkbox) and commits/pushes it. At no point did Desktop indicate the presence of the invisible bidi override sequence, whereas the same content in a smaller file would (eventually, upon expansion) trigger `DiffContentsWarning`'s `UnicodeBidiCharacters` banner [9](#0-8) .

### Citations

**File:** app/src/lib/git/diff.ts (L42-76)
```typescript
/**
 * V8 has a limit on the size of string it can create (~256MB), and unless we want to
 * trigger an unhandled exception we need to do the encoding conversion by hand.
 *
 * This is a hard limit on how big a buffer can be and still be converted into
 * a string.
 */
const MaxDiffBufferSize = 70e6 // 70MB in decimal

/**
 * Where `MaxDiffBufferSize` is a hard limit, this is a suggested limit. Diffs
 * bigger than this _could_ be displayed but it might cause some slowness.
 */
const MaxReasonableDiffSize = MaxDiffBufferSize / 16 // ~4.375MB in decimal

/**
 * The longest line length we should try to display. If a diff has a line longer
 * than this, we probably shouldn't attempt it
 */
const MaxCharactersPerLine = 5000

/**
 * Utility function to check whether parsing this buffer is going to cause
 * issues at runtime.
 *
 * @param buffer A buffer of binary text from a spawned process
 */
function isValidBuffer(buffer: Buffer) {
  return buffer.length <= MaxDiffBufferSize
}

/** Is the buffer too large for us to reasonably represent? */
function isBufferTooLarge(buffer: Buffer) {
  return buffer.length >= MaxReasonableDiffSize
}
```

**File:** app/src/lib/git/diff.ts (L861-864)
```typescript
  if (!isValidBuffer(buffer)) {
    // the buffer's diff is too large to be renderable in the UI
    return { kind: DiffType.Unrenderable }
  }
```

**File:** app/src/lib/diff-parser.ts (L392-459)
```typescript
  /**
   * Parse a well-formed unified diff into hunks and lines.
   *
   * @param text A unified diff produced by git diff, git log --patch
   *             or any other git plumbing command that produces unified
   *             diffs.
   */
  public parse(text: string): IRawDiff {
    this.text = text

    try {
      const headerInfo = this.parseDiffHeader()

      const headerEnd = this.le
      const header = this.text.substring(0, headerEnd)

      // empty diff
      if (!headerInfo) {
        return {
          header,
          contents: '',
          hunks: [],
          isBinary: false,
          maxLineNumber: 0,
          hasHiddenBidiChars: false,
        }
      }

      if (headerInfo.isBinary) {
        return {
          header,
          contents: '',
          hunks: [],
          isBinary: true,
          maxLineNumber: 0,
          hasHiddenBidiChars: false,
        }
      }

      const hunks = new Array<DiffHunk>()
      let linesConsumed = 0
      let previousHunk: DiffHunk | null = null

      do {
        const hunk = this.parseHunk(linesConsumed, hunks.length, previousHunk)
        hunks.push(hunk)
        previousHunk = hunk
        linesConsumed += hunk.lines.length
      } while (this.peek())

      const contents = this.text
        .substring(headerEnd + 1, this.le)
        // Note that this simply returns a reference to the
        // substring if no match is found, it does not create
        // a new string instance.
        .replace(/\n\\ No newline at end of file/g, '')

      return {
        header,
        contents,
        hunks,
        isBinary: headerInfo.isBinary,
        maxLineNumber: getLargestLineNumber(hunks),
        hasHiddenBidiChars: HiddenBidiCharsRegex.test(text),
      }
    } finally {
      this.reset()
    }
```

**File:** app/src/models/diff/diff-data.ts (L50-61)
```typescript
interface ITextDiffData {
  /** The unified text diff - including headers and context */
  readonly text: string
  /** The diff contents organized by hunk - how the git CLI outputs to the caller */
  readonly hunks: ReadonlyArray<DiffHunk>
  /** A warning from Git that the line endings have changed in this file and will affect the commit */
  readonly lineEndingsChange?: LineEndingsChange
  /** The largest line number in the diff  */
  readonly maxLineNumber: number
  /** Whether or not the diff has invisible bidi characters */
  readonly hasHiddenBidiChars: boolean
}
```

**File:** app/src/models/diff/diff-data.ts (L113-119)
```typescript
export interface ILargeTextDiff extends ITextDiffData {
  readonly kind: DiffType.LargeText
}

export interface IUnrenderableDiff {
  readonly kind: DiffType.Unrenderable
}
```

**File:** app/src/ui/diff/index.tsx (L196-203)
```typescript
  private renderUnrenderableDiff() {
    return (
      <div className="panel empty large-diff">
        <img src={NoDiffImage} alt="" />
        <p>The diff is too large to be displayed.</p>
      </div>
    )
  }
```

**File:** app/src/ui/diff/diff-contents-warning.tsx (L1-63)
```typescript
import React from 'react'
import { Octicon } from '../octicons'
import * as octicons from '../octicons/octicons.generated'
import { LinkButton } from '../lib/link-button'
import { ITextDiff, LineEndingsChange } from '../../models/diff'

enum DiffContentsWarningType {
  UnicodeBidiCharacters,
  LineEndingsChange,
}

type DiffContentsWarningItem =
  | {
      readonly type: DiffContentsWarningType.UnicodeBidiCharacters
    }
  | {
      readonly type: DiffContentsWarningType.LineEndingsChange
      readonly lineEndingsChange: LineEndingsChange
    }

interface IDiffContentsWarningProps {
  readonly diff: ITextDiff
}

export class DiffContentsWarning extends React.Component<IDiffContentsWarningProps> {
  public render() {
    const items = this.getTextDiffWarningItems()

    if (items.length === 0) {
      return null
    }

    return (
      <div className="diff-contents-warning-container">
        {items.map((item, i) => (
          <div className="diff-contents-warning" key={i}>
            <Octicon symbol={octicons.alert} />
            {this.getWarningMessageForItem(item)}
          </div>
        ))}
      </div>
    )
  }

  private getTextDiffWarningItems(): ReadonlyArray<DiffContentsWarningItem> {
    const items = new Array<DiffContentsWarningItem>()
    const { diff } = this.props

    if (diff.hasHiddenBidiChars) {
      items.push({
        type: DiffContentsWarningType.UnicodeBidiCharacters,
      })
    }

    if (diff.lineEndingsChange) {
      items.push({
        type: DiffContentsWarningType.LineEndingsChange,
        lineEndingsChange: diff.lineEndingsChange,
      })
    }

    return items
  }
```

**File:** app/src/ui/diff/side-by-side-diff.tsx (L611-617)
```typescript
      <div
        className={containerClassName}
        onMouseDown={this.onMouseDown}
        onKeyDown={this.onKeyDown}
      >
        <DiffContentsWarning diff={diff} />
        {isSearching && (
```
