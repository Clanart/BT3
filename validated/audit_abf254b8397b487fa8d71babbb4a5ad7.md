I have enough evidence now to construct the analog. The core broken invariant matches the SwapGuardV2 pattern precisely: a "checkpoint" (the diff+selection the user reviewed) is established at one point in time via `getWorkingDirectoryDiff`, but the actual value used for the security-relevant operation (the patch applied to the index / committed) is recomputed independently at commit time from live disk state, with the user's line-index-based selection blindly reapplied to whatever the second read produces — no re-validation that the reviewed content still matches.

### Title
Partial-commit selections are applied to a re-read working-directory diff, allowing repository-triggered file changes to silently commit unreviewed content - (File: app/src/lib/git/apply.ts)

### Summary
When a user stages only some lines/hunks of a file, Desktop stores the choice as a set of line indices (`DiffSelection`) computed against a specific diff snapshot rendered in the UI. When the commit is actually created, `applyPatchToIndex` does not reuse that snapshot; it re-invokes `getWorkingDirectoryDiff` against the live working directory and blindly reapplies the old positional indices to the new diff to build the patch that gets staged and committed. If the file content changes between the moment the user reviewed/selected lines and the moment the commit executes (e.g., because a hook, LFS smudge/clean filter, or other tracked automation shipped in the cloned repository touches the file), the committed/pushed content can silently diverge from what the user saw and approved.

### Finding Description
The UI computes a diff once for display and selection: `updateChangesWorkingDirectoryDiff` calls `getWorkingDirectoryDiff(repository, selectedFileBeforeLoad, ...)` and stores per-line selection state as abstract indices via `DiffSelection` (`app/src/lib/stores/app-store.ts:3444`, `app/src/models/diff/diff-selection.ts:122`). The user toggles inclusion of specific lines based on that rendered diff (`app/src/ui/diff/side-by-side-diff.tsx:1335`). [1](#0-0) 

When the commit is actually created, `createCommit` → `stageFiles` → `applyPatchToIndex` is invoked, and this function **independently re-fetches the diff from disk** rather than reusing the diff the selection was made against: [2](#0-1) 

`formatPatch` then reapplies the old selection's `isSelected(absoluteIndex)` check against the *new* diff's hunk lines purely by positional index, with no verification that the line at that index is still the same content the user reviewed: [3](#0-2) 

`DiffSelection.isSelected` is a pure index lookup with no content binding at all: [4](#0-3) 

This is the same broken invariant as the SwapGuardV2 report: a "checkpoint" (the reviewed diff + selection) is established once, but the value actually acted upon (the freshly re-read diff) is captured by a separate, unsynchronized call, and the code trusts stale positional state against it. Just as `makeCheckpoint()`/`ensureCheckpoint()` can be desynchronized by an intervening call, the reviewed diff and the staged diff can be desynchronized by anything that mutates the working tree between `updateChangesWorkingDirectoryDiff` and `applyPatchToIndex` — for example a `post-checkout`/`post-merge`/`pre-commit` hook, an LFS clean/smudge filter, or any other automation shipped inside a cloned/fetched repository that Desktop already knows how to execute (see `app/src/lib/hooks/get-repo-hooks.ts`, `app/src/lib/hooks/with-hooks-env.ts`). Note in particular that `createCommit` explicitly enables interception of `pre-commit` (`app/src/lib/git/commit.ts:58-65`), which git runs before the final `git commit` invocation but *after* `stageFiles`/`applyPatchToIndex` have already computed the staged content from a freshly re-diffed file — so a hook running earlier in the same operation (e.g. triggered by a preceding step, an editor auto-format-on-save, or a concurrently running LFS smudge on checkout) can alter file content in the window between the two `getWorkingDirectoryDiff` calls without the user being shown the new content or asked to re-confirm their line selection. [5](#0-4) 

### Impact Explanation
If the working-tree file changes between the diff the user reviewed and the diff used to build the staged patch, the resulting commit can contain lines the user never saw or explicitly deselected, and can omit lines the user selected — silent corruption of what the user commits and subsequently pushes. This is a direct match for the "silent corruption of what the user commits or pushes" category of valid impact, driven entirely by content coming from a cloned/fetched repository (hooks, LFS config, or other tracked automation), with no need for local/physical access, admin rights, or pre-existing host malware.

### Likelihood Explanation
Triggering this requires the working file to change between two specific internal calls to `getWorkingDirectoryDiff` within a single commit operation, which is a narrow timing window that ordinarily depends on external actors (editors, file watchers, LFS filters) running concurrently with the app. Desktop does provide some validation when the *displayed* diff changes across a completed reload (`app/src/lib/stores/app-store.ts:3478-3497` recomputes `selectableLines`), but this guard only runs on the earlier, separate "refresh diff for display" path — it is not consulted again by `applyPatchToIndex` immediately before staging, so a change occurring after the last UI diff refresh but before/during commit is not caught.

### Recommendation
Apply the same fix pattern the report recommends for SwapGuardV2: collapse the two-step "diff now, act on stale state later" flow into a single atomic checkpoint. Concretely, `applyPatchToIndex`/`stageFiles` should reuse the exact diff object the user's selection was computed against (passed through from the Changes view) rather than re-fetching a fresh diff at commit time, or, if a fresh diff must be fetched, `createCommit` should hash/compare the newly fetched diff against the one the selection was made from and abort/re-prompt the user if the file has changed since the selection was made, instead of silently reapplying positional indices to different content.

### Proof of Concept
1. In a cloned repository, add a `post-checkout` (or other Desktop-intercepted, `.git/hooks`-adjacent) automation, or a `filter=<name>` `.gitattributes` entry paired with an already globally-configured clean/smudge filter, that appends or alters a line in `tracked-file.txt` shortly after it is touched.
2. In Desktop, modify `tracked-file.txt` locally, open the Changes diff, and deselect (uncheck) a specific line/hunk you do not want committed.
3. Before clicking "Commit", let the repository-driven automation fire and rewrite the same region of `tracked-file.txt` (e.g., via a background process the repository is configured to run).
4. Click "Commit". Observe that `applyPatchToIndex` (`app/src/lib/git/apply.ts:60`) re-diffs the now-modified file and `formatPatch` (`app/src/lib/patch-formatter.ts:157`) reapplies the old positional selection, producing and committing a patch whose content differs from what was shown/deselected in step 2 — with no re-confirmation shown to the user.

### Citations

**File:** app/src/lib/stores/app-store.ts (L3444-3449)
```typescript
    const diff = await getWorkingDirectoryDiff(
      repository,
      selectedFileBeforeLoad,
      this.hideWhitespaceInChangesDiff
    )

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

**File:** app/src/lib/patch-formatter.ts (L143-171)
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
```

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

**File:** app/src/lib/git/commit.ts (L15-72)
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

  const args = ['-F', '-']

  if (options?.amend) {
    args.push('--amend')
  }

  if (options?.noVerify) {
    args.push('--no-verify')
  }

  if (options?.signOff) {
    args.push('--signoff')
  }

  if (options?.allowEmpty) {
    args.push('--allow-empty')
  }

  const result = await git(
    ['commit', ...args],
    repository.path,
    'createCommit',
    {
      stdin: message,
      // https://git-scm.com/docs/githooks/2.46.1
      interceptHooks: [
        'pre-commit',
        'prepare-commit-msg',
        'commit-msg',
        'post-commit',
        ...(options?.amend ? ['post-rewrite'] : []),
        'pre-auto-gc',
      ],
      onHookProgress: options?.onHookProgress,
      onHookFailure: options?.onHookFailure,
      onTerminalOutputAvailable: options?.onTerminalOutputAvailable,
    }
  )
  return parseCommitSHA(result)
}
```
