### Title
Discarded/trashed working-directory files are resolved without symlink/traversal validation, unlike sibling path-handling code - (File: `app/src/lib/stores/git-store.ts`)

### Summary
`GitStore.discardChanges` builds the on-disk path to delete or trash purely from the `file.path`/`file.status.oldPath` values that git status reports for a working tree that the attacker fully controls (a cloned/checked-out malicious repository), and passes that path straight to `shell.moveItemToTrash` / `rm` with no root-containment check. Every other place in this codebase that turns an attacker-influenced repo-relative path into a filesystem path (`app-store.ts` Copilot conflict writer, `copilot-conflict-context.ts` reader, `dispatcher.ts` `openRepositoryFromUrl`) explicitly calls `resolveWithin()`, which does a `realpath`-based check to reject paths that escape the repository root via `..` or symlinks. `discardChanges` is the one path-consuming operation omitted from that guard, despite being triggered by one of the most common user actions ("Discard Changes").

### Finding Description
`resolveWithin` in `app/src/lib/path.ts` was purpose-built as the project's canonical defense against "path traversal and symlink escapes (cross-platform)": [1](#0-0)  It is consistently applied wherever a repo-relative, attacker-influenced path is turned into an absolute filesystem path that's read or written:

- Copilot conflict resolution write: `app-store.ts` calls `resolveWithin(repository.path, resolution.path)` and bails if `null` before `writeFile`. [2](#0-1) 
- Copilot conflict context reader explicitly comments "Guard against path traversal and symlink escapes (cross-platform)". [3](#0-2) 
- `dispatcher.ts` `openRepositoryFromUrl` rejects absolute paths and calls `resolveWithin(repository.path, filepath)` before calling `shell.showItemInFolder`. [4](#0-3) 

In contrast, `GitStore.discardChanges` resolves the path with plain `Path.resolve`/`Path.join` and performs a destructive filesystem operation directly: [5](#0-4) 

The `file.path` / `file.status.oldPath` values originate from `git status` on the working directory of a repository the user just cloned or checked out — i.e., content fully controlled by whoever authored/pushed that repository. Nothing in the `discardChanges` code path calls `resolveWithin`, checks `isAbsolute`, or resolves symlinks before calling `shell.moveItemToTrash` (Electron's trash API) or `rm` (used for the "discard permanently" flow, `moveToTrash === false`, and as the untracked-file fallback when trashing fails). `docs/technical/discard-changes.md` documents this as an intentional, unaudited assumption ("Files moved to the Trash are moved over as-is") rather than a security boundary. [6](#0-5) 

### Impact Explanation
This is the same broken invariant as the report's `_debitFrom`: a destructive operation trusts a caller/attacker-supplied identifier (there, an NFT owner address; here, a repo-relative file path) without validating that the identifier actually resolves to something the operation is authorized to touch (an asset owned by the caller / a location inside the repository). If a crafted repository can cause git status to surface an entry whose resolved path (directly, or via a working-tree symlink) lies outside the repository root, then the ordinary "Discard Changes" action — invoked from `changes/sidebar.tsx` and `filter-changes-list.tsx` for arbitrary selected files — will permanently delete or trash a file outside the user's repository the moment they discard changes, without any additional confirmation identifying the true target path. This is a silent-corruption/arbitrary-file-deletion primitive triggered by an ordinary, expected workflow action on a hostile repository, matching the requested impact class (attacker controls a cloned repository; result is file write/deletion outside the repo).

### Likelihood Explanation
"Discard Changes" is one of the most frequently used features in Desktop and requires no unusual steps — a user clones or opens a malicious repository and discards a modified/untracked file, which is completely ordinary behavior. The codebase's own established pattern of guarding every comparable path-join operation with `resolveWithin` strongly suggests the omission in `discardChanges` is a genuine gap rather than an accepted risk, and the destructive nature of the call (delete/trash) means any successful traversal/symlink escape is immediately consequential and hard to detect (no user-facing indication of the actual resolved absolute path).

### Recommendation
Route `discardChanges` through the same `resolveWithin(repository.path, file.path)` (and for renames, `file.status.oldPath`) validation used elsewhere before calling `shell.moveItemToTrash` or `rm`, refusing to act (and logging/surfacing an error) when the resolved path is `null` or lies outside `repository.path`. This mirrors the recommendation in the source report: verify the operation's target is actually within the entity's authorized scope before performing an irreversible action on it.

### Proof of Concept
Not independently reproducible from static code alone: it depends on whether `git status`/the working tree can be made, on some platform/filesystem, to expose a discardable entry (untracked file or working-tree symlink) whose resolved path escapes the repository root (e.g., a working-tree symlink pointing outside the repo reported as an untracked/modified path, or platform-specific traversal in reported paths). I could not verify from the indexed code whether git's own status/porcelain output or Desktop's status parser (`app/src/lib/status-parser.ts`, not fully inspected here) filters out such entries before they reach `discardChanges`; this should be checked directly (e.g., by a Devin session with full repo/file access and the ability to run a working checkout) to confirm exploitability end-to-end. What is concretely verifiable in the index is the structural gap itself: `discardChanges` is the only attacker-path-consuming, destructive operation in the codebase that skips the `resolveWithin` guard applied everywhere else.

### Citations

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

**File:** app/src/lib/stores/app-store.ts (L7233-7239)
```typescript
      const absolutePath = await resolveWithin(repository.path, resolution.path)
      if (absolutePath === null) {
        log.warn(
          `Copilot resolution skipped: path outside repository: ${resolution.path}`
        )
        continue
      }
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

**File:** docs/technical/discard-changes.md (L15-24)
```markdown
### Moving Files to Trash

Electron provides the [`shell.trashItem(fullPath)`](https://www.electronjs.org/docs/api/shell#shelltrashitempath)
API to manage moving files into the OS-specific trash.

Desktop uses this API to move _all new or modified files_ out from the
repository as a way to preserve changes, in case the user wishes to recover
them later. Files moved to the Trash are moved over as-is, so ensure you have
the ability to view hidden files if you wish to recover files that are prefixed
with `.`.
```
