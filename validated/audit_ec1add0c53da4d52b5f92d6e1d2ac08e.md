## Title
Stale `safe.directory` trust grants are never revoked when a repository is removed or relocated, letting a later different owner of the same path inherit automatic hook execution - (File: `app/src/lib/git/config.ts`, `app/src/ui/add-repository/add-existing-repository.tsx`, `app/src/ui/missing-repository.tsx`)

### Summary
The Hats report shows that `detachHSG()` tears down a privileged gate (the Safe's guard/module) without revoking the standing privileges (`owner` status) that were granted while the gate was active, so stale entries silently retain control after "detachment." The Desktop analog is the `safe.directory` trust mechanism: `addSafeDirectory()` in [1](#0-0)  permanently whitelists a path in the user's **global** git config once the user clicks "Trust Repository"/"add an exception", but there is no corresponding code path anywhere in the codebase that removes a path from `safe.directory` when the repository entry is removed, relocated, or found missing.

### Finding Description
When Desktop encounters a directory owned by a different OS user, it treats it as `unsafe` and asks the user to explicitly trust it via `onTrustDirectory`, which calls `addSafeDirectory(path)` [2](#0-1)  and again in the "missing repository" recovery flow [3](#0-2) . That trust decision is written with `--global` scope, keyed only on the file-system **path**, not on any repository identity, fingerprint, or owner check: [4](#0-3) 

Once a path is added to `safe.directory`, Git will treat *any* future repository found at that same path as safe and will not prompt again - it does not re-validate ownership at that point, it only checks path membership in the global allow-list.

`config.ts` does provide `removeGlobalConfigValue` (a generic `--unset-all` for the local repo config) [5](#0-4) , but this generic helper is never invoked to clean up `safe.directory` entries. A search of the codebase confirms there is no `removeSafeDirectory`, no call site that ever unsets `safe.directory`, and no cleanup hook wired into `_removeRepository` [6](#0-5) , `removeRepository` in the repositories store [7](#0-6) , or `_relocateRepository` [8](#0-7) . Trust is added but never subtracted - it is a permanent, path-keyed grant with no lifecycle tied to the repository it was granted for.

This is the same broken invariant as the Hats bug: a mechanism that grants standing privilege (`owner` status in the Safe / trusted-path status in Git) is deactivated or the underlying object is removed (`detachHSG` / repository removed from Desktop), but the privilege itself is never revalidated or revoked, so it silently persists and can be inherited by whoever next occupies that "slot" (a new Safe owner via hat transfer / a new filesystem owner at that same path).

### Impact Explanation
`safe.directory` in Git exists specifically to prevent automatic execution of repository-controlled files (hooks, `.git/config` includes, etc.) when a repository is not owned by the current user - a classic multi-user/shared-machine or malicious-archive protection. Once Desktop persists an exception for a path, that protection is permanently disabled for that path, even after:
- the repository is removed from Desktop (`_removeRepository`),
- the path becomes "missing" and is later reused by something else, or
- the repository is relocated and the old path is abandoned.

If an attacker (a different local account on a shared machine, a malicious installer, a restored backup, or a network/UNC share owned by someone else - the same class of `path[0] === '/'`/UNC-path case Desktop explicitly handles) later places a Git repository containing malicious hooks at that same previously-trusted path, Desktop/Git will silently treat it as safe and Git will auto-execute hooks/config from that untrusted content without ever re-prompting the user, i.e. code execution outside of the repository's original trust boundary. This matches the report's core impact class: a privilege that should have expired with the removal of the guarded object instead persists and is captured by a new, unvetted party.

### Likelihood Explanation
Requires (a) a user having ever clicked "Trust Repository" for a path, and (b) that same path later becoming reachable by a different owner (multi-user machine, shared/mounted network path, container/VM path reuse, or reinstalled/restored environment) - none of which require local malware already running or admin rights, matching the valid-impact criteria (attacker controls a filesystem/remote path the victim's Desktop instance will subsequently resolve). This is a real, low-effort persistence bug rather than a hypothetical: Desktop's own changelog documents purposefully hardening this trust flow for UNC/network paths [9](#0-8) , indicating shared/team/network path reuse is an anticipated real-world scenario for this exact feature.

### Recommendation
Tie the `safe.directory` grant to the lifecycle of the Desktop repository record instead of making it permanent:
- Add a `removeSafeDirectory(path)` using the existing `removeConfigValueInPath`/`--unset-all safe.directory <path>` pattern already present for other config keys [10](#0-9) .
- Call it from `_removeRepository`/`repositoriesStore.removeRepository` when a trusted repository is removed, and from `_relocateRepository` when the old path is abandoned/found unsafe/regular elsewhere.
- Consider re-validating ownership (not just path membership) at repository open/refresh time rather than relying solely on the one-time global exception.

### Proof of Concept
1. On a shared or multi-user machine, User A adds a repository at `C:\Shared\repo` that Git flags `unsafe` (owned by a different user), and clicks "Trust Repository", which calls `addSafeDirectory('C:\Shared\repo')` [2](#0-1)  → `safe.directory=C:\Shared\repo` is written to User A's **global** `.gitconfig`.
2. User A later removes that repository from Desktop (`dispatcher.removeRepository`) [11](#0-10) . No code path unsets `safe.directory` for that path.
3. Attacker (a different account/process with write access to `C:\Shared\repo`, e.g. another OS user, or a restored/rebuilt directory at the same path) creates a new Git repository at the exact same path containing a malicious `core.hooksPath` or hook scripts.
4. User A re-adds/re-opens `C:\Shared\repo` in Desktop. Because `safe.directory` still contains that path, Git no longer flags it as unsafe and no trust prompt is shown; any subsequent Git operation (`clone`, `fetch`, `commit`, etc.) that Desktop performs there will execute the attacker's hooks under User A's Desktop process, exactly the same "stale privileged entity retains control after detachment" failure described in the Hats report.

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

**File:** app/src/lib/git/config.ts (L248-256)
```typescript
/** Remove the global config value by name. */
export async function removeGlobalConfigValue(
  name: string,
  env?: {
    HOME: string
  }
): Promise<void> {
  return removeConfigValueInPath(name, null, env)
}
```

**File:** app/src/lib/git/config.ts (L266-284)
```typescript
async function removeConfigValueInPath(
  name: string,
  path: string | null,
  env?: {
    HOME: string
  }
): Promise<void> {
  const options = env ? { env } : undefined

  const flags = ['config']

  if (!path) {
    flags.push('--global')
  }

  flags.push('--unset-all', name)

  await git(flags, path || __dirname, 'removeConfigValueInPath', options)
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

**File:** app/src/lib/stores/app-store.ts (L8176-8208)
```typescript
  public async _relocateRepository(repository: Repository): Promise<void> {
    const path = await showOpenDialog({ properties: ['openDirectory'] })

    if (path === null) {
      return
    }

    const rt = await getRepositoryType(path)

    if (rt.kind === 'regular') {
      // The repository has moved, so any main worktree we recorded before now
      // points at where it used to be. Resolve it again from the new location.
      await this.repositoriesStore.updateRepositoryPath(
        repository,
        rt.topLevelWorkingDirectory,
        rt.gitDir,
        await this.findMainWorktreePath(rt.topLevelWorkingDirectory)
      )
    } else if (rt.kind === 'unsafe') {
      // Git refuses to run in a repository it considers unsafe, so there's no
      // resolving the main worktree here. Drop the recorded path rather than
      // keep one we know is stale.
      await this.repositoriesStore.updateRepositoryPath(
        repository,
        path,
        undefined,
        undefined,
        true
      )
    } else {
      this.emitError(new Error(this.getInvalidRepoPathsMessage([path])))
    }
  }
```

**File:** app/src/lib/stores/app-store.ts (L8210-8246)
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
    } catch (err) {
      this.emitError(err)
      return
    }

    const allRepositories = await this.repositoriesStore.getAll()
    if (allRepositories.length === 0) {
      this._closeFoldout(FoldoutType.Repository)
    } else {
      this._showFoldout({ type: FoldoutType.Repository })
    }
  }
```

**File:** app/src/lib/stores/repositories-store.ts (L272-278)
```typescript
  /** Remove the given repository. */
  public async removeRepository(repository: Repository): Promise<void> {
    await this.db.repositories.delete(repository.id)
    clearTagsToPush(repository)

    this.emitUpdatedRepositories()
  }
```

**File:** changelog.json (L1761-1767)
```json
    "2.9.15-beta1": [
      "[Improved] Add UNC path prefix before adding to safe directory list - #14368",
      "[Improved] Upgrade embedded Git to v2.35.3 on macOS, v2.35.3.windows.1 on Windows, and Git LFS to v3.1.2"
    ],
    "2.9.14": [
      "[Improved] Surface Git's warning about unsafe directories and provide a way to trust repositories not owned by the current user - #14336"
    ],
```

**File:** app/src/ui/app.tsx (L1268-1288)
```typescript
  private removeRepository = (
    repository: Repository | CloningRepository | null
  ) => {
    if (!repository) {
      return
    }

    if (repository instanceof CloningRepository || repository.missing) {
      this.props.dispatcher.removeRepository(repository, false)
      return
    }

    if (this.state.askForConfirmationOnRepositoryRemoval) {
      this.props.dispatcher.showPopup({
        type: PopupType.RemoveRepository,
        repository,
      })
    } else {
      this.props.dispatcher.removeRepository(repository, false)
    }
  }
```
