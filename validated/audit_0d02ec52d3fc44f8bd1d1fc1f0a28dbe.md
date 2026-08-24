## Title
Hidden Unicode bidi-character warning is suppressed for large diffs, allowing a malicious repo file to cause users to silently commit/push content that renders differently than what they approved - (File: `app/src/ui/diff/index.tsx`, `app/src/ui/diff/side-by-side-diff.tsx`, `app/src/lib/git/diff.ts`)

### Summary
`DiffParser.parse` computes `hasHiddenBidiChars` for every text diff by testing the raw diff text against `HiddenBidiCharsRegex` [1](#0-0) , and this flag is meant to be surfaced to the user via `DiffContentsWarning`, which is only rendered from within `side-by-side-diff.tsx` [2](#0-1) . However, when a diff is classified as `DiffType.LargeText` (buffer/line-length thresholds hard-coded in `app/src/lib/git/diff.ts`) [3](#0-2) [4](#0-3) , `Diff.render` shows only the "too large to display" placeholder (`renderLargeTextDiff`) unless the user explicitly clicks "Show Diff" [5](#0-4) . Because the bidi-character warning lives inside the actual diff-rendering component that is skipped by default for large diffs, a file crafted by a repo owner/collaborator to trip the hard-coded "large diff" thresholds will never show the "This diff contains bidirectional Unicode text…" warning to the user unless they manually opt in to viewing the (possibly multi-megabyte) diff.

### Finding Description
The broken invariant is: *"Any file containing hidden bidi control characters must be flagged to the user before they stage/commit it."* This holds only when the diff is small enough to be auto-rendered. The classification thresholds (`MaxDiffBufferSize` = 70MB, `MaxReasonableDiffSize` ≈ 4.375MB, `MaxCharactersPerLine` = 5000) are fixed constants with no per-call override, analogous to the Yearn adapter's hard-coded `maxLoss` — the caller (Desktop) cannot adjust the threshold, and an attacker who controls repository content (a cloned/fetched repo) can deliberately trigger the "unsafe" branch of the code path (`DiffType.LargeText`) to suppress a safety check that only fires on the "safe" branch.

Concretely: `buildDiff` marks any file whose diff buffer/line length crosses those thresholds as `LargeText`, still carrying `hasHiddenBidiChars` in the data model [6](#0-5) , but the `Diff` component does not surface that flag until the user presses "Show Diff", at which point `renderLargeText` converts it back to a viewable `ITextDiff` [7](#0-6) . The warning banner itself is only wired up inside `side-by-side-diff.tsx`'s rendering path, which is never invoked for the collapsed `LargeText`/`Unrenderable` states. `applyPatchToIndex` (used for partial commits) explicitly *allows* `DiffType.LargeText` to be staged and committed with no additional check for `hasHiddenBidiChars` [8](#0-7) .

### Impact Explanation
A malicious or compromised collaborator can commit a source file containing Trojan-Source-style bidi control characters (`\u202A`–`\u202E`, `\u2066`–`\u2069`) alongside padding/long lines that push the diff over the hard-coded size threshold. When a victim pulls/fetches this branch and reviews their own subsequent change to that file (or even the initial file addition itself, if it's large), Desktop will not show the "bidirectional Unicode text" warning that normally alerts users their file may render or compile differently than displayed. The victim may then stage, commit, and push changes based on a visual representation that doesn't match the actual byte content — i.e., silent corruption of what the user believes they are committing/pushing, potentially reintroducing or hiding malicious logic that is later executed by the victim or downstream consumers of the pushed code.

### Likelihood Explanation
Requires only that the attacker control content in a repository the victim will fetch/clone and diff — no local access, no elevated privileges, no social engineering beyond normal collaboration. Triggering the `LargeText` classification is straightforward (a sufficiently large diff or a single line exceeding 5000 characters), and most users will not click "Show Diff" for files flagged as too-large-to-render before committing, since that's precisely the friction the placeholder is designed to avoid.

### Recommendation
Surface the `hasHiddenBidiChars` flag independently of whether the underlying diff content is rendered — e.g., render `DiffContentsWarning` (or an equivalent banner) for `DiffType.LargeText` and `DiffType.Unrenderable` states in `Diff.render`/`renderLargeTextDiff`/`renderUnrenderableDiff`, rather than only inside the fully-rendered text-diff view. Additionally, `applyPatchToIndex` should refuse or warn before allowing a partial/full commit of a file whose diff has `hasHiddenBidiChars === true`, regardless of size classification.

### Proof of Concept
1. Attacker adds/modifies a tracked file so that it contains a bidi-override character (e.g., U+202E) plus enough additional content/long lines that the diff exceeds `MaxReasonableDiffSize` (~4.375MB) or has a line longer than `MaxCharactersPerLine` (5000 chars), so `isBufferTooLarge`/`isDiffTooLarge` in `app/src/lib/git/diff.ts` classify it as `DiffType.LargeText`.
2. Attacker pushes this to a shared branch.
3. Victim fetches/pulls the branch in Desktop and views the Changes list; the file's diff panel shows only "The diff is too large to be displayed by default" (`renderLargeTextDiff`) — no bidi warning is shown because `DiffContentsWarning` never mounts.
4. Victim, trusting the default checked/staged state, commits and pushes without ever seeing "This diff contains bidirectional Unicode text that may be interpreted or compiled differently than what appears below."

### Citations

**File:** app/src/lib/diff-parser.ts (L449-456)
```typescript
      return {
        header,
        contents,
        hunks,
        isBinary: headerInfo.isBinary,
        maxLineNumber: getLargestLineNumber(hunks),
        hasHiddenBidiChars: HiddenBidiCharsRegex.test(text),
      }
```

**File:** app/src/ui/diff/diff-contents-warning.tsx (L45-53)
```typescript
  private getTextDiffWarningItems(): ReadonlyArray<DiffContentsWarningItem> {
    const items = new Array<DiffContentsWarningItem>()
    const { diff } = this.props

    if (diff.hasHiddenBidiChars) {
      items.push({
        type: DiffContentsWarningType.UnicodeBidiCharacters,
      })
    }
```

**File:** app/src/lib/git/diff.ts (L42-61)
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
```

**File:** app/src/lib/git/diff.ts (L861-882)
```typescript
  if (!isValidBuffer(buffer)) {
    // the buffer's diff is too large to be renderable in the UI
    return { kind: DiffType.Unrenderable }
  }

  const diff = diffFromRawDiffOutput(buffer)

  if (isBufferTooLarge(buffer) || isDiffTooLarge(diff)) {
    // we don't want to render by default
    // but we keep it as an option by
    // passing in text and hunks
    const largeTextDiff: ILargeTextDiff = {
      kind: DiffType.LargeText,
      text: diff.contents,
      hunks: diff.hunks,
      lineEndingsChange,
      maxLineNumber: diff.maxLineNumber,
      hasHiddenBidiChars: diff.hasHiddenBidiChars,
    }

    return largeTextDiff
  }
```

**File:** app/src/ui/diff/index.tsx (L124-146)
```typescript
  public render() {
    const diff = this.props.diff

    switch (diff.kind) {
      case DiffType.Text:
        return this.renderText(diff)
      case DiffType.Binary:
        return this.renderBinaryFile()
      case DiffType.Submodule:
        return this.renderSubmoduleDiff(diff)
      case DiffType.Image:
        return this.renderImage(diff)
      case DiffType.LargeText: {
        return this.state.forceShowLargeDiff
          ? this.renderLargeText(diff)
          : this.renderLargeTextDiff()
      }
      case DiffType.Unrenderable:
        return this.renderUnrenderableDiff()
      default:
        return assertNever(diff, `Unsupported diff type: ${diff}`)
    }
  }
```

**File:** app/src/ui/diff/index.tsx (L205-217)
```typescript
  private renderLargeText(diff: ILargeTextDiff) {
    // guaranteed to be set since this function won't be called if text or hunks are null
    const textDiff: ITextDiff = {
      text: diff.text,
      hunks: diff.hunks,
      kind: DiffType.Text,
      lineEndingsChange: diff.lineEndingsChange,
      maxLineNumber: diff.maxLineNumber,
      hasHiddenBidiChars: diff.hasHiddenBidiChars,
    }

    return this.renderTextDiff(textDiff)
  }
```

**File:** app/src/lib/git/apply.ts (L52-78)
```typescript
  const applyArgs: string[] = [
    'apply',
    '--cached',
    '--unidiff-zero',
    '--whitespace=nowarn',
    '-',
  ]

  const diff = await getWorkingDirectoryDiff(repository, file)

  if (diff.kind !== DiffType.Text && diff.kind !== DiffType.LargeText) {
    const { kind } = diff
    switch (diff.kind) {
      case DiffType.Binary:
      case DiffType.Submodule:
      case DiffType.Image:
        throw new Error(
          `Can't create partial commit in binary file: ${file.path}`
        )
      case DiffType.Unrenderable:
        throw new Error(
          `File diff is too large to generate a partial commit: ${file.path}`
        )
      default:
        assertNever(diff, `Unknown diff kind: ${kind}`)
    }
  }
```
