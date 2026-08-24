## Title
Stale line-index diff selection can silently commit unreviewed content when the working-tree file changes between diff render and staging - ([File: app/src/lib/git/apply.ts])

## Summary
GitHub Desktop's partial-commit feature stores a user's line selection as a set of **positional indices** (`DiffSelection`), not as content-addressed identifiers. When the app actually stages a partial file (`applyPatchToIndex`), it re-runs `git diff` against the *current* working-tree file rather than reusing the diff the user visually reviewed. If the file on disk changes between the moment the user made their line selection and the moment the commit is executed, the old index-based selection is applied to a brand-new diff, and `git apply --cached` silently stages whatever lines now occupy those positions — not what the user actually looked at and checked.

## Finding Description
`DiffSelection` (`app/src/models/diff/diff-selection.ts`) tracks selection purely by line index: `isSelected(lineIndex)` and `withRangeSelection(from, length, selected)` operate on a `Set<number>` of "diverging lines," with no reference to the actual line content or diff hunk identity [1](#0-0) .

The UI computes this selection against a diff obtained once, when the file is selected in the Changes list, via `updateChangesWorkingDirectoryDiff` → `getWorkingDirectoryDiff` [2](#0-1) .

However, when the user commits, `createCommit` → `stageFiles` → `applyPatchToIndex` is invoked, and that function **independently re-fetches the diff from disk** right before formatting the patch:
```
const diff = await getWorkingDirectoryDiff(repository, file)
...
const patch = await formatPatch(file, diff)
await git(applyArgs, repository.path, 'applyPatchToIndex', { stdin: patch })
``` [3](#0-2) 

`formatPatch` then walks the **freshly-fetched** hunks and calls `file.selection.isSelected(absoluteIndex)` using the **stale** selection object created against the old diff [4](#0-3) . Since `isSelected` only knows line indices, not content, if the underlying file changed (different hunk boundaries, shifted or replaced lines) between the two `getWorkingDirectoryDiff` calls, the indices the user "selected" now point at different, unreviewed lines of the new diff. There's no re-validation step at commit time — the only reconciliation of selection state to a changed diff (`withSelectableLines`, in `app-store.ts` lines 3478-3497) happens for *display* refresh, not for the commit path in `apply.ts`.

This breaks the invariant that "the content shown to the user for review is the content that gets committed." Nothing in `applyPatchToIndex`, `stageFiles`, or `createCommit` checks that the diff used for staging matches the diff the selection was derived from.

## Impact Explanation
This allows **silent corruption of what the user commits/pushes** — one of the explicitly listed valid impacts. A cloned/fetched repository under attacker control can contain a `post-checkout`, `post-merge`, file-watcher script, or any background tool (build watcher, formatter-on-save, LFS smudge filter, etc.) that rewrites a tracked file shortly after the user opens the diff and starts composing a commit message. When the user finally clicks "Commit," Desktop re-diffs the now-modified file and reapplies the old line-index selection to it, so the resulting commit can contain lines the user never saw or explicitly excluded from their review. In the worst case, code intentionally excluded by the user gets included and pushed (or vice versa, unintentionally excluding a security-relevant line the user intended to include), all without any warning dialog.

## Likelihood Explanation
The window between diff-load and commit is entirely attacker-controllable in time (the user can pause arbitrarily long while typing a commit message), and the trigger (any process modifying a tracked file, e.g., a repo-provided git hook, npm script, or watch tool) is common in real developer repositories. No local/admin access or credentials are required — only that the victim opens/clones an attacker-authored repository and uses Desktop's partial-commit (line-selection) feature while some in-repo tooling touches the file. There is no existing check in `apply.ts`/`update-index.ts`/`commit.ts` that detects or rejects a diff mismatch, so the app has no safeguard against this scenario.

## Recommendation
Before staging, verify that the diff used for `formatPatch` is the same diff the user's selection was built against — e.g., by hashing/checksumming the working-tree file content (or its git blob OID) captured at selection time and re-checking it at staging time, aborting (or reloading the diff and re-prompting the user) if it has changed. Alternatively, persist the diff object alongside the selection and pass that exact diff into `applyPatchToIndex` instead of re-fetching from disk.

## Proof of Concept
1. Attacker publishes a repository containing a file `app.js` with a script (e.g. a `package.json` "postinstall"/build-watch task, or a git `post-checkout` hook if hooks aren't disabled) that appends/rewrites lines in `app.js` a few seconds after checkout.
2. Victim clones the repo in GitHub Desktop, edits `app.js`, and opens the Changes view; Desktop computes and displays a diff (`getWorkingDirectoryDiff`), and the victim selects only specific lines to include, deselecting the rest, via the partial-commit UI (`DiffSelection`).
3. While the victim is typing the commit message, the attacker's background script fires and modifies `app.js` on disk (adding/removing/shifting lines).
4. Victim clicks "Commit." `createCommit` → `stageFiles` → `applyPatchToIndex` re-runs `getWorkingDirectoryDiff` on the now-changed file [5](#0-4)  and calls `formatPatch(file, diff)` using the stale `file.selection` [6](#0-5) .
5. Because `isSelected` matches by numeric index only, the resulting patch stages lines from the new diff at those indices — content the victim never reviewed — and `git apply --cached` commits it silently, with no diff-mismatch warning shown to the user.

### Citations

**File:** app/src/models/diff/diff-selection.ts (L121-136)
```typescript
  /** Returns a value indicating wether the given line number is selected or not */
  public isSelected(lineIndex: number): boolean {
    const lineIsDivergent =
      !!this.divergingLines && this.divergingLines.has(lineIndex)

    if (this.defaultSelectionType === DiffSelectionType.All) {
      return !lineIsDivergent
    } else if (this.defaultSelectionType === DiffSelectionType.None) {
      return lineIsDivergent
    } else {
      return assertNever(
        this.defaultSelectionType,
        `Unknown base selection type ${this.defaultSelectionType}`
      )
    }
  }
```

**File:** app/src/lib/stores/app-store.ts (L3444-3448)
```typescript
    const diff = await getWorkingDirectoryDiff(
      repository,
      selectedFileBeforeLoad,
      this.hideWhitespaceInChangesDiff
    )
```

**File:** app/src/lib/git/apply.ts (L60-81)
```typescript
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

**File:** app/src/lib/patch-formatter.ts (L129-161)
```typescript
export function formatPatch(
  file: WorkingDirectoryFileChange,
  diff: ITextDiff | ILargeTextDiff
): string {
  let patch = ''

  diff.hunks.forEach((hunk, hunkIndex) => {
    let hunkBuf = ''

    let oldCount = 0
    let newCount = 0

    let anyAdditionsOrDeletions = false

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
```
