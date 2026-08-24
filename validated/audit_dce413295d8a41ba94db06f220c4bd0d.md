## Findings

The bug class in M-8 is: **two operations that are supposed to be inverses of each other are computed against different underlying state (yield-adjusted share/asset ratio), and nothing checks that the state hasn't shifted between them — so a positional/index-based mapping silently produces the wrong result.**

I found a structurally identical pattern in GitHub Desktop's partial-staging/commit pipeline.

### Title
Stale line-selection indices are silently reapplied to a freshly re-fetched diff during staging, allowing corruption of committed content - (File: `app/src/lib/git/apply.ts`)

### Summary
`DiffSelection` (`app/src/models/diff/diff-selection.ts`) tracks which lines a user wants to commit purely as a set of **numeric line indices** (`divergingLines: Set<number>`), with no binding to the content or version of the diff those indices were computed from. When the user actually commits, `createCommit` → `stageFiles` → `applyPatchToIndex` (`app/src/lib/git/apply.ts:60`) **re-fetches a brand-new diff** from disk via `getWorkingDirectoryDiff(repository, file)` and then calls `formatPatch(file, diff)`, which reuses `file.selection.isSelected(absoluteIndex)` against the hunks of that *new* diff [1](#0-0) . There is no check anywhere that the newly fetched diff still has the same hunk layout/line count as the diff the selection was originally computed against.

### Finding Description
`formatPatch` walks the (freshly fetched) diff's hunks and, for each non-context line, asks `file.selection.isSelected(absoluteIndex)` where `absoluteIndex = hunk.unifiedDiffStart + lineIndex` [2](#0-1) . `DiffSelection.isSelected` is a pure index lookup with no notion of file content or diff version — it only knows "line index N is/isn't in the diverging set" [3](#0-2) .

This mirrors the WStable flaw exactly: `mint` converts using one snapshot of the share:asset ratio, `burn` converts using a different (later) snapshot, and nothing detects or compensates for the drift — the accounting silently breaks. Here, the user's line selection is built while looking at diff **A** (rendered when a file was opened in the Changes view), but at commit time the code discards that diff and fetches diff **B** live off disk, then blindly reapplies the index set built against **A** onto **B**. If diff **B**'s hunk boundaries, line ordering, or line count differ at all from **A** (e.g., because the working-tree file was modified between the time the user selected lines and the time they clicked Commit), the same numeric indices now point at different content. A line the user explicitly deselected could get committed, and a line they wanted included could be silently dropped or, worse, a deletion the user never confirmed could be staged as-is.

The `AppFileStatusKind.Renamed` handling and the "new/untracked file" special case even acknowledge that partial-selection application is index-sensitive and fragile [4](#0-3) , but no code path validates that the diff used to build the selection is the same diff used to render the final patch.

### Impact Explanation
This is a **silent corruption of what the user commits/pushes**, one of the explicitly valid impact categories. An attacker who controls a cloned/fetched repository can ship a repository configured with a git hook (e.g. `post-checkout`, `post-merge`, or a smudge filter driven by `.gitattributes`) that rewrites a tracked file's contents shortly after checkout/fetch, or ships a build/watch script that a project's documented workflow tells the victim to run in the background while working in Desktop. If that mutation happens between the moment the victim reviews and selects specific lines in the Changes diff view and the moment they press "Commit", `applyPatchToIndex`'s fresh re-fetch of the diff combined with the stale, purely-positional `DiffSelection` can cause git to stage/commit content the user never approved — without any warning, diff re-confirmation, or error from Desktop. Because commits are then pushed, this can result in the victim unknowingly pushing attacker-influenced content under their own authorship.

### Likelihood Explanation
The trigger requires only a timing window between line-selection and commit while the working tree changes underneath Desktop — something attacker-controlled repository tooling (hooks, filters, or documented "run this watcher" build scripts) can realistically induce without any privileged access, malware, or leaked credentials, satisfying the "attacker controls a cloned/fetched repository" precondition. I was not able to fully trace, within the indexed portion of the codebase, whether Desktop's Changes view forces a fresh diff-and-selection reset immediately before every commit click (I found no such revalidation in `createCommit`/`stageFiles`/`applyPatchToIndex`, but background file-watcher/status-polling code that could also be relevant was outside what I could inspect). This uncertainty should be resolved by reviewing `app/src/lib/stores/app-store.ts`'s commit flow and the working-directory watcher for any diff/selection reconciliation step before concluding severity.

### Recommendation
Bind the `DiffSelection` (or the patch generated from it) to a fingerprint (e.g., diff text hash, or per-hunk content hash) of the diff it was built from. Before staging/committing, re-fetch the diff and compare its fingerprint to the one recorded when the selection was made; if they differ, abort the commit for that file and force the UI to re-render the diff so the user re-confirms their line selection against current content, rather than silently reapplying stale indices in `applyPatchToIndex`/`formatPatch`.

### Proof of Concept
Conceptual reproduction, based on the confirmed code path (not run against a live app):
1. Open a repository in Desktop and modify a tracked file so it has a multi-hunk diff.
2. In the Changes view, deselect specific lines/hunks the user does not want committed (building a `DiffSelection` with `divergingLines` tied to line indices of the currently-rendered diff).
3. Before clicking Commit, have an external process (simulating an attacker-supplied git hook/filter/build script shipped with a malicious repo) modify the same file on disk in a way that shifts line counts/hunk boundaries (e.g., insert or remove a line above the hunks the user was editing).
4. Click Commit. `createCommit` → `stageFiles` → `applyPatchToIndex` (`app/src/lib/git/apply.ts:60`) fetches the new diff and calls `formatPatch(file, diff)`, reusing the old `DiffSelection`'s indices (`app/src/lib/patch-formatter.ts:143-171`) against the new hunk structure.
5. Inspect the resulting commit: it will not match either the diff the user reviewed nor the diff on disk at commit time — lines the user intended to exclude/include get flipped based on the shifted index mapping, demonstrating silent corruption of the committed content. [1](#0-0) [2](#0-1) [3](#0-2) [5](#0-4)

### Citations

**File:** app/src/lib/git/apply.ts (L52-61)
```typescript
  const applyArgs: string[] = [
    'apply',
    '--cached',
    '--unidiff-zero',
    '--whitespace=nowarn',
    '-',
  ]

  const diff = await getWorkingDirectoryDiff(repository, file)

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
