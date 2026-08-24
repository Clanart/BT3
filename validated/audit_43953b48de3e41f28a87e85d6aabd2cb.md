## Analog Found [1](#0-0) 

### Title
Once-Trusted Repository Paths Can Never Be Revoked From `safe.directory` — Permanent Bypass of Git's Unsafe-Ownership Protection - (File: `app/src/lib/git/config.ts`)

### Summary
Git's `safe.directory` mechanism exists specifically to stop repository configuration (hooks, `core.fsmonitor`, `core.hooksPath`, `credential.helper`, etc.) from executing when a repository is owned by a different, untrusted user — exactly the class of primitive an attacker who controls a cloned/fetched repository could otherwise abuse. GitHub Desktop implements one-way trust: `addSafeDirectory()` permanently appends a path to the global `safe.directory` git-config list, but there is no corresponding removal function anywhere in the codebase. This is the direct Desktop analog of the Bond Protocol finding: a whitelist (`approvedMarkets` / `safe.directory`) that can be added to but never revoked, so once trust is granted it can't be swiftly withdrawn if the trusted subject later becomes hostile.

### Finding Description
When Desktop detects a repository whose directory is owned by a different OS user, `getRepositoryType()` reports it as `{ kind: 'unsafe', path }` [2](#0-1) . Desktop then prompts the user to "Trust Repository," which calls `addSafeDirectory(path)` from both `MissingRepository` and `AddExistingRepository` [3](#0-2) [4](#0-3) .

`addSafeDirectory` writes the path into the user's **global** `~/.gitconfig` `safe.directory` multi-value list via `addGlobalConfigValueIfMissing`, and this entry is never cleaned up: [5](#0-4) 

```
export async function addSafeDirectory(path: string) {
  ...
  await addGlobalConfigValueIfMissing('safe.directory', path)
}
```

A grep across the codebase confirms there is no `removeSafeDirectory`, no "untrust", and no UI (Preferences or otherwise) that lets a user later revoke a path from `safe.directory` — the only mutation path found is the additive one in `config.ts`. Once a path is trusted, that trust is permanent for the life of the user's global git config, regardless of what later happens to the content living at that path.

This matters because `safe.directory` is Git's boundary against a directory whose ownership/contents are not trusted running arbitrary configuration when Desktop (or the user) runs git in it. Realistic attacker-controlled scenarios where this boundary is bypassed permanently include:
- A path is trusted once (e.g., a shared drive, container mount, CI workspace, or a directory later reassigned to another user/process), then subsequently repopulated by an attacker with a malicious `.git/config` (`core.hooksPath`, `core.fsmonitor`, hooks, `credential.helper`) — Desktop will silently treat it as safe forever because the stale allow-list entry can never be removed by the user through the app.
- Removing the repository from Desktop's UI (`_removeRepository`) only removes Desktop's local bookkeeping entry [6](#0-5) ; it does not touch `safe.directory`, so the path-level trust silently outlives the repository record itself.

### Impact Explanation
An attacker who can eventually write to a path that was once whitelisted (e.g. reused build directories, shared/removable volumes, container/VM disk reuse, or directory recycling after a repository was removed and its path handed to another purpose) can plant a malicious `.git` configuration that will execute with the user's privileges the next time Desktop (or git) operates in that directory — because Desktop's own `safe.directory` opt-in has no expiry and no user-facing revocation path. This is functionally identical to the Bond Protocol issue: the owner (user) has no way to swiftly withdraw trust from an entity (path) once it turns hostile, defeating the entire purpose of the allow-list as an incident-response control.

### Likelihood Explanation
Moderate. It requires a scenario where a previously-trusted path's ownership/content changes hands to an attacker (directory/path reuse, shared machines, CI runners with recycled workspaces, or removable/network drives), which is a realistic but not everyday occurrence. The core defect — irreversibility of the trust decision — is unconditionally present and independent of any particular reuse scenario; it's the underlying architectural gap that removes the app's ability to ever respond once such a scenario materializes.

### Recommendation
Add a `removeSafeDirectory(path)` counterpart to `addSafeDirectory` in `app/src/lib/git/config.ts` (using `git config --global --unset-all safe.directory <path>` semantics), and surface a way for users to review and revoke previously trusted directories — e.g. a "Trusted directories" list in Preferences, and automatically offer/perform revocation when a repository is removed via `_removeRepository` in `app/src/lib/stores/app-store.ts`, so trust doesn't outlive the repository record it was granted for.

### Proof of Concept
1. Add an existing repository at path `P` owned by another user; Desktop shows the "unsafe" warning and the user clicks "Trust Repository," triggering `addSafeDirectory(P)` [4](#0-3) . `P` is now permanently listed in the global `safe.directory` config.
2. Remove the repository from Desktop via "Remove" (`_removeRepository`) — this deletes Desktop's bookkeeping but leaves the global `safe.directory` entry for `P` untouched [6](#0-5) .
3. Later, path `P` is reused (e.g., recycled CI workspace, shared volume, or directory recreated by another actor) and now contains a malicious `.git/config` (e.g. `core.hooksPath` pointing to an attacker script, or a malicious `credential.helper`).
4. Because `P` still appears in `safe.directory` and Desktop/Git has no mechanism to un-trust it, `getRepositoryType(P)` reports `regular` (not `unsafe`) [7](#0-6) , so Desktop happily runs git operations in `P`, executing the attacker's configured hooks/credential helper with the user's privileges — with no in-app way for the user to have prevented this by revoking the earlier trust decision.

### Citations

**File:** app/src/lib/git/config.ts (L176-206)
```typescript
/**
 * Adds a path to the `safe.directories` configuration variable if it's not
 * already present. Adding a path to `safe.directory` will cause Git to ignore
 * if the path is owner by a different user than the current.
 */
export async function addSafeDirectory(path: string) {
  // UNC-paths on Windows need to be prefixed with `%(prefix)/`, see
  // https://github.com/git-for-windows/git/commit/e394a16023cbb62784e380f70ad8a833fb960d68
  if (__WIN32__ && path[0] === '/') {
    path = `%(prefix)/${path}`
  }

  await addGlobalConfigValueIfMissing('safe.directory', path)
}

/** Set the global config value by name. */
export async function addGlobalConfigValueIfMissing(
  name: string,
  value: string
): Promise<void> {
  const { stdout, exitCode } = await git(
    ['config', '--global', '-z', '--get-all', name, value],
    __dirname,
    'addGlobalConfigValue',
    { successExitCodes: new Set([0, 1]) }
  )

  if (exitCode === 1 || !stdout.split('\0').includes(value)) {
    await addGlobalConfigValue(name, value)
  }
}
```

**File:** app/src/lib/git/rev-parse.ts (L44-54)
```typescript
      if (match) {
        const [, isBare, cdup, gitDir] = match

        return isBare === 'true'
          ? { kind: 'bare' }
          : {
              kind: 'regular',
              topLevelWorkingDirectory: resolve(path, cdup),
              gitDir: resolve(path, gitDir),
            }
      }
```

**File:** app/src/lib/git/rev-parse.ts (L57-63)
```typescript
    const unsafeMatch =
      /fatal: detected dubious ownership in repository at '(.+)'/.exec(
        result.stderr
      )
    if (unsafeMatch) {
      return { kind: 'unsafe', path: unsafeMatch[1] }
    }
```

**File:** app/src/ui/missing-repository.tsx (L35-50)
```typescript
  private onTrustDirectory = async () => {
    this.setState({ isTrustingPath: true })
    const { unsafePath } = this.state
    const { repository } = this.props

    if (unsafePath) {
      await addSafeDirectory(unsafePath)
      const type = await getRepositoryType(repository.path)

      this.setState({ isTrustingPath: false })

      if (type.kind !== 'unsafe') {
        this.checkAgain()
      }
    }
  }
```

**File:** app/src/ui/add-repository/add-existing-repository.tsx (L69-77)
```typescript
  private onTrustDirectory = async () => {
    this.setState({ isTrustingRepository: true })
    const { repositoryUnsafePath, path } = this.state
    if (repositoryUnsafePath) {
      await addSafeDirectory(repositoryUnsafePath)
    }
    await this.validatePath(path)
    this.setState({ isTrustingRepository: false })
  }
```

**File:** app/src/lib/stores/app-store.ts (L8210-8234)
```typescript
  public async _removeRepository(
    repository: Repository | CloningRepository,
    moveToTrash: boolean
  ): Promise<void> {
    try {
      if (moveToTrash) {
        try {
          await shell.moveItemToTrash(repository.path)
        } catch (error) {
          log.error('Failed moving repository to trash', error)

          this.emitError(
            new Error(
              `Failed to move the repository directory to ${TrashNameLabel}.\n\nA common reason for this is that the directory or one of its files is open in another program.`
            )
          )
          return
        }
      }

      if (repository instanceof CloningRepository) {
        this._removeCloningRepository(repository)
      } else {
        await this.repositoriesStore.removeRepository(repository)
      }
```
