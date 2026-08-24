### Title
Missing path-containment check in `discardChanges()` allows a malicious repository to trigger file deletion/move outside the working directory - (File: `app/src/lib/stores/git-store.ts`)

### Summary
`GitStore.discardChanges()` resolves the on-disk path for a file to discard by directly joining the repository root with the `path` value reported by `git status`, instead of using the repository's own path-containment helper (`resolveWithin`) that is already applied at several other untrusted-input boundaries in the codebase. [1](#0-0) 

### Finding Description
`git status --porcelain=2 -z` output is parsed by `parsePorcelainStatus`/`parseChangedEntry`/`parseUntrackedEntry`, which extract the `path` field with a permissive, non-validating regex (`[\s\S]*?`) and a raw `substring()` for untracked entries — the value is taken verbatim from whatever Git reports for the working tree, with no normalization or containment check. [2](#0-1) [3](#0-2) 

That `IStatusEntry.path` becomes `WorkingDirectoryFileChange.path`, which flows unmodified into `GitStore.discardChanges()`. There, the destination path is computed with `Path.resolve(this.repository.path, file.path)` (for `moveItemToTrash`) and `Path.join(this.repository.path, file.path)` (for direct `rm`) — with **no call to `resolveWithin`**, the containment helper that exists precisely for this purpose elsewhere in the app: [4](#0-3) 

Contrast this with `dispatcher.ts`, `copilot-conflict-context.ts`, and `app-store.ts`, which use `resolveWithin(repository.path, ...)` before touching the filesystem and explicitly reject paths that escape the repo root: [5](#0-4) [6](#0-5) 

`resolveWithin` performs `realpath` comparisons and explicitly guards against directory-traversal and symlink escapes, and the project's own tests document this as the sanctioned defense-in-depth boundary for repo-relative paths derived from external/attacker input: [7](#0-6) 

`discardChanges()` bypasses this boundary entirely, so any `IStatusEntry.path`/`WorkingDirectoryFileChange.path` value that resolves outside `repository.path` (e.g. via a working-tree entry name containing traversal sequences, or on Windows a name containing a literal backslash which `Path.resolve`/`Path.join` will interpret as a directory separator) is passed straight to `shell.moveItemToTrash` or `fs.rm`.

### Impact Explanation
If an attacker can get such a path to exist as an untracked/modified entry in the working tree of a cloned/checked-out repository (the app's own status parser makes no attempt to reject it), invoking "Discard Changes" on that entry causes the app to move-to-trash or permanently delete a file **outside the repository**, under the user's own privileges — this is the "file write/read (here: destructive write) outside the repo" impact category, triggered purely by opening/interacting with an attacker-supplied repository, with no additional privileged or local access required.

### Likelihood Explanation
This is a defense-in-depth gap rather than a proven end-to-end exploit: producing a working-tree path that both (a) `git status` will surface as changed/untracked and (b) resolves outside the repo root when joined with `Path.resolve`/`Path.join` typically requires an OS/filesystem or Git version quirk (e.g. Windows backslash-in-filename interpretation, or a symlinked working-tree directory) — modern Git has hardened `checkout`/`clone` against writing such names directly. The exposure is nonetheless real because, unlike every other path-join site touching repository-relative input, this one has no `resolveWithin` guard at all, so it is entirely dependent on upstream Git/OS protections rather than the app's own validation.

### Recommendation
Route every path derived from `WorkingDirectoryFileChange.path` / `file.status.oldPath` through `resolveWithin(this.repository.path, file.path)` (or `resolveWithinPosix` where Git paths are always POSIX-style) before calling `shell.moveItemToTrash` or `rm` in `GitStore.discardChanges()`, mirroring the pattern already used in `dispatcher.ts` and `app-store.ts`, and refuse to act on any entry whose resolved path is `null`.

### Proof of Concept
Conceptual (not confirmed to be exploitable end-to-end without a vulnerable Git/OS combination, per the Likelihood section above):
1. Obtain/produce a working tree in which a tracked or untracked entry's path, when read by `git status --porcelain=2 -z`, contains characters that `Path.resolve`/`Path.join` on the host OS treat as directory separators (e.g. a literal backslash on Windows), such that the joined path escapes `repository.path`.
2. Open the repository in Desktop; the Changes list will show this entry as a normal file change (the status parser performs no path validation, see `status-parser.ts`).
3. Select "Discard Changes" on that entry — `GitStore.discardChanges()` computes `Path.resolve(this.repository.path, file.path)` and calls `shell.moveItemToTrash`/`rm` on it, without the `resolveWithin` containment check used elsewhere in the codebase, deleting/moving a file outside the repository.

### Citations

**File:** app/src/lib/stores/git-store.ts (L1558-1582)
```typescript
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
```

**File:** app/src/lib/status-parser.ts (L101-119)
```typescript
// 1 <XY> <sub> <mH> <mI> <mW> <hH> <hI> <path>
const changedEntryRe =
  /^1 ([MADRCUTX?!.]{2}) (N\.\.\.|S[C.][M.][U.]) (\d+) (\d+) (\d+) ([a-f0-9]+) ([a-f0-9]+) ([\s\S]*?)$/

function parseChangedEntry(field: string): IStatusEntry {
  const match = changedEntryRe.exec(field)

  if (!match) {
    log.debug(`parseChangedEntry parse error: ${field}`)
    throw new Error(`Failed to parse status line for changed entry`)
  }

  return {
    kind: 'entry',
    statusCode: match[1],
    submoduleStatusCode: match[2],
    path: match[8],
  }
}
```

**File:** app/src/lib/status-parser.ts (L172-182)
```typescript
function parseUntrackedEntry(field: string): IStatusEntry {
  const path = field.substring(2)
  return {
    kind: 'entry',
    // NOTE: We return ?? instead of ? here to play nice with mapStatus,
    // might want to consider changing this (and mapStatus) in the future.
    statusCode: '??',
    submoduleStatusCode: '????',
    path,
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

**File:** app/test/unit/path-test.ts (L44-78)
```typescript
  describe('resolveWithin', () => {
    const root = process.cwd()

    it('fails for paths outside of the root', async () => {
      assert((await resolveWithin(root, join('..'))) === null)
      assert((await resolveWithin(root, join('..', '..'))) === null)
    })

    it('succeeds for paths that traverse out, and then back into, the root', async () => {
      assert.equal(await resolveWithin(root, join('..', basename(root))), root)
    })

    it('fails for paths containing null bytes', async () => {
      assert((await resolveWithin(root, 'foo\0bar')) === null)
    })

    it('succeeds for absolute relative paths as long as they stay within the root', async () => {
      const parent = resolve(root, '..')
      assert.equal(await resolveWithin(parent, root), root)
    })

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
