Based on exhaustive searching across the Desktop codebase, I could not find a solid, code-supported analog that maps directly onto the "double execution of a supposedly-once action due to a mutable-state bypass" pattern from the Solidity report (Desktop simply doesn't have an equivalent "already launched" state machine gated by a value the app itself later changes). The closest structurally similar risk is a different bug class — a missing containment check on an attacker-influenced path — which does have direct file/line support in the repo.

### Title
Discard Changes trashes files using unvalidated status paths, bypassing the repo's own symlink-containment guard - ([File: app/src/lib/stores/git-store.ts])

### Summary
`GitStore.discardChanges` resolves the on-disk location of a file to discard with a bare `Path.resolve`/`Path.join` against the repository root, instead of the codebase's dedicated `resolveWithin` helper that is specifically designed (and tested) to reject paths that escape the repository root via a symlinked path component.

### Finding Description
`discardChanges` computes the file to move to trash (or delete) directly from the `file.path` reported by `git status`, without ever validating that the resolved location actually stays inside the repository: [1](#0-0) 

Compare this to the file-open path in the URL/deep-link handler, which explicitly guards against exactly this class of escape by calling `resolveWithin` and refusing to proceed if the resolved path leaves the repository: [2](#0-1) 

`resolveWithin` itself is written, and unit-tested, to catch the case where a path component is a symlink that redirects resolution outside of the root directory: [3](#0-2) [4](#0-3) 

`discardChanges`, however, does not reuse this helper at all — it trusts that `file.path` (sourced from `git status --porcelain -z` parsing) always stays within the repository tree, and passes the resolved path straight into `shell.moveItemToTrash` (or `rm`) for both tracked-modified and untracked files: [5](#0-4) 

### Impact Explanation
If a status entry's `path` can ever resolve (via a symlinked ancestor directory that is part of the checked-out working tree) to a location outside the repository, "Discard Changes" would delete or move-to-trash a file the user never intended to touch, entirely outside the repo — this is a silent, attacker-directed file deletion/write-outside-repo primitive, distinct from ordinary git operations that Desktop otherwise carefully sandboxes (as shown by the `resolveWithin` usage elsewhere).

### Likelihood Explanation
This is Medium-to-Low likelihood in practice: current versions of Git apply their own protections against tree structures that place a tracked path underneath a symlinked component during checkout, which narrows (but doesn't provably eliminate, e.g. via untracked/ignored-directory symlinks or racing filesystem changes) the ways an attacker-controlled clone could produce a `git status` path that traverses outside the repo root. Because this depends on Git's own checkout-time protections rather than a Desktop-side check, I cannot fully verify from the code alone whether a bypass exists today.

### Recommendation
Route the resolved discard/trash path for every file in `discardChanges` (and the analogous `rm` calls for untracked files) through `resolveWithin` (or `resolveWithinPosix`/`resolveWithinWin32` as appropriate), refusing the operation and surfacing an error if the resolved path does not stay within `repository.path`, mirroring the existing protection used for `openRepositoryFromUrl`'s `filepath` handling.

### Proof of Concept
Not fully constructible from static code review alone — exploitability hinges on whether a working-tree state exists (e.g., an ignored/untracked symlinked directory checked out inside the repo) that produces a `git status` entry whose `path` resolves outside `repository.path` despite Git's own tree-checkout protections. I was not able to confirm this bypass with the tools available and am flagging the missing `resolveWithin` usage as a defense-in-depth gap rather than a confirmed exploitable path.

### Citations

**File:** app/src/lib/stores/git-store.ts (L1545-1584)
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
```

**File:** app/src/ui/dispatcher/dispatcher.ts (L1957-1972)
```typescript
    if (filepath !== null) {
      if (isAbsolute(filepath)) {
        log.error(`Refusing to open absolute path: ${filepath}`)
        return
      }

      const resolved = await resolveWithin(repository.path, filepath)

      if (resolved !== null) {
        shell.showItemInFolder(resolved)
      } else {
        log.error(
          `Prevented attempt to open path outside of the repository root: ${filepath}`
        )
      }
    }
```

**File:** app/src/lib/path.ts (L36-72)
```typescript
async function _resolveWithin(
  rootPath: string,
  pathSegments: string[],
  options: {
    join: (...pathSegments: string[]) => string
    normalize: (p: string) => string
    resolve: (...pathSegments: string[]) => string
  } = Path
) {
  // An empty root path would let all relative
  // paths through.
  if (rootPath.length === 0) {
    return null
  }

  const { join, normalize, resolve } = options

  const normalizedRoot = normalize(rootPath)
  const normalizedRelative = normalize(join(...pathSegments))

  // Null bytes has no place in paths.
  if (
    normalizedRoot.indexOf('\0') !== -1 ||
    normalizedRelative.indexOf('\0') !== -1
  ) {
    return null
  }

  // Resolve to an absolute path. Note that this will not contain
  // any directory traversal segments.
  const resolved = resolve(normalizedRoot, normalizedRelative)

  const realRoot = await realpath(normalizedRoot)
  const realResolved = await realpath(resolved)

  return realResolved.startsWith(realRoot) ? resolved : null
}
```

**File:** app/test/unit/path-test.ts (L65-78)
```typescript
    if (!__WIN32__) {
      it('fails for paths that use a symlink to traverse outside of the root', async () => {
        const tempDir = await mkdtemp(join(tmpdir(), 'path-test'))
        const symlinkName = 'dangerzone'
        const symlinkPath = join(tempDir, symlinkName)

        try {
          await symlink(resolve(tempDir, '..', '..'), symlinkPath)
          assert((await resolveWithin(tempDir, symlinkName)) === null)
        } finally {
          await unlink(symlinkPath)
          await rmdir(tempDir)
        }
      })
```
