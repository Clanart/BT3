This is now solid confirmation of a real, exploitable TOCTOU gap. The Changes view already acknowledges this exact class of risk for *rendering* (comment at `app-store.ts:3480-3486`: "The diff might have changed dramatically since last we loaded it... we'll settle on just updating the selectable lines") but the actual patch-generation path used for staging/committing (`applyPatchToIndex` in `app/src/lib/git/apply.ts:60`) does not perform that reconciliation at all — it re-fetches a *fresh* diff via `getWorkingDirectoryDiff` and blindly applies the user's `DiffSelection` (a bitset of absolute line indices) against whatever hunk layout that fresh diff happens to have.

### Title
Partial-commit line selection is applied against a re-fetched diff, allowing silent staging of unreviewed content when the working file changes between diff render and commit - (File: `app/src/lib/git/apply.ts`)

### Summary
When a user stages/commits only some lines of a file (a "partial commit"), GitHub Desktop does not apply the patch that was actually rendered and reviewed on screen. Instead, `applyPatchToIndex` re-runs `getWorkingDirectoryDiff` at commit time and replays the user's `DiffSelection` (line indices) against this newly computed diff [1](#0-0) . If the tracked file on disk changes between the moment the diff was rendered/selected in the UI and the moment the user clicks "Commit", the hunk boundaries and line indices can shift, causing the same numeric indices to now refer to different, unreviewed lines. `formatPatch` trusts `file.selection.isSelected(absoluteIndex)` unconditionally against the hunks of whatever diff it is given [2](#0-1) .

### Finding Description
The broken invariant is: *"what the user visually selected in the diff viewer is what gets committed."* Desktop already documents awareness of this invariant being fragile for the **rendering** path — `updateChangesWorkingDirectoryDiff` explicitly notes that a reloaded diff might have "changed dramatically" and only prunes now-invalid selectable lines, admitting it doesn't fully revalidate selection state [3](#0-2) . However, no equivalent staleness check exists on the **commit path**. `applyPatchToIndex` never compares the diff it fetches against the diff the UI last displayed to the user; it simply does:

```
const diff = await getWorkingDirectoryDiff(repository, file)
...
const patch = await formatPatch(file, diff)
``` [4](#0-3) 

`formatPatch` walks `diff.hunks` and, for every changed line, tests `file.selection.isSelected(absoluteIndex)` where `absoluteIndex = hunk.unifiedDiffStart + lineIndex` — purely a positional index, not content-addressed [5](#0-4) . If a background process (a build tool, formatter-on-save, git hook such as `post-checkout`/`post-merge`, or any file watcher shipped inside a cloned/fetched repository) rewrites the file after the user opens the diff and selects specific lines but before they press "Commit", the new diff's hunks can have different line counts/positions than the one the user saw. The same absolute indices from the stale `DiffSelection` will now select different (unreviewed) lines in the freshly fetched diff, producing a patch that silently differs from what the user believes they are committing.

### Impact Explanation
This corrupts the *content of the commit itself* — the value a user relies on Desktop to protect. A malicious or compromised repository can ship an npm/build/lint script, editor auto-formatter config, or Git hook that is a normal part of a project's workflow and gets triggered incidentally while a developer is staging changes (e.g. on save, on checkout, on a periodic watch task). The result is that lines the user never selected can end up staged and committed (or lines they did select can be silently dropped), without any error or warning from Desktop. This falls squarely into "silent corruption of what the user commits or pushes."

### Likelihood Explanation
Moderate-to-low but plausible: it requires (1) partial/line-level staging (a heavily used Desktop feature) and (2) some file-mutating process racing with the UI's diff snapshot, which is a normal occurrence in real projects (auto-formatters, lint --fix on save, build watchers, git hooks) rather than requiring privileged access or malware. No user action beyond normal reviewing-then-committing is needed, and no confirmation dialog exists to catch the mismatch, unlike the analogous force-push protections (`--force-with-lease`, "newer commits on remote" warning) that Desktop already implements for the network-race case [6](#0-5) [7](#0-6) .

### Recommendation
Before calling `formatPatch`/`applyPatchToIndex`, re-validate that the freshly fetched diff is structurally equivalent (same hunk boundaries/line content) to the diff the selection was made against; if not, abort the partial stage/commit and force the UI to refresh the diff and require the user to re-confirm their selection, mirroring the "commits changed underneath you" warnings Desktop already shows for pushes. At minimum, compare the diff text/hash captured at selection time against the one fetched in `applyPatchToIndex` and fail loudly on mismatch instead of silently proceeding.

### Proof of Concept
1. Open a repository in Desktop, modify a tracked file with several distinct hunks.
2. In the Changes view, open the diff for that file and select only specific lines within one hunk (partial selection), leaving the commit dialog open/focused.
3. Trigger an external modification of the same file that shifts line counts before hunk N (e.g., simulate a `post-checkout`/save-hook script, or a build tool watch task in the repo, appending/removing lines earlier in the file) — done without further interacting with Desktop's diff view so the cached `DiffSelection` is not revalidated.
4. Click "Commit" without reopening/re-rendering the diff. `performCommit`/`createCommit` invokes `stageFiles` → `applyPatchToIndex`, which re-fetches the diff via `getWorkingDirectoryDiff` [8](#0-7)  and applies the old `DiffSelection` indices against the new hunk layout.
5. Inspect the resulting commit (`git show`) and confirm it differs from the lines that were visually checked in the diff viewer prior to the external file change.

### Citations

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

**File:** app/src/lib/stores/app-store.ts (L3478-3493)
```typescript
    const selectableLines = new Set<number>()
    if (diff.kind === DiffType.Text || diff.kind === DiffType.LargeText) {
      // The diff might have changed dramatically since last we loaded it.
      // Ideally we would be more clever about validating that any partial
      // selection state is still valid by ensuring that selected lines still
      // exist but for now we'll settle on just updating the selectable lines
      // such that any previously selected line which now no longer exists or
      // has been turned into a context line isn't still selected.
      diff.hunks.forEach(h => {
        h.lines.forEach((line, index) => {
          if (line.isIncludeableLine()) {
            selectableLines.add(h.unifiedDiffStart + index)
          }
        })
      })
    }
```

**File:** app/src/lib/git/push.ts (L66-70)
```typescript
  if (!remoteBranch) {
    args.push('--set-upstream')
  } else if (options?.forceWithLease) {
    args.push('--force-with-lease')
  }
```

**File:** app/src/ui/push-needs-pull/push-needs-pull-warning.tsx (L44-49)
```typescript
          <p>
            GitHub Desktop is unable to push commits to this branch because
            there are commits on the remote that are not present on your local
            branch. Fetch these new commits before pushing in order to reconcile
            them with your local commits.
          </p>
```

**File:** app/src/lib/git/update-index.ts (L163-168)
```typescript
  // Finally we run through all files that have partial selections.
  // We don't care about renamed or not here since applyPatchToIndex
  // has logic to support that scenario.
  for (const file of partial) {
    await applyPatchToIndex(repository, file)
  }
```
