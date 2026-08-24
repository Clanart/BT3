## Finding: `discardChanges` builds destructive file paths without symlink/containment validation

### Title
Arbitrary File Deletion via Symlink Escape in `Discard Changes` - (File: `app/src/lib/stores/git-store.ts`)

### Summary
`GitStore.discardChanges` resolves the on-disk path for each file to be discarded by simply joining the repository root with the git-reported `file.path` and passing that straight to `shell.moveItemToTrash` / `rm`, without ever verifying that the resolved path is actually contained within the repository. The codebase already has a purpose-built primitive, `resolveWithin` in `app/src/lib/path.ts`, that performs `realpath`-based containment checks specifically to defeat symlink escapes, and it is used in other file-touching code paths (e.g. `buildConflictContext` in `app/src/lib/copilot-conflict-context.ts` and `openRepositoryFromUrl` in `app/src/ui/dispatcher/dispatcher.ts`). `discardChanges` does not use it.

### Finding Description
The broken invariant mirrors the report's pattern: a destructive operation trusts a value derived from external/attacker-influenced state (`file.path`, sourced from `git status` parsing of a cloned/fetched repository's working tree) instead of validating it against the trusted boundary (the repository root), even though the exact validation primitive (`resolveWithin`) exists and is used elsewhere. [1](#0-0) 

Here, `file.path` values originate from git status entries for a working directory that can contain a symlink checked directly into the repository content (a normal, valid git object — symlinks are a supported git blob mode). When Desktop reports changes for paths that traverse through such a symlink, `Path.resolve(this.repository.path, file.path)` produces a path string that is syntactically under the repo, but at the OS/filesystem level resolves through the symlink to an arbitrary location outside the repository. `Path.resolve` is purely lexical and does not detect this, unlike `resolveWithin`, which explicitly calls `realpath` on both the root and the resolved path and rejects anything that escapes: [2](#0-1) 

The project's own tests demonstrate exactly this attack class is expected to be blocked when `resolveWithin` is used: [3](#0-2) 

But `discardChanges` never routes through this guard before calling `moveItemToTrash` or `rm`: [4](#0-3) 

### Impact Explanation
A malicious or compromised repository that a user clones/fetches/opens in Desktop can contain a tracked symlink pointing outside the repo (e.g., to the user's home directory, `.ssh`, or an app data folder). If subsequent working-directory content under that symlinked path is reported as changed/untracked by `git status` (e.g. due to normal user activity, build tooling, or another crafted commit that later modifies the symlink target contents), and the user invokes "Discard Changes" (including the common "discard all changes" action), Desktop will call `shell.moveItemToTrash`/`rm` on a path that the OS resolves outside the repository boundary — resulting in unauthorized deletion (or trashing) of files outside the repo the user never intended to touch. This matches the "file write or read outside the repo" impact category from an unprivileged, attacker-controlled repository.

### Likelihood Explanation
Exploitation requires only that the victim clone/open a malicious repository and use the standard "Discard Changes" UI feature — no admin rights, no pre-existing malware, and no unnatural steps beyond normal Desktop usage on an untrusted repo. The existence of `resolveWithin` and its use elsewhere shows the maintainers are aware of and actively defend against this exact class of symlink escape, but `discardChanges` — one of the most destructive code paths in the app — was not updated to use it, indicating the guard is inconsistently applied rather than deliberately absent for a safety reason.

### Recommendation
In `GitStore.discardChanges` (`app/src/lib/stores/git-store.ts`), replace the direct `Path.resolve(this.repository.path, file.path)` / `Path.join(this.repository.path, file.path)` calls used before `moveItemToTrash` and `rm` with `resolveWithin(this.repository.path, file.path)`, and skip (or fail closed on) any file whose resolved path is `null` (i.e., escapes the repository root), consistent with how `buildConflictContext` and `openRepositoryFromUrl` already handle this.

### Proof of Concept
1. Attacker creates a repository containing a tracked symlink `evil` pointing to an absolute path outside the repo (e.g. the victim's home directory or a shared/writable location), and commits it.
2. Victim clones the repository in GitHub Desktop; the symlink `evil` is checked out on disk pointing outside the repo root.
3. Content under the target of `evil` changes (via a later crafted commit changing the symlink target's underlying data, a build script, or other repo-provided tooling the attacker convinces the victim to run inside the folder) such that `git status` reports a changed/untracked path like `evil/some-file`.
4. Victim opens the Changes list in Desktop and clicks "Discard changes" (or "Discard all changes").
5. `GitStore.discardChanges` resolves `Path.resolve(repoPath, "evil/some-file")`, which the OS follows through the `evil` symlink, and calls `shell.moveItemToTrash`/`rm` on a file physically located outside the cloned repository, deleting/trashing user data the victim never intended to affect.

### Citations

**File:** app/src/lib/stores/git-store.ts (L1550-1583)
```typescript
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
