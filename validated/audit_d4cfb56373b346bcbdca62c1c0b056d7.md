Found it: `git-store.ts` `discardChanges()` calls `Path.resolve(this.repository.path, file.path)` / `Path.join(this.repository.path, file.path)` for every file returned by `git status`, with no `resolveWithin`-style containment check — unlike the guarded call sites (`copilot-conflict-context.ts`, `dispatcher.ts` `openRepositoryFromUrl`) that this codebase has already hardened against exactly this class of bug.

### Title
Path traversal via crafted `git status` path in `discardChanges()` leads to file deletion/overwrite outside the repository - (File: app/src/lib/stores/git-store.ts)

### Summary
`discardChanges()` trusts the `path` field parsed from `git status --porcelain=v2 -z` output and joins it directly onto the repository root with `Path.resolve`/`Path.join`, without validating that the result stays inside the working directory. This mirrors the CometBFT bug class: an attacker-influenced field (there, `LastCommit` round; here, the status `path` field) is consumed without a bound/containment check, corrupting an invariant the app relies on ("all working-directory file operations happen inside the repo").

### Finding Description
`_discardChanges` in `git-store.ts` builds the on-disk target path like this: [1](#0-0) 
Note `Path.resolve(this.repository.path, file.path)` and `Path.join(this.repository.path, file.path)` — no call to `resolveWithin`/`resolveWithinPosix` (the helper this codebase already uses elsewhere to contain untrusted paths): [2](#0-1) 

Compare to the two places where the same class of risk was already fixed: `copilot-conflict-context.ts`, which explicitly guards "path traversal and symlink escapes" before reading a file path derived from a diff/conflict list, [3](#0-2) 
and `dispatcher.ts`'s `openRepositoryFromUrl`, which refuses absolute paths and calls `resolveWithin` before doing anything with a filepath that ultimately comes from a URL/deep link: [4](#0-3) 

`discardChanges()` has no equivalent guard even though its input (`WorkingDirectoryFileChange.path`) is derived straight from Git's status output, which is influenceable by anything that can affect the index/working tree of a cloned/fetched repository (e.g., a crafted tree entry, a case-insensitive filesystem collision, or `core.protectNTFS`/`core.fsmonitor` edge cases that make Git report an unexpected path string, or simply a `.gitattributes`/rename entry with `../` sequences that Desktop's own status parser does not reject). The status parser itself is permissive by design — it explicitly supports multi-line/arbitrary-content paths: [5](#0-4) 

If such a path reaches `discardChanges`, `Path.resolve(this.repository.path, file.path)` can escape the repository root, and the code either:
- moves an arbitrary out-of-repo file to the trash (`this.shell.moveItemToTrash(...)`), or
- for untracked files, unlinks it with `rm(Path.join(this.repository.path, file.path))`.

Existing guards elsewhere in the code (`resolveWithin`, `sanitizeCloneName`, the "unsafe repository" ownership check) show the maintainers are actively closing this exact bug class for clone/checkout/open flows, but `discardChanges` was not brought under the same containment check.

### Impact Explanation
If reachable with a `..`-containing (or absolute) `file.path`, this allows deletion or trashing of an arbitrary file outside the repository the unprivileged user did not intend to touch, triggered purely by opening/interacting with a malicious repository's Changes view and clicking "Discard changes." This is a file-write/delete-outside-repo primitive, not merely a crash — squarely in scope per the Valid Impact section (attacker controls a cloned/fetched repository → file write/delete outside the repo).

### Likelihood Explanation
Moderate-to-low: it requires Desktop's status parser/Git to actually surface a path string containing traversal sequences to the UI's `WorkingDirectoryFileChange` list, which normal Git behavior on well-formed repos does not produce, and Desktop does have other layers (Git itself normalizes many paths, `.git` internal protections) that reduce direct exploitability. This is exactly the same "requires a knowledgeable adversary but the containment check is legitimately missing" profile as the reported CometBFT issue.

### Recommendation
Route every path derived from working-directory/status data through `resolveWithin`/`resolveWithinPosix` before it is used in `moveItemToTrash`, `rm`, or any other filesystem-mutating call in `discardChanges()`/`_discardChanges()`, consistent with the pattern already applied in `copilot-conflict-context.ts` and `dispatcher.ts`. Reject (and log) any file whose resolved path is not contained within `repository.path`.

### Proof of Concept
1. Obtain (clone) a repository whose working tree/index causes Git status to report a file entry whose `path` field, once parsed by Desktop's `status-parser.ts`, is not a plain repo-relative filename (e.g., a rename/copy entry or a path field containing `../` sequences that the permissive multi-line-capable regex in `parseChangedEntry`/`parsedRenamedOrCopiedEntry` does not reject).
2. Open the repository in Desktop; the crafted entry shows up as a change in the Changes list.
3. Select "Discard changes" on that file. `discardChanges()` in `git-store.ts` calls `Path.resolve(this.repository.path, file.path)` / `Path.join(...)` with the unvalidated path and passes it to `shell.moveItemToTrash` or `rm`.
4. Because there is no `resolveWithin` containment check on this path (unlike the guarded call sites), a resolved path outside `repository.path` is trashed/deleted without any warning to the user.

I was not able to fully confirm, within the indexed code available, an end-to-end concrete Git status output that would make `status-parser.ts` emit a `path` containing `../` for a legitimate `git status --porcelain=v2 -z` invocation — Git itself normally quotes/escapes such paths — so this should be validated by a Devin session with full repo/CLI access before treating it as a confirmed exploitable path traversal, rather than a defense-in-depth gap. The missing containment check on the destructive path itself, however, is directly confirmed in the code shown above.

### Citations

**File:** app/src/lib/stores/git-store.ts (L1558-1583)
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

**File:** app/src/lib/copilot-conflict-context.ts (L390-407)
```typescript
      // Guard against path traversal and symlink escapes (cross-platform)
      let absolutePath: string | null
      try {
        absolutePath = await resolveWithin(workingDirectory, file.path)
      } catch {
        return {
          path: file.path,
          hunks: [],
          skippedReason: 'File path could not be resolved safely',
        }
      }
      if (absolutePath === null) {
        return {
          path: file.path,
          hunks: [],
          skippedReason: 'File path is outside the repository',
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
