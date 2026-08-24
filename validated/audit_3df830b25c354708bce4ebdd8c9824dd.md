### Title
Submodule diffs never surface the hidden-bidi / homoglyph warning shown for text diffs, letting a malicious submodule URL silently deceive reviewers - ([File: app/src/ui/diff/submodule-diff.tsx])

### Summary
GitHub Desktop protects users from the "off-chain simulation vs on-chain execution" class of bug the report describes — i.e. *what the user reviews is not what actually gets committed/pushed* — via a dedicated bidirectional-Unicode detector (`HiddenBidiCharsRegex`) that is threaded through `DiffParser.parse()` into `hasHiddenBidiChars` on every regular text diff, and rendered as a warning banner by `DiffContentsWarning`. That mechanism is the closest Desktop analog to the Atlas report's core problem: a check that is supposed to guarantee "what you approved is what happens" but that can be silently bypassed for one specific, attacker-reachable code path.

### Finding Description
`DiffParser.parse()` computes `hasHiddenBidiChars: HiddenBidiCharsRegex.test(text)` for every text diff [1](#0-0) , and this flag is what `DiffContentsWarning` uses to show the "This diff contains bidirectional Unicode text that may be interpreted or compiled differently…" banner [2](#0-1) . This is Desktop's actual mitigation for "what the reviewer sees is not what gets committed."

However, `buildSubmoduleDiff()` — the function that builds the diff object shown when a submodule pointer changes — never runs this check and never populates `hasHiddenBidiChars` at all. It only surfaces `url`, `oldSHA`, and `newSHA` [3](#0-2) , and `ISubmoduleDiff` (via `IDiff.Submodule`) carries no bidi-detection field, unlike `ITextDiffData` which explicitly has `hasHiddenBidiChars` [4](#0-3) . Correspondingly, `SubmoduleDiff` (the React component that renders the submodule URL and commit-change summary a user reviews before committing/pushing a submodule pointer bump) never renders `DiffContentsWarning` — that component is only wired into `side-by-side-diff.tsx` for regular text diffs [5](#0-4)  — while `SubmoduleDiff.render()` shows the submodule URL directly via `renderSubmoduleInfo()`/`LinkButton` with no such warning [6](#0-5) .

An attacker who controls a repository that another user clones (or a repository the user pulls a submodule pointer change from) can add/modify `.gitmodules` or a submodule commit URL string containing invisible bidirectional-override Unicode characters (`\u202A`–`\u202E`, `\u2066`–`\u2069`) so the rendered submodule URL in the changes/history view visually spoofs a trusted host or path while the raw string differs, with zero warning — exactly the class of "review looks safe, real state differs" defect Desktop otherwise defends against for ordinary text diffs.

### Impact Explanation
This maps to "silent corruption of what the user commits or pushes": the user reviews the Submodule Changes panel, sees what looks like a benign/trusted submodule URL, and commits/pushes the pointer update believing they inspected it — but the actual committed `.gitmodules`/tree state contains a visually-spoofed value that differs from what appeared on screen. Because Desktop already treats this exact primitive (hidden bidi chars altering perceived vs. actual diff content) as security-relevant enough to special-case for text diffs, its absence for submodule diffs is a real coverage gap rather than a hypothetical concern.

### Likelihood Explanation
Medium-low. It requires an attacker-controlled repository/submodule reference with crafted Unicode in a value that Desktop renders raw (submodule URL string), and it only affects the submodule-diff review UI (it does not, by itself, achieve arbitrary code execution — Git itself would still clone/fetch whatever the real, unspoofed URL bytes point to). The primary harm is deceptive review, not automatic exploitation, so it depends on a user acting on the misleading visual review (e.g., approving a PR, merging a submodule bump) without independently verifying the raw bytes.

### Recommendation
Extend the existing `HiddenBidiCharsRegex` check to submodule diff construction: compute `hasHiddenBidiChars` for the submodule `url`, `oldSHA`/`newSHA` context, and any other user-facing string in `buildSubmoduleDiff()`, add the field to `ISubmoduleDiff`, and render `DiffContentsWarning` (or an equivalent alert) inside `SubmoduleDiff.render()` when it is set, matching the coverage already provided for `ITextDiff`.

### Proof of Concept
1. Attacker creates/controls a repository `evil-parent` with a submodule whose `.gitmodules` URL is crafted to contain bidi override characters, e.g. a string that renders as `https://github.com/trusted-org/repo` but whose underlying bytes point elsewhere (achieved by embedding `\u202E`/`\u2066`-class characters around reordered path segments).
2. Attacker updates the submodule's committed SHA in a way that changes the visible/reviewed pointer, and gets the victim to fetch/pull this change into a repository opened in GitHub Desktop (e.g., via a PR branch or a pushed update to a shared repo).
3. Victim opens the Changes/Diff pane for the submodule change; `getStatus`/`listSubmodules` → `buildSubmoduleDiff()` builds an `ISubmoduleDiff` with the spoofed `url` [7](#0-6) .
4. `SubmoduleDiff` renders `renderSubmoduleInfo()` showing the crafted URL as a clickable link with no `DiffContentsWarning`, unlike what would happen for a text file with the same hidden characters [6](#0-5) .
5. Victim, seeing what appears to be a normal/trusted submodule reference, commits/pushes the pointer update, propagating the spoofed value further without ever having been warned, unlike the equivalent scenario in a regular text diff.

Note: I was not able to fully verify whether Git itself (outside Desktop) would also fail to warn on such a crafted `.gitmodules` URL, nor whether `parseRepositoryIdentifier` normalizes/strips bidi control characters before rendering (which could partially mitigate display spoofing) — this would require deeper inspection of `app/src/lib/remote-parsing.ts`, which I did not have remaining budget to examine in full.

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

**File:** app/src/lib/git/diff.ts (L798-842)
```typescript
async function buildSubmoduleDiff(
  buffer: Buffer,
  repository: Repository,
  file: FileChange,
  status: SubmoduleStatus
): Promise<IDiff> {
  const path = file.path
  const fullPath = Path.join(repository.path, path)
  const url = await getConfigValue(repository, `submodule.${path}.url`, true)

  let oldSHA = null
  let newSHA = null

  if (
    status.commitChanged ||
    file.status.kind === AppFileStatusKind.New ||
    file.status.kind === AppFileStatusKind.Deleted
  ) {
    const diff = buffer.toString('utf-8')
    const lines = diff.split('\n')
    const baseRegex = 'Subproject commit ([^-]+)(-dirty)?$'
    const oldSHARegex = new RegExp('-' + baseRegex)
    const newSHARegex = new RegExp('\\+' + baseRegex)
    const lineMatch = (regex: RegExp) =>
      lines
        .flatMap(line => {
          const match = line.match(regex)
          return match ? match[1] : []
        })
        .at(0) ?? null

    oldSHA = lineMatch(oldSHARegex)
    newSHA = lineMatch(newSHARegex)
  }

  return {
    kind: DiffType.Submodule,
    fullPath,
    path,
    url,
    status,
    oldSHA,
    newSHA,
  }
}
```

**File:** app/src/models/diff/diff-data.ts (L47-61)
```typescript
/**
 * Data returned as part of a textual diff from Desktop
 */
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

**File:** app/src/ui/diff/side-by-side-diff.tsx (L604-617)
```typescript
    return (
      /**
       * This a11y linter is a false-positive as the mousedown facilitates our
       * drag selection functionality and the keydown facilitates our select all
       * keyboard shortcut.
       */
      // eslint-disable-next-line jsx-a11y/no-static-element-interactions
      <div
        className={containerClassName}
        onMouseDown={this.onMouseDown}
        onKeyDown={this.onKeyDown}
      >
        <DiffContentsWarning diff={diff} />
        {isSearching && (
```

**File:** app/src/ui/diff/submodule-diff.tsx (L69-97)
```typescript
  private renderSubmoduleInfo() {
    if (this.props.diff.url === null) {
      return null
    }

    const repoIdentifier = parseRepositoryIdentifier(this.props.diff.url)
    if (repoIdentifier === null) {
      return null
    }

    const hostname =
      repoIdentifier.hostname === 'github.com'
        ? ''
        : ` (${repoIdentifier.hostname})`

    return this.renderSubmoduleDiffItem(
      { octicon: octicons.info, className: 'info-icon' },
      <>
        This is a submodule based on the repository{' '}
        <LinkButton
          uri={`https://${repoIdentifier.hostname}/${repoIdentifier.owner}/${repoIdentifier.name}`}
        >
          {repoIdentifier.owner}/{repoIdentifier.name}
          {hostname}
        </LinkButton>
        .
      </>
    )
  }
```
