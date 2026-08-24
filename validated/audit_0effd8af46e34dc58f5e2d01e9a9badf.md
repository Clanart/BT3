Found the strongest analog: a hardcoded/naive assumption about status parsing that silently miscategorizes a change, causing the app to commit different content than what the user believes they are committing — mirroring the report's core theme ("protocol assumes X≈Y without verifying, attacker manipulates reality to differ").

### Title
Unhandled/unmapped `git status` code silently falls back to "modified", causing the app to stage and commit unintended content - ([File: app/src/lib/status-parser.ts])

### Summary
`mapStatus` in `app/src/lib/status-parser.ts` assumes every two-character porcelain v2 status code it doesn't explicitly recognize can safely be treated as an ordinary "modified" file [1](#0-0)  This is analogous to the ClaimCore bug: the code hardcodes an assumption ("unrecognized status ≈ modified", just like "1 stable ≈ 1 USD") instead of verifying/handling the actual state, and an attacker who controls the repository content (via unusual index states reachable through a crafted/cloned repo, e.g. a hostile submodule commit, a symlink/type-change entry, or an unmerged/conflict permutation not covered by the explicit branches above it) can cause a mismatch between what Desktop displays and what actually gets staged.

### Finding Description
`mapStatus` receives the raw two-letter status code from `git status --porcelain=2 -z` and explicitly matches known combinations (ordinary, renamed, copied, untracked, unmerged/conflicted) [2](#0-1) [3](#0-2) . For any code that does not match one of these branches, the function falls through to a default that unconditionally reports `{ kind: 'ordinary', type: 'modified', submoduleStatus }` [1](#0-0) .

This status feeds directly into `buildStatusMap`, which determines the `AppFileStatusKind` shown to the user and, critically, the initial diff-selection state used when staging/committing [4](#0-3) . The staging pipeline (`stageFiles` / `applyPatchToIndex`) branches its behavior based on `AppFileStatusKind` — e.g., it special-cases `Renamed` and `Deleted` to run `git mv`-style index surgery and otherwise just applies whatever diff/patch was generated for the (assumed) "modified" file [5](#0-4) , and `applyPatchToIndex` similarly branches only on the kinds it knows about [6](#0-5) .

If git ever emits a two-character code combination this switch does not anticipate — e.g. an unusual working-tree/index pairing produced by a crafted commit tree containing type-changes (regular file ↔ symlink ↔ submodule gitlink), or a partially-resolved conflict state reached via a hostile repository/branch a victim fetches or checks out — Desktop will silently treat it as an ordinary text modification. This breaks the guarantee that the UI's Changes list and the resulting commit/patch accurately reflect the real git state. Unlike the diff/patch code, which explicitly throws on unrecognized diff kinds (`assertNever`, see `apply.ts` lines 62-78), the status parser has no equivalent fail-closed behavior — it fails open into "modified", the most generic and least protective classification.

### Impact Explanation
This can lead to "silent corruption of what the user commits or pushes" (explicitly a valid impact category): the user may believe they are committing an ordinary text edit while, in fact, a type-changed entry (e.g., a submodule gitlink or symlink swapped in via a malicious repository) is staged and committed with default handling instead of the special-cased logic renames/deletes/submodules receive elsewhere in the codebase. Severity is bounded by the fact that this requires an unusual repository state to be reachable and observable by Desktop, which is not fully confirmed without deeper testing of exotic git porcelain v2 codes.

### Likelihood Explanation
Likelihood is Low-to-Medium: it requires the victim to open/fetch/checkout a repository engineered to produce an index/working-tree state whose porcelain v2 code isn't covered by the explicit branches in `mapStatus`. Git's porcelain v2 format has a fixed, well-documented set of codes, so the practical trigger surface is narrow, but it is fully attacker-influenced (the repository content and its resulting git status), matching the "attacker controls a cloned/fetched repository" impact criterion.

### Recommendation
Add an explicit exhaustive check (mirroring `assertNever` used in `apply.ts`) that throws/logs loudly for any status code combination not matched by the known branches, rather than silently defaulting to `'modified'`. This ensures unexpected repository states surface as errors requiring investigation instead of being silently committed as ordinary changes.

### Proof of Concept
Not independently reproduced in this session — verifying a concrete git porcelain v2 code combination that reaches the fallback branch requires constructing a repository with an exotic index/working-tree state (e.g. mixed type-change + rename + conflict) and running `git status --porcelain=2 -z` against it, then confirming Desktop's `mapStatus`/`buildStatusMap` misclassifies it. This would need to be validated in a full Devin session with terminal access to git, which is outside the scope of this read-only analysis.

### Citations

**File:** app/src/lib/status-parser.ts (L272-309)
```typescript
  if (statusCode === 'R.') {
    return {
      kind: 'renamed',
      index: GitStatusEntry.Renamed,
      workingTree: GitStatusEntry.Unchanged,
      renameOrCopyScore,
      submoduleStatus,
    }
  }

  if (statusCode === '.R') {
    return {
      kind: 'renamed',
      index: GitStatusEntry.Unchanged,
      workingTree: GitStatusEntry.Renamed,
      renameOrCopyScore,
      submoduleStatus,
    }
  }

  if (statusCode === 'C.') {
    return {
      kind: 'copied',
      index: GitStatusEntry.Copied,
      workingTree: GitStatusEntry.Unchanged,
      submoduleStatus,
    }
  }

  if (statusCode === '.C') {
    return {
      kind: 'copied',
      index: GitStatusEntry.Unchanged,
      workingTree: GitStatusEntry.Copied,
      submoduleStatus,
    }
  }

```

**File:** app/src/lib/status-parser.ts (L400-418)
```typescript
  if (statusCode === 'AA') {
    return {
      kind: 'conflicted',
      action: UnmergedEntrySummary.BothAdded,
      us: GitStatusEntry.Added,
      them: GitStatusEntry.Added,
      submoduleStatus,
    }
  }

  if (statusCode === 'UU') {
    return {
      kind: 'conflicted',
      action: UnmergedEntrySummary.BothModified,
      us: GitStatusEntry.UpdatedButUnmerged,
      them: GitStatusEntry.UpdatedButUnmerged,
      submoduleStatus,
    }
  }
```

**File:** app/src/lib/status-parser.ts (L420-425)
```typescript
  // as a fallback, we assume the file is modified in some way
  return {
    kind: 'ordinary',
    type: 'modified',
    submoduleStatus,
  }
```

**File:** app/src/lib/git/status.ts (L297-349)
```typescript
function buildStatusMap(
  files: Map<string, WorkingDirectoryFileChange>,
  entry: IStatusEntry,
  conflictDetails: ConflictFilesDetails
): Map<string, WorkingDirectoryFileChange> {
  const status = mapStatus(
    entry.statusCode,
    entry.submoduleStatusCode,
    entry.renameOrCopyScore
  )

  if (status.kind === 'ordinary') {
    // when a file is added in the index but then removed in the working
    // directory, the file won't be part of the commit, so we can skip
    // displaying this entry in the changes list
    if (
      status.index === GitStatusEntry.Added &&
      status.workingTree === GitStatusEntry.Deleted
    ) {
      return files
    }
  }

  if (status.kind === 'untracked') {
    // when a delete has been staged, but an untracked file exists with the
    // same path, we should ensure that we only draw one entry in the
    // changes list - see if an entry already exists for this path and
    // remove it if found
    files.delete(entry.path)
  }

  // for now we just poke at the existing summary
  const appStatus = convertToAppStatus(
    entry.path,
    status,
    conflictDetails,
    entry.oldPath
  )

  const initialSelectionType =
    appStatus.kind === AppFileStatusKind.Modified &&
    appStatus.submoduleStatus !== undefined &&
    !appStatus.submoduleStatus.commitChanged
      ? DiffSelectionType.None
      : DiffSelectionType.All

  const selection = DiffSelection.fromInitialSelection(initialSelectionType)

  files.set(
    entry.path,
    new WorkingDirectoryFileChange(entry.path, appStatus, selection)
  )
  return files
```

**File:** app/src/lib/git/update-index.ts (L109-168)
```typescript
export async function stageFiles(
  repository: Repository,
  files: ReadonlyArray<WorkingDirectoryFileChange>
): Promise<void> {
  const normal = []
  const oldRenamed = []
  const partial = []
  const deletedFiles = []

  for (const file of files) {
    if (file.selection.getSelectionType() === DiffSelectionType.All) {
      normal.push(file.path)
      if (file.status.kind === AppFileStatusKind.Renamed) {
        oldRenamed.push(file.status.oldPath)
      } else if (file.status.kind === AppFileStatusKind.Deleted) {
        deletedFiles.push(file.path)
      }
    } else {
      partial.push(file)
    }
  }

  // Staging files happens in three steps.
  //
  // In the first step we run through all of the renamed files, or
  // more specifically the source files (old) that were renamed and
  // forcefully remove them from the index. We do this in order to handle
  // the scenario where a file has been renamed and a new file has been
  // created in its original position. Think of it like this
  //
  // $ touch foo && git add foo && git commit -m 'foo'
  // $ git mv foo bar
  // $ echo "I'm a new foo" > foo
  //
  // Now we have a file which is of type Renamed that has its path set
  // to 'bar' and its oldPath set to 'foo'. But there's a new file called
  // foo in the repository. So if the user selects the 'foo -> bar' change
  // but not the new 'foo' file for inclusion in this commit we don't
  // want to add the new 'foo', we just want to recreate the move in the
  // index. We do this by forcefully removing the old path from the index
  // and then later (in step 2) stage the new file.
  await updateIndex(repository, oldRenamed, { forceRemove: true })

  // In the second step we update the index to match
  // the working directory in the case of new, modified, deleted,
  // and copied files as well as the destination paths for renamed
  // paths.
  await updateIndex(repository, normal)

  // This third step will only happen if we have files that have been marked
  // for deletion. This covers us for files that were blown away in the last
  // updateIndex call
  await updateIndex(repository, deletedFiles, { forceRemove: true })

  // Finally we run through all files that have partial selections.
  // We don't care about renamed or not here since applyPatchToIndex
  // has logic to support that scenario.
  for (const file of partial) {
    await applyPatchToIndex(repository, file)
  }
```

**File:** app/src/lib/git/apply.ts (L12-83)
```typescript
export async function applyPatchToIndex(
  repository: Repository,
  file: WorkingDirectoryFileChange
): Promise<void> {
  // If the file was a rename we have to recreate that rename since we've
  // just blown away the index. Think of this block of weird looking commands
  // as running `git mv`.
  if (file.status.kind === AppFileStatusKind.Renamed) {
    // Make sure the index knows of the removed file. We could use
    // update-index --force-remove here but we're not since it's
    // possible that someone staged a rename and then recreated the
    // original file and we don't have any guarantees for in which order
    // partial stages vs full-file stages happen. By using git add the
    // worst that could happen is that we re-stage a file already staged
    // by updateIndex.
    await git(
      ['add', '--update', '--', file.status.oldPath],
      repository.path,
      'applyPatchToIndex'
    )

    // Figure out the blob oid of the removed file
    // <mode> SP <type> SP <object> TAB <file>
    const oldFile = await git(
      ['ls-tree', 'HEAD', '--', file.status.oldPath],
      repository.path,
      'applyPatchToIndex'
    )

    const [info] = oldFile.stdout.split('\t', 1)
    const [mode, , oid] = info.split(' ', 3)

    // Add the old file blob to the index under the new name
    await git(
      ['update-index', '--add', '--cacheinfo', mode, oid, file.path],
      repository.path,
      'applyPatchToIndex'
    )
  }

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

  return Promise.resolve()
```
