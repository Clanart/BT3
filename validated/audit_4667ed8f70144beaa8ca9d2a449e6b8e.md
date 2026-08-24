### Title
Discard/checkout of tracked paths accepts exit code 1 from `git checkout-index` as unconditional success, allowing failed reverts to be silently treated as completed - (File: `app/src/lib/git/checkout-index.ts`)

### Summary
`checkoutIndex()` shells out to `git checkout-index -f -u -q --stdin -z` and configures `successExitCodes: new Set([0, 1])`, meaning any exit code of `0` or `1` is treated by the shared `git()` wrapper as a successful result, with no inspection of `stdout`/`stderr` to confirm which of the requested paths were actually restored. [1](#0-0) 

### Finding Description
The `git()` core wrapper only throws a `GitError` when the exit code is outside `successExitCodes` and the parsed error isn't in `expectedErrors`; any exit code inside `successExitCodes` is returned to the caller as a normal, successful `IGitResult` with no further validation of its content. [2](#0-1) 

`checkoutIndex()` deliberately widens the acceptable exit codes to `[0, 1]` with the stated rationale that `-q` "will not yield errors for paths that don't exist in the index", but `git checkout-index` also returns a non-zero exit status for other failure classes (e.g. a target file that cannot be written because it's locked, read-only, or the disk is full) that are unrelated to "path missing from index." Because the caller never inspects `result.stderr`/`result.stdout` or per-path outcomes, there is no way to distinguish "some paths didn't exist in the index (benign)" from "some paths existed but failed to be overwritten (real failure)" - both collapse into the same accepted "success" result. [3](#0-2) 

This function is the mechanism used to restore the on-disk contents of files during Discard Changes: `GitStore.discardChanges()` moves modified files to trash and then calls `checkoutPaths` (built on the same `checkoutIndex` primitive) to force-write the index's version of those paths back into the working directory. [4](#0-3) [5](#0-4) 

If `checkout-index` returns exit code 1 because one or more targeted files genuinely failed to be rewritten (as opposed to simply not existing in the index), `checkoutIndex()`/`checkoutPaths()` resolve normally, `discardChanges()` proceeds as if the operation fully succeeded, and `AppStore._discardChanges()` immediately calls `_refreshRepository()` assuming a clean discard. [6](#0-5) 

### Impact Explanation
The user's original (attacker-influenced) working-directory content for the affected file(s) remains on disk even though the trashed original was removed and the UI reports the discard as complete. If those bytes originated from an untrusted source (e.g. a file introduced through a malicious merge/checkout, a crafted patch, or a hostile fetch result the user was trying to discard), the user is left believing the working tree matches HEAD when it does not, and any subsequent `git add`/commit/push in that session can silently re-stage and publish the discarded/untrusted content - a silent corruption of what the user actually intends to commit or push, matching the "transfer result not validated" bug class from the seed report (a state-changing operation's result is accepted without checking whether it actually achieved the intended effect).

### Likelihood Explanation
Requires no elevated privileges: any condition that makes `git checkout-index` return exit code 1 for a reason other than "path absent from index" (a genuinely locked/permission-denied/disk-full file, or another platform-specific checkout failure) is sufficient to trigger the gap. This is a narrower, environment-dependent trigger rather than a universally reproducible one, since the specific error condition causing exit code 1 for an existing path (rather than exit 0 or a fatal >1 code) needs to be confirmed against the exact `checkout-index` behavior in the dugite/git version bundled with Desktop - I was not able to verify from the local code alone which non-zero-but-accepted scenarios are reachable in practice versus only the documented "-q suppresses missing-path errors" case.

### Recommendation
After calling `checkout-index`, inspect `result.stderr` for lines indicating real failures for paths that are known to exist in the index (as opposed to the expected "does not exist" case for `-q`), and surface/throw when such lines are present instead of unconditionally accepting exit code 1 as success.

### Proof of Concept
Not independently reproducible from static analysis alone; verifying it requires exercising `git checkout-index -f -u -q --stdin -z` against a path that exists in the index but cannot be written (e.g. file held open/locked, made read-only, or a full disk) and confirming it returns exit code 1, then confirming `checkoutIndex()`/`discardChanges()` return successfully and the file's untrusted contents remain unchanged on disk while the app reports the discard as complete. [1](#0-0)

### Citations

**File:** app/src/lib/git/checkout-index.ts (L12-20)
```typescript
 * Note that this function will not yield errors for paths that don't
 * exist in the index (-q).
 *
 * @param repository The repository in which to update the working directory
 *                   with information from the index
 *
 * @param paths      The relative paths in the working directory to update
 *                   with information from the index.
 */
```

**File:** app/src/lib/git/checkout-index.ts (L21-40)
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
}
```

**File:** app/src/lib/git/core.ts (L322-353)
```typescript
          const exitCode = result.exitCode

          let gitError: DugiteError | null = null
          const acceptableExitCode = opts.successExitCodes
            ? opts.successExitCodes.has(exitCode)
            : false
          if (!acceptableExitCode) {
            gitError = parseError(coerceToString(result.stderr))
            if (gitError === null) {
              gitError = parseError(coerceToString(result.stdout))
            }
          }

          const gitErrorDescription =
            gitError !== null
              ? getDescriptionForError(gitError, coerceToString(result.stderr))
              : null
          const gitResult = {
            ...result,
            gitError,
            gitErrorDescription,
            path,
          }

          let acceptableError = true
          if (gitError !== null && opts.expectedErrors) {
            acceptableError = opts.expectedErrors.has(gitError)
          }

          if ((gitError !== null && acceptableError) || acceptableExitCode) {
            return gitResult
          }
```

**File:** app/src/lib/stores/git-store.ts (L1545-1601)
```typescript
  public async discardChanges(
    files: ReadonlyArray<WorkingDirectoryFileChange>,
    moveToTrash: boolean = true,
    askForConfirmationOnDiscardChangesPermanently: boolean = false
  ): Promise<void> {
    const pathsToCheckout = new Array<string>()
    const pathsToReset = new Array<string>()

    const submodules = await listSubmodules(this.repository)

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

      if (
        file.status.kind === AppFileStatusKind.Copied ||
        file.status.kind === AppFileStatusKind.Renamed
      ) {
        // file.path is the "destination" or "new" file in a copy or rename.
        // we've already deleted it so all we need to do is make sure the
        // index forgets about it.
        pathsToReset.push(file.path)

        // checkout the old path too
        pathsToCheckout.push(file.status.oldPath)
        pathsToReset.push(file.status.oldPath)
      } else {
        pathsToCheckout.push(file.path)
        pathsToReset.push(file.path)
      }
```

**File:** docs/technical/discard-changes.md (L35-41)
```markdown
### Checkout Paths

The last step is to replace the modified files in the working directory with
whatever is currently in the index - this ensures that Desktop only replaces
files that the user has chosen to discard.

**Git CLI equivalent**: `git checkout-index -f -u -- [path]`
```

**File:** app/src/lib/stores/app-store.ts (L5696-5721)
```typescript
  public async _discardChanges(
    repository: Repository,
    files: ReadonlyArray<WorkingDirectoryFileChange>,
    moveToTrash: boolean = true
  ) {
    const gitStore = this.gitStoreCache.get(repository)

    const { askForConfirmationOnDiscardChangesPermanently } = this.getState()

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

    return this._refreshRepository(repository)
  }
```
