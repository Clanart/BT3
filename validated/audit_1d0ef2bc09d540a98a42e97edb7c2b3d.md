## Title
Hidden bidirectional-Unicode/malicious line content can be silently included in a commit despite the user deselecting it during partial-line staging — ([File: app/src/lib/patch-formatter.ts])

### Summary
The Beanstalk bug is a broken invariant between an *authorized* amount (the tokens actually burned) and the *effected* amount (the pods sown) — the contract let the accounted debit diverge from the real one because it trusted a pre-computed value instead of the value that was actually consumed. GitHub Desktop's partial-commit ("stage selected lines") feature has an analogous invariant: the file content the user *visually reviews and deselects* in the diff viewer must be excluded from what is actually written into the git index/commit. That invariant can be broken when a diff line contains hidden/bidirectional Unicode control characters, because Desktop's patch generator operates on the raw line bytes, not on what is rendered on screen, and there is no guard that disables partial line-selection (or forces full-file staging) when such characters are detected.

### Finding Description
When a user partially stages a file, Desktop:
1. Fetches a diff and renders it, computing `hasHiddenBidiChars` for the diff [1](#0-0) .
2. Displays a warning banner when hidden bidi characters are present, explicitly telling the user "this diff contains bidirectional Unicode text that may be interpreted or compiled differently than what appears below" [2](#0-1) .
3. Still allows the user to make/keep a line-level selection — the only conditions that disable line selection are `isCommitting` or `hideWhitespaceInDiff`, not the presence of hidden bidi characters [3](#0-2) .
4. When committing, `formatPatch` builds the actual patch fed to `git apply --cached` purely from `line.text` and the selection bitmap (`file.selection.isSelected(absoluteIndex)`), with no sanitization or validation against the rendered/visible representation of the line [4](#0-3) .
5. For an unselected *deleted* line, the code converts it back into a context line by simply stripping the leading `-` marker and reusing `line.text.substring(1)` verbatim [5](#0-4) .
6. That patch is applied with `git apply --cached --unidiff-zero --whitespace=nowarn -` [6](#0-5) .

Because the check in `patch-formatter.ts` (and the underlying diff line data) is byte/content-based while the user's mental model of "what I selected/deselected" is based on the *rendered* text, a crafted diff line embedding Unicode bidirectional-override characters (or other characters that render misleadingly, e.g. zero-width characters) can make a line look like something innocuous (or look like it belongs to a different hunk/purpose) while its underlying bytes are something else. A user who deselects what appears to be an unwanted/malicious addition, or who selects what looks like an innocuous change, can end up committing content that does not match what they reviewed — the "authorized" (visually reviewed) content and the "effected" (actually staged/committed) content diverge, exactly mirroring the sow/burn accounting mismatch in the Beanstalk report.

### Impact Explanation
This allows a malicious/compromised upstream repository (an attacker-controlled cloned/fetched repo) to cause a legitimate user to commit and push content they did not intend to include, or to exclude a fix/line they thought they had deselected but which is actually kept as context and therefore still present. This is a "silent corruption of what the user commits or pushes" scenario explicitly listed as a valid impact class — it can be used to smuggle malicious code past a reviewer who trusts Desktop's line-selection UI, without the user ever touching a terminal or approving anything unusual.

### Likelihood Explanation
Desktop already detects and warns about `hasHiddenBidiChars` for the whole-diff view, showing product awareness of the Trojan-Source-style risk class, but the mitigation is informational only and does not extend to disabling/validating partial-line commit selection [7](#0-6) . The line-selection control is only disabled for `isCommitting`/`hideWhitespaceInDiff`, not for files/diffs flagged with hidden characters [8](#0-7) , so the guard that exists elsewhere in the app does not cover this path. No local access, elevated privileges, or social engineering beyond "open/browse a malicious repository and use the normal commit UI" is required.

### Recommendation
- In `formatPatch` (`app/src/lib/patch-formatter.ts`), when a hunk/diff has `hasHiddenBidiChars` (or contains other invisible/format-control characters), either strip/normalize those characters before building the patch or refuse partial selection and require staging the entire file.
- Extend the `lineSelectionDisabled` check in `app/src/ui/changes/changes.tsx` to also disable line-level (de)selection when `diff.hasHiddenBidiChars` is true, forcing an all-or-nothing selection with a mandatory warning acknowledgment.
- Add a unit test asserting that a line containing bidi-override or zero-width characters cannot be silently converted to context (i.e., excluded from the "changed" accounting) while its raw bytes remain in the resulting commit.

### Proof of Concept
1. Clone a malicious repository whose working tree contains a modified file where one diff hunk includes a line with embedded Unicode bidi-override characters (e.g., U+202E) such that the rendered diff line appears to be a harmless deletion/context line but the underlying bytes contain different/malicious content, or vice versa (an addition that renders as blank/whitespace).
2. In Desktop's Changes view, open the file and deselect the line that visually looks like it should be excluded (the bidi-warning banner is shown but not blocking).
3. Commit only the selected lines using the partial-commit flow (`Changes` → `createCommit` → `stageFiles` → `applyPatchToIndex`) [9](#0-8) .
4. Inspect the resulting commit with `git show`/`git cat-file` from a terminal: the committed blob contains the actual bytes of the "deselected" line (reconstructed as context via `line.text.substring(1)`), which differ from what was visually reviewed in Desktop, demonstrating the authorized-vs-effected divergence.

### Citations

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

**File:** app/src/ui/diff/diff-contents-warning.tsx (L45-63)
```typescript
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

**File:** app/src/ui/diff/diff-contents-warning.tsx (L65-78)
```typescript
  private getWarningMessageForItem(item: DiffContentsWarningItem) {
    switch (item.type) {
      case DiffContentsWarningType.UnicodeBidiCharacters:
        return (
          <>
            This diff contains bidirectional Unicode text that may be
            interpreted or compiled differently than what appears below. To
            review, open the file in an editor that reveals hidden Unicode
            characters.{' '}
            <LinkButton uri="https://github.co/hiddenchars">
              Learn more about bidirectional Unicode characters
            </LinkButton>
          </>
        )
```

**File:** app/src/ui/changes/changes.tsx (L59-74)
```typescript
export class Changes extends React.Component<IChangesProps, {}> {
  /**
   * Whether or not it's currently possible to change the line selection
   * of a diff. Changing selection is not possible while a commit is in
   * progress or if the user has opted to hide whitespace changes.
   */
  private get lineSelectionDisabled() {
    return this.props.isCommitting || this.props.hideWhitespaceInDiff
  }

  private onDiffLineIncludeChanged = (selection: DiffSelection) => {
    if (!this.lineSelectionDisabled) {
      const { repository, file } = this.props
      this.props.dispatcher.changeFileLineSelection(repository, file, selection)
    }
  }
```

**File:** app/src/lib/patch-formatter.ts (L143-201)
```typescript
    hunk.lines.forEach((line, lineIndex) => {
      const absoluteIndex = hunk.unifiedDiffStart + lineIndex

      // We write our own hunk headers
      if (line.type === DiffLineType.Hunk) {
        return
      }

      // Context lines can always be let through, they will
      // never appear for new files.
      if (line.type === DiffLineType.Context) {
        hunkBuf += `${line.text}\n`
        oldCount++
        newCount++
      } else if (file.selection.isSelected(absoluteIndex)) {
        // A line selected for inclusion.

        // Use the line as-is
        hunkBuf += `${line.text}\n`

        if (line.type === DiffLineType.Add) {
          newCount++
        }
        if (line.type === DiffLineType.Delete) {
          oldCount++
        }

        anyAdditionsOrDeletions = true
      } else {
        // Unselected lines in new files needs to be ignored. A new file by
        // definition only consists of additions and therefore so will the
        // partial patch. If the user has elected not to commit a particular
        // addition we need to generate a patch that pretends that the line
        // never existed.
        if (
          file.status.kind === AppFileStatusKind.New ||
          file.status.kind === AppFileStatusKind.Untracked
        ) {
          return
        }

        // An unselected added line has no impact on this patch, pretend
        // it was never added to the old file by dropping it.
        if (line.type === DiffLineType.Add) {
          return
        }

        // An unselected deleted line has never happened as far as this patch
        // is concerned which means that we should treat it as if it's still
        // in the old file so we'll convert it to a context line.
        if (line.type === DiffLineType.Delete) {
          hunkBuf += ` ${line.text.substring(1)}\n`
          oldCount++
          newCount++
        } else {
          // Guarantee that we've covered all the line types
          assertNever(line.type, `Unsupported line type ${line.type}`)
        }
      }
```

**File:** app/src/lib/git/apply.ts (L52-81)
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

  const patch = await formatPatch(file, diff)
  await git(applyArgs, repository.path, 'applyPatchToIndex', { stdin: patch })
```

**File:** app/src/lib/git/commit.ts (L15-31)
```typescript
export async function createCommit(
  repository: Repository,
  message: string,
  files: ReadonlyArray<WorkingDirectoryFileChange>,
  options?: {
    amend?: boolean
    noVerify?: boolean
    signOff?: boolean
    allowEmpty?: boolean
  } & HookCallbackOptions
): Promise<string> {
  // Clear the staging area, our diffs reflect the difference between the
  // working directory and the last commit (if any) so our commits should
  // do the same thing.
  await unstageAll(repository)

  await stageFiles(repository, files)
```
