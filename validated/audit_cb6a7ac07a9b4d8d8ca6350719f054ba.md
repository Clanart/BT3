Given the extensive hardening already present in this fork (clone-path traversal blocked via `isClonePathSensitive` in `app/src/lib/git/clone.ts`, symlink/traversal-aware `resolveWithin()` used for deep-link file paths in `app/src/ui/dispatcher/dispatcher.ts`, sandboxed hook execution via `app/src/lib/hooks/hooks-proxy.ts`, credential/proxy isolation in `app/src/lib/git/environment.ts` and `app/src/lib/trampoline/`), most classic Desktop attack surfaces for this bug class are already patched. I did find one asymmetry worth flagging, though I was not able to fully confirm the OS-level trash behavior before running out of tool calls.

### Title
Discard Changes resolves attacker-controlled `git status` paths with plain `Path.resolve`/`Path.join` instead of the symlink-aware `resolveWithin()` guard used elsewhere - (File: app/src/lib/stores/git-store.ts)

### Summary
`GitStore.discardChanges()` builds the on-disk path to delete/trash directly from the `file.path` string returned by `git status --porcelain=v2 -z`, using `Path.resolve`/`Path.join`, with no realpath/symlink containment check. Elsewhere in the same codebase (e.g. deep-link file opening in the dispatcher), Desktop deliberately routes user/repo-supplied relative paths through `resolveWithin()`, which resolves symlinks via `fs.realpath` and rejects any result that escapes the repository root. That guard is absent from the discard-changes code path.

### Finding Description
`discardChanges` in [1](#0-0)  iterates over `WorkingDirectoryFileChange` objects (whose `.path` comes straight from `IStatusEntry.path`, itself parsed with no sanitization beyond NUL-splitting in [2](#0-1) ) and calls:
- `this.shell.moveItemToTrash(Path.resolve(this.repository.path, file.path))` for the trash-based flow, and
- `rm(Path.join(this.repository.path, file.path))` for the permanent-delete fallback.

Both use plain path concatenation. Compare this to the deliberate hardening already present for the deep-link "open file" flow in [3](#0-2) , which explicitly calls `resolveWithin(repository.path, filepath)` — implemented in [4](#0-3)  — to resolve symlinks via `realpath` and reject paths that escape the repository root, with dedicated tests proving it defeats symlink traversal in [5](#0-4) .

Discard Changes never uses this helper. Any commit in a cloned/fetched repository can introduce a tracked filesystem object (a symlink blob, mode `120000`) at some path; if the user subsequently interacts with that entry so `git status` reports it as modified/new/untracked, `discardChanges` will hand the *exact* attacker-chosen relative path straight to the OS-level "move to trash" primitive without ever confirming that the resolved path stays inside the repository.

### Impact Explanation
If the underlying `moveItemToTrash` implementation (Electron's `shell.trashItem`, wired up in `app/src/lib/app-shell.ts`) does not itself perform symlink-safe resolution — a real, historically documented pitfall on Windows where the shell's recycle-bin operation can traverse into a directory junction/symlink and act on its target rather than the link node — then a user who clicks "Discard Changes" on such a file could have an arbitrary file/directory outside the repository trashed or removed. This matches the requested impact class ("file write [or destructive modification] outside the repo... via a cloned/fetched repository the attacker controls").

### Likelihood Explanation
Medium-low. It requires: (1) the victim to open/clone an attacker-authored repository, (2) the repository (or a subsequent fetch/pull) to introduce a symlink entry at a path the victim will interact with, and (3) the victim to explicitly discard changes to that entry. This is a normal, low-friction Desktop workflow (Desktop actively encourages "Discard Changes" for unwanted/untracked files), so no unusual/unnatural steps are required beyond normal repo usage — but I could not confirm from static reading whether Electron's `shell.trashItem` on the target platform actually dereferences directory symlinks/junctions when trashing, which is the crux of exploitability.

### Recommendation
Route `discardChanges`'s trash/reset/delete path resolution through the existing `resolveWithin()` helper (as already done for deep-link file paths in `dispatcher.ts`) before calling `shell.moveItemToTrash` or `rm`, and refuse to act on any file whose realpath is not confined to the repository working directory. Additionally, treat any working-directory entry whose git file mode is `120000` (symlink) with extra scrutiny before passing it to destructive OS-level primitives.

### Proof of Concept
Conceptual PoC (unverified against the live `app-shell.ts` implementation due to no remaining tool budget):
1. Attacker creates a public repo containing a commit that adds a tracked symlink `evil-link` → an absolute path the victim is likely to have write access to (e.g. a Windows junction to `%USERPROFILE%\Documents` or similar).
2. Victim clones the repo in Desktop and, in a later commit/fetch, `evil-link`'s target changes (or the victim edits something that causes `git status` to show `evil-link` as Modified/New).
3. Victim selects `evil-link` in the Changes list and clicks "Discard Changes".
4. `GitStore.discardChanges()` resolves `Path.resolve(repoPath, 'evil-link')` and calls `shell.moveItemToTrash(...)` on it; depending on the OS trash implementation's symlink handling, this can operate on/through the link's target rather than the link node itself, discarding data outside the cloned repository.

I was unable to inspect `app/src/lib/app-shell.ts`'s exact `moveItemToTrash` implementation before running out of tool calls, so I cannot confirm with certainty whether it dereferences symlinks on this specific Electron/OS combination — this should be verified in a live Devin session (e.g., by writing a unit/integration test that stages a symlinked directory, runs `discardChanges`, and inspects whether the symlink target's contents were affected) before treating this as a confirmed, exploitable vulnerability rather than a hardening gap.

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

**File:** app/src/lib/status-parser.ts (L54-99)
```typescript
/** Parses output from git status --porcelain -z into file status entries */
export function parsePorcelainStatus(
  output: Buffer
): ReadonlyArray<StatusItem> {
  const entries = new Array<StatusItem>()

  // See https://git-scm.com/docs/git-status
  //
  // In the short-format, the status of each path is shown as
  // XY PATH1 -> PATH2
  //
  // There is also an alternate -z format recommended for machine parsing. In that
  // format, the status field is the same, but some other things change. First,
  // the -> is omitted from rename entries and the field order is reversed (e.g
  // from -> to becomes to from). Second, a NUL (ASCII 0) follows each filename,
  // replacing space as a field separator and the terminating newline (but a space
  // still separates the status field from the first filename). Third, filenames
  // containing special characters are not specially formatted; no quoting or
  // backslash-escaping is performed.

  const tokens = splitBuffer(output, '\0')

  for (let i = 0; i < tokens.length; i++) {
    const field = tokens[i].toString()
    if (field.startsWith('# ') && field.length > 2) {
      entries.push({ kind: 'header', value: field.substring(2) })
      continue
    }

    const entryKind = field.substring(0, 1)

    if (entryKind === ChangedEntryType) {
      entries.push(parseChangedEntry(field))
    } else if (entryKind === RenamedOrCopiedEntryType) {
      entries.push(parsedRenamedOrCopiedEntry(field, tokens[++i].toString()))
    } else if (entryKind === UnmergedEntryType) {
      entries.push(parseUnmergedEntry(field))
    } else if (entryKind === UntrackedEntryType) {
      entries.push(parseUntrackedEntry(field))
    } else if (entryKind === IgnoredEntryType) {
      // Ignored, we don't care about these for now
    }
  }

  return entries
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

**File:** app/src/lib/path.ts (L36-71)
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
