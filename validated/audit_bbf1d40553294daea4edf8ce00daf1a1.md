Based on the code evidence gathered, `DiffContentsWarning` is rendered from within `SideBySideDiff` (the actual expanded diff view), i.e. only reachable via `renderTextDiff`/`renderLargeText`, and never from `renderLargeTextDiff()` (the collapsed placeholder) or `renderUnrenderableDiff()`. This confirms the analog below.

### Title
Bidirectional-Unicode ("Trojan Source") warning is silently suppressed for diffs that exceed the size/line-length circuit breaker, allowing hidden malicious characters to be committed/pushed unreviewed - ([File: app/src/lib/git/diff.ts])

### Summary
GitHub Desktop's diff pipeline has a size-based "circuit breaker" (`isBufferTooLarge` / `isDiffTooLarge`) analogous to a Chainlink aggregator's `minAnswer`/`maxAnswer` clamp. When the underlying data (a diff) crosses that threshold, the pipeline doesn't fail safely — it returns a `LargeText`/collapsed view that hides the diff by default. Crucially, the one safety signal that exists for this exact attack class — the bidirectional-Unicode ("hidden chars") warning banner — is only ever rendered inside the fully-expanded diff view, which the user must explicitly opt into via "Show Diff". An attacker who crafts a file with both hidden bidi characters and one very long line (or a diff/file large enough to trip the size limits) can force Desktop into the collapsed "diff too large" state, suppressing the only UI signal that would have alerted the user to Trojan-Source-style hidden characters, while the diff/commit action is still fully available.

### Finding Description
`isValidBuffer`, `isBufferTooLarge`, and `isDiffTooLarge` gate whether a diff is rendered as `Text`, `LargeText`, or `Unrenderable` [1](#0-0) . `isDiffTooLarge` specifically trips whenever any single line exceeds `MaxCharactersPerLine` (5000 chars) [2](#0-1) . In `buildDiff`, once the buffer/diff is classified as too large, the function returns an `ILargeTextDiff` that still *carries* `hasHiddenBidiChars` (computed by `DiffParser.parse`, which unconditionally runs `HiddenBidiCharsRegex.test(text)` on the whole diff text) [3](#0-2) [4](#0-3) .

However, in the UI, the `Diff` component defaults to `renderLargeTextDiff()` — a placeholder panel that only says "The diff is too large to be displayed by default" with a "Show Diff" button, and does not render `DiffContentsWarning` [5](#0-4) . The `DiffContentsWarning` component — the only UI element that surfaces `hasHiddenBidiChars` to the user — is wired up inside the fully expanded diff render path (`SideBySideDiff`), which is only reached after the user clicks "Show Diff" and `forceShowLargeDiff` becomes true [6](#0-5) [7](#0-6) .

The broken invariant: the `hasHiddenBidiChars` value is computed correctly (like a legitimate oracle price), but the consumer (the UI) only checks/displays it once the value has already passed through the "large diff" clamp — exactly mirroring the Chainlink bug where a valid signal exists internally, but the layer responsible for warning/reverting doesn't consult it when the value has been clamped/collapsed. Users routinely stage and commit files without expanding "too large" diffs, especially for files that were already large before the malicious edit (e.g. minified JS, generated files, vendored code) — a realistic scenario since attackers control the content of files in a cloned/fetched repository.

### Impact Explanation
An attacker who can get a victim to pull/fetch a branch or apply a patch (e.g., via a PR checkout, a shared branch, or a supply-chain dependency committed in-repo) can hide bidirectional-Unicode "Trojan Source" characters (e.g., in a large or long-line file) so that Desktop collapses the diff and never shows the "This diff contains bidirectional Unicode text…" warning unless the user manually clicks through. The victim may then commit, merge, or push that content without ever being warned, propagating hidden/reordered source text that renders differently than it executes — a class of vulnerability GitHub explicitly added this warning banner to mitigate (see `changelog.json` entry "[Improved] Warn users when files contain bidirectional Unicode text - #13343"). This corresponds to "silent corruption of what the user commits or pushes," since the safety mechanism that would let the user object is bypassed by design in the collapsed state.

### Likelihood Explanation
Moderate-to-high: crossing the size/line-length threshold is trivial for an attacker who controls file content (a single line over 5000 characters, or padding a file to exceed `MaxReasonableDiffSize`), and large/minified/generated diffs are common enough in real repos that users are conditioned to skip expanding them. No special privileges, local access, or social engineering beyond a normal fetch/pull/PR checkout are required.

### Recommendation
Compute and surface the bidi-warning independent of the diff-size classification: render `DiffContentsWarning` (or an equivalent banner) even in the collapsed `LargeText`/`Unrenderable` states whenever `diff.hasHiddenBidiChars` is true, so the user is warned before ever deciding whether to expand or commit the diff. Consider also failing closed (blocking commit/stage without explicit acknowledgment) rather than silently allowing the action when hidden bidi characters are detected in a diff that is not fully rendered.

### Proof of Concept
1. Create/checkout a branch where a tracked file contains standard bidirectional-override Unicode characters (e.g. U+202E) intermixed with source code, following the "Trojan Source" pattern.
2. Ensure the modified line in the diff exceeds `MaxCharactersPerLine` (5000 characters) — e.g., by appending a long trailing comment/whitespace so the diff line trips `isDiffTooLarge`, or make the overall diff exceed `MaxReasonableDiffSize` (~4.375MB).
3. Open the repository in GitHub Desktop and view Changes/History for this file: the `Diff` component renders `renderLargeTextDiff()`, showing only "The diff is too large to be displayed by default" with no bidi warning, per [8](#0-7) .
4. Stage and commit (or push) the file without clicking "Show Diff" — the hidden bidi characters are committed/pushed with no warning ever shown to the user, whereas the same content in a normal-sized diff would have triggered `DiffContentsWarning` per [6](#0-5) .

### Citations

**File:** app/src/lib/git/diff.ts (L57-89)
```typescript
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

/** Is the diff too large for us to reasonably represent? */
function isDiffTooLarge(diff: IRawDiff) {
  for (const hunk of diff.hunks) {
    for (const line of hunk.lines) {
      if (line.text.length > MaxCharactersPerLine) {
        return true
      }
    }
  }

  return false
}
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

**File:** app/src/ui/diff/index.tsx (L115-203)
```typescript
export class Diff extends React.Component<IDiffProps, IDiffState> {
  public constructor(props: IDiffProps) {
    super(props)

    this.state = {
      forceShowLargeDiff: false,
    }
  }

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

  private renderImage(imageDiff: IImageDiff) {
    if (imageDiff.current && imageDiff.previous) {
      return (
        <ModifiedImageDiff
          onChangeDiffType={this.props.onChangeImageDiffType}
          diffType={this.props.imageDiffType}
          current={imageDiff.current}
          previous={imageDiff.previous}
        />
      )
    }

    if (
      imageDiff.current &&
      (this.props.file.status.kind === AppFileStatusKind.New ||
        this.props.file.status.kind === AppFileStatusKind.Untracked)
    ) {
      return <NewImageDiff current={imageDiff.current} />
    }

    if (
      imageDiff.previous &&
      this.props.file.status.kind === AppFileStatusKind.Deleted
    ) {
      return <DeletedImageDiff previous={imageDiff.previous} />
    }

    return null
  }

  private renderLargeTextDiff() {
    return (
      <div className="panel empty large-diff">
        <img src={NoDiffImage} className="blankslate-image" alt="" />
        <div className="description">
          <p>The diff is too large to be displayed by default.</p>
          <p>
            You can try to show it anyway, but performance may be negatively
            impacted.
          </p>
        </div>
        <Button onClick={this.showLargeDiff}>
          {__DARWIN__ ? 'Show Diff' : 'Show diff'}
        </Button>
      </div>
    )
  }

  private renderUnrenderableDiff() {
    return (
      <div className="panel empty large-diff">
        <img src={NoDiffImage} alt="" />
        <p>The diff is too large to be displayed.</p>
      </div>
    )
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
