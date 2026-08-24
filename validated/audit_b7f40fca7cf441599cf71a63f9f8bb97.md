## Title
Stale line-selection commit corrupts partial commits when working directory content changes between diff review and commit - (File: `app/src/lib/git/apply.ts`, `app/src/lib/patch-formatter.ts`)

### Summary
The external report describes a class of bug where a value (`weeklyPremium`) is captured and used to compute a price, but the value can change between when the user reviews/decides and when the transaction actually executes, causing the user to unknowingly pay more than they agreed to — with no on-chain guard tying execution to the reviewed value. The same "decide against snapshot A, execute against snapshot B" pattern exists in GitHub Desktop's partial-commit / line-selection staging flow: the user selects specific diff lines to include in a commit based on a diff rendered at time T1, but the actual patch applied to the index is generated from a **fresh** diff read at commit time T2 [1](#0-0) , using the **old, index-based line selection** [2](#0-1) . If the on-disk content of the file changes between T1 and T2 (e.g., due to a filter driver / smudge-clean pipeline defined by an attacker-controlled cloned repository's `.gitattributes`, or any other content-altering hook that fires between diff display and the commit click), the line indices selected by the user no longer correspond to the same logical lines in the new diff, and Desktop will silently stage/commit different content than what the user reviewed and approved.

### Finding Description
The commit flow is:
1. Desktop computes and caches a diff for the currently selected file when the user views the Changes tab: `getWorkingDirectoryDiff` [3](#0-2) . The user then toggles individual lines/hunks via `file.selection`, which stores an index-based bitmap keyed to that diff's line positions.
2. When the user clicks "Commit", `createCommit` is invoked, which unstages everything and re-stages files via `stageFiles` [4](#0-3) .
3. For any file with a partial selection, `stageFiles` calls `applyPatchToIndex`, which re-reads the diff from disk **at that moment** via `getWorkingDirectoryDiff(repository, file)` — a second, independent diff computation, not the one the user actually looked at [1](#0-0) .
4. `formatPatch` then walks the **newly fetched** diff's hunks and lines and decides whether each line is included by calling `file.selection.isSelected(absoluteIndex)` — using line-index positions computed against the **original** (potentially stale) diff [5](#0-4) .

There is no check anywhere in this path that the diff used to build `file.selection` still matches the diff read at commit time (no hash/line-count/content comparison, no re-prompt, no abort). Contrast this with `updateChangesWorkingDirectoryDiff`, which *does* guard against staleness for the purpose of updating the *UI* display [6](#0-5)  — but that staleness protection only prevents the UI from showing outdated diff content; it does nothing to protect the actual commit operation, which independently re-diffs and reapplies the stored selection bitmap.

This is structurally identical to the reported Pool.buy issue: a monetary/semantic decision (`allPremium`, or here, "which lines to commit") is derived from a value snapshot (`weeklyPremium` / diff at T1) but the operation that is ultimately executed (`buy` / `git apply --cached`) is evaluated against a different, later value (`weeklyPremium` at execution time / diff at T2), with no bound/consistency check tying the two together.

### Impact Explanation
If a cloned/fetched repository is attacker-controlled (e.g., contains a `.gitattributes` filter/clean driver, a `core.fsmonitor` hook, or any mechanism that rewrites tracked file content asynchronously — all of which are legitimate, attacker-suppliable repository configuration, not "malware already on the host"), the attacker can cause the on-disk representation of a file to shift lines (insert/remove blank lines, reflow whitespace, etc.) in the window between the user reviewing a diff and clicking Commit (which can be arbitrarily long — the user might review, then go get coffee, while a filter or background process changes file content). When the user commits, Desktop will silently include/exclude different code than what was shown and approved, i.e., **silent corruption of what the user commits**. This can be used to smuggle unreviewed/malicious code into a commit that the user believes only contains the lines they explicitly checked, or to make a user unknowingly exclude a security-relevant line they intended to keep, and later push that corrupted commit to a shared remote.

### Likelihood Explanation
The precondition (working tree content changing between the diff render and the commit action) is realistic and repo-attacker-controllable through standard git mechanisms (filters, hooks) that GitHub Desktop already invokes as part of normal `git diff`/`git status`/`git add` cycles, and the time window is entirely user-paced (arbitrarily long, no server-side synchronization). No local malware, admin rights, or unusual user steps are required beyond opening/cloning the malicious repo and doing a normal partial commit — a core, everyday Desktop workflow.

### Recommendation
Before applying `file.selection` to the freshly-read diff in `applyPatchToIndex`/`formatPatch`, validate that the diff used to build the selection is still consistent with the diff read at commit time (e.g., compare a content hash or the diff's hunk headers/line count recorded when the selection was captured). If they differ, abort the commit for that file and force the UI to refresh the diff and require the user to re-confirm the selection, mirroring the way `updateChangesWorkingDirectoryDiff` already discards stale UI state on mismatch.

### Proof of Concept
1. Clone a malicious repository that defines a `.gitattributes` clean/smudge filter (or similar content-mutating driver) for a tracked file, configured to alter line content/count only after a short delay or on a secondary git invocation.
2. In Desktop, open the Changes tab, select the file, and use partial-line selection to stage only specific lines of a multi-line hunk.
3. Before clicking "Commit", trigger the filter driver's mutation (e.g., wait for Desktop's periodic background status refresh, which invokes `git status`/`diff` and thus the filter, mutating the working copy).
4. Click "Commit". Because `applyPatchToIndex` re-diffs the now-mutated file and applies the original index-based `file.selection` to the new hunk layout, the resulting commit will contain different lines than what the user selected in step 2 — with no warning to the user.

Note: The exact attacker mechanism for triggering the timing (which filter/hook combination reliably mutates content mid-session) was not verified end-to-end in the index available to me; a background Devin session with full repo/tooling access would be needed to build and run a concrete filter-driver PoC to confirm exploitability in practice.

### Citations

**File:** app/src/lib/git/apply.ts (L52-62)
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
```

**File:** app/src/lib/patch-formatter.ts (L129-157)
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
```

**File:** app/src/lib/stores/app-store.ts (L3444-3448)
```typescript
    const diff = await getWorkingDirectoryDiff(
      repository,
      selectedFileBeforeLoad,
      this.hideWhitespaceInChangesDiff
    )
```

**File:** app/src/lib/stores/app-store.ts (L3453-3464)
```typescript
    // A different file (or files) could have been selected while we were
    // loading the diff in which case we no longer care about the diff we
    // just loaded.
    if (
      changesState.selection.kind !== ChangesSelectionKind.WorkingDirectory ||
      !arrayEquals(
        changesState.selection.selectedFileIDs,
        selectedFileIDsBeforeLoad
      )
    ) {
      return
    }
```

**File:** app/src/lib/git/commit.ts (L26-31)
```typescript
  // Clear the staging area, our diffs reflect the difference between the
  // working directory and the last commit (if any) so our commits should
  // do the same thing.
  await unstageAll(repository)

  await stageFiles(repository, files)
```
