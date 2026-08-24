## Analysis

The Noya finding's core pattern: a **single failing item inside a shared batch operation aborts the whole batch**, causing collateral damage/DoS to unrelated items processed together. I looked for the same broken invariant in GitHub Desktop and found it in the multi-file **Discard Changes** flow.

### Title
Attacker-controlled repository path can abort the batched `checkout-index` step of "Discard Changes," leaving unrelated already-trashed files unrestored - (File: `app/src/lib/stores/git-store.ts`)

### Summary
`GitStore.discardChanges()` processes an array of `WorkingDirectoryFileChange` in two phases: first it moves each file to the OS trash **individually, file-by-file**, and only afterward does it issue a **single batched** `resetPaths` + `checkoutIndex` call wrapped in one `performFailableOperation`. Because the restore-from-index step is one atomic git invocation across the whole file list, a problem with just one path in that list can make the entire checkout fail, after the per-file trash step has already succeeded for every other file in the batch.

### Finding Description
In `discardChanges`, files are looped over and moved to trash one at a time: [1](#0-0) 

Only after this per-file loop completes does Desktop compute `necessaryPathsToReset`/`necessaryPathsToCheckout` and perform the actual restoration from the git index as a **single batched operation**: [2](#0-1) 

`checkoutIndex` itself is a single `git checkout-index -f -u -q --stdin -z` invocation that receives *all* paths on stdin in one call: [3](#0-2) 

If this single call throws for any reason tied to one specific path in the batch (e.g., a path from an attacker-controlled/fetched branch that collides with another entry on a case-insensitive or Unicode-normalizing filesystem, or a path git refuses to write because of an intervening directory left by a previous checkout of hostile content), `performFailableOperation` propagates the failure and the whole `discardChanges` call rejects: [4](#0-3) 

Because the trash step for the *other* files in the same discard batch has already executed and deleted their working-directory contents, but the batched `checkout-index` that would restore them from HEAD never completes, those unrelated files are left missing on disk while the operation reports a single error for the whole batch — mirroring the Noya pattern where one bad participant blocks the shared operation for everyone else in the queue.

### Impact Explanation
A victim who fetches/checks out a branch containing an attacker-crafted path (e.g. colliding case-only filenames, or a path that becomes a directory where a sibling entry expects a file) and then selects "Discard All Changes" or a multi-file discard in Desktop can have the batched `checkout-index` step fail. All the other files in that selection — which were already moved to the OS trash — will not be restored from the index, leaving the working directory missing content for files unrelated to the attacker's crafted path. If the user is unaware and continues to work (stage/commit), this can lead to those files appearing as locally deleted and potentially being committed as deletions, i.e. corruption of what the user commits.

### Likelihood Explanation
This requires the user to (a) have a working-directory change set that includes both attacker-influenced and unrelated files, and (b) invoke a multi-file/"Discard All" operation. It does not require local/physical access, admin rights, or leaked credentials — only that the victim previously fetched/checked out attacker-controlled repository content, which is within the normal Desktop threat model (cloned/fetched repository as attack vector). I was not able to fully verify, within this session, a concrete filesystem/path condition that reliably makes `git checkout-index` fail for only one path while leaving trash-deletion already applied for the rest — this would need empirical testing (e.g., on case-insensitive HFS+/APFS or NTFS) to confirm a repeatable trigger, which I could not complete due to tool/time limits.

### Recommendation
- Make the restore-from-index step resilient per-file (or split the batched `checkout-index`/`resetPaths` calls so a failure on one path does not prevent successful restoration of the rest), and/or perform the trash-and-restore as a single per-file transaction instead of "trash all, then batch-restore all."
- Surface which specific files failed to be restored after a partial `discardChanges` failure, rather than a single opaque emitted error, so users are not left with silently missing files.

### Proof of Concept
Not independently reproduced in this session — confirming the exact path condition that causes `checkout-index -f -u --stdin -z` to fail for a single malicious path while other paths in the same stdin batch have already been trashed would require local filesystem experimentation (e.g., crafting case/Unicode-colliding filenames on a case-insensitive volume) that wasn't completed here.

---
**Caveat:** The severity/likelihood of this analog is lower confidence than the original Noya report because I could not empirically confirm a concrete attacker-controlled path that reliably fails `git checkout-index` for only one entry in the batch. The code-level structural weakness (per-file trash + one atomic batched restore, all wrapped in a single failable operation) is confirmed directly from source, but the end-to-end exploitability depends on git/filesystem behavior I could not verify further given the tool budget.

### Citations

**File:** app/src/lib/stores/git-store.ts (L1555-1584)
```typescript
    for (const file of files) {
      const foundSubmodule = submodules.some(s => s.path === file.path)

      if (file.status.kind !== AppFileStatusKind.Deleted && !foundSubmodule) {
        if (moveToTrash) {
          try {
            await this.shell.moveItemToTrash(
              Path.resolve(this.repository.path, file.path)
            )
          } catch (e) {
            if (askForConfirmationOnDiscardChangesPermanently) {
              throw new DiscardChangesError(e, this.repository, files)
            }

            // The user has received the confirmation dialog in past and has
            // chosen to always discard the changes permanently if trash failes.
            // We need to remove the file manually.
            if (file.status.kind === AppFileStatusKind.Untracked) {
              await rm(Path.join(this.repository.path, file.path))
            }
          }
        } else if (moveToTrash === false) {
          // The user has received the confirmation dialog and has chosen to
          // discard the changes permanently. We need to remove the file
          // manually.
          if (file.status.kind === AppFileStatusKind.Untracked) {
            await rm(Path.join(this.repository.path, file.path))
          }
        }
      }
```

**File:** app/src/lib/stores/git-store.ts (L1636-1648)
```typescript
    await this.performFailableOperation(async () => {
      if (submodulePaths.length > 0) {
        await resetSubmodulePaths(this.repository, submodulePaths)
      }

      await resetPaths(
        this.repository,
        GitResetMode.Mixed,
        'HEAD',
        necessaryPathsToReset
      )
      await checkoutIndex(this.repository, necessaryPathsToCheckout)
    })
```

**File:** app/src/lib/git/checkout-index.ts (L21-39)
```typescript
export async function checkoutIndex(
  repository: Repository,
  paths: ReadonlyArray<string>
) {
  if (!paths.length) {
    return
  }

  const options = {
    successExitCodes: new Set([0, 1]),
    stdin: paths.join('\0'),
  }

  await git(
    ['checkout-index', '-f', '-u', '-q', '--stdin', '-z'],
    repository.path,
    'checkoutIndex',
    options
  )
```

**File:** app/src/lib/stores/app-store.ts (L5705-5718)
```typescript
    try {
      await gitStore.discardChanges(
        files,
        moveToTrash,
        askForConfirmationOnDiscardChangesPermanently
      )
    } catch (error) {
      if (!(error instanceof DiscardChangesError)) {
        log.error('Failed discarding changes', error)
      }

      this.emitError(error)
      return
    }
```
