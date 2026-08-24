Confirmed: `app/src/lib/git/config.ts` exposes `addSafeDirectory()` and `addGlobalConfigValueIfMissing()` but there is no corresponding remove/revoke function anywhere in the codebase, matching the same one-way-trust-list defect as the original report's `addSafeAddress()`.

### Title
Trusting an unsafe repository directory is permanent with no in-app revocation - (File: `app/src/lib/git/config.ts`)

### Summary
GitHub Desktop lets a user "trust" a repository path that Git flagged as having dubious/different ownership (e.g. cloned to a shared drive, extracted from an archive, restored from a USB stick, or otherwise attacker-influenced). Clicking **Trust Repository** calls `addSafeDirectory()`, which permanently appends the path to the user's **global** `safe.directory` git config entry. There is no corresponding function to remove a path from `safe.directory` anywhere in the app, so once a path is trusted it stays trusted for the lifetime of the user's global git config, exactly mirroring the reported "can add but not remove" bug class.

### Finding Description
`addSafeDirectory()` calls `addGlobalConfigValueIfMissing('safe.directory', path)`, which runs `git config --global --add safe.directory <path>` if the value isn't already present. [1](#0-0) 

This is invoked from two UI entry points whenever `getRepositoryType()` reports `kind: 'unsafe'` (Git's own dubious-ownership detection surfaced via `fatal: detected dubious ownership in repository at '...'`):
- `AddExistingRepository.onTrustDirectory` in the "Add Existing Repository" flow [2](#0-1) 
- `MissingRepository.onTrustDirectory`, shown whenever a previously-added repository path can't be found/opened and is now reported unsafe [3](#0-2) 

`safe.directory` is a Git security mechanism specifically designed to stop Git from reading **repository-local** config/hooks (e.g. `core.fsmonitor`, `core.hooksPath`, `core.sshCommand`) from a directory owned by someone other than the current user — settings that can lead to arbitrary command execution when Git operations run in that directory. By adding a path to `safe.directory`, Desktop tells Git to always trust that path's local config regardless of ownership, forever.

A codebase-wide search confirms there is no `removeSafeDirectory`, `unset`, or any git-config removal path tied to `safe.directory` anywhere in `app/src/lib/git/`, and `safe.directory` only appears in `config.ts`, its test, the changelog, and docs — never in a removal context.

Because the trust decision is keyed purely on **path**, not on repository identity/fingerprint/content, once a path is safe-listed:
1. If the original repository at that path is later replaced (e.g. a shared/mounted directory, symlink target, or reused path after removal, common on shared machines, mapped network drives, or synced folders) with attacker-controlled content, Desktop/Git will silently treat the new content's local config/hooks as trusted, since `getRepositoryType()` will now report `kind: 'regular'` instead of `kind: 'unsafe'` for that path.
2. The user has no in-app way to revoke that trust — there is no "untrust" button, settings entry, or API call that removes an entry from `safe.directory` once added, matching the exact "one-way trust list" defect from the report.

### Impact Explanation
This breaks the invariant that `safe.directory` trust should be revocable when a previously-trusted path becomes untrustworthy. Because trust is bound only to a filesystem path and persists indefinitely with no UI-exposed removal, an attacker who can get their content into a path a user once trusted (shared drives, container/VM reuse, restored backups, USB reuse, CI-shared runners) can achieve execution of malicious repository-local Git configuration/hooks without any further prompt from Desktop, since the "unsafe" warning that would normally require user confirmation is permanently bypassed for that path.

### Likelihood Explanation
Requires a somewhat specific but plausible scenario: reuse of a previously-trusted path with different, attacker-supplied content (e.g., shared/mounted directories, multi-user machines, or path reuse after deleting/re-cloning). It does not require local malware already present, admin rights, or leaked credentials, only that the user once clicked "Trust Repository" for that path and the path is later repopulated by an untrusted party. This is a realistic path for shared/managed environments and satisfies the "attacker controls a cloned/fetched repository" condition since the second occupant of the path is the attacker's content.

### Recommendation
Add a way to remove/untrust entries from `safe.directory` from within Desktop (e.g. a settings UI listing trusted directories with a remove/"untrust" action that runs `git config --global --unset safe.directory <path>`), and consider re-validating trust based on something stronger than raw path equality (e.g. warn again if the directory's owner/inode changes after being trusted, or prompt again if repository content substantially changes, rather than persisting trust solely by path).

### Proof of Concept
1. On a shared or network-mounted directory, clone/place a Git repository owned by another user at `/shared/repo`.
2. In Desktop, use **Add Existing Repository** and point it at `/shared/repo`; Desktop shows the "potentially unsafe" warning via `buildRepositoryUnsafeError()` and offers **add an exception for this directory**. [4](#0-3) 
3. Click the exception link, triggering `onTrustDirectory` → `addSafeDirectory('/shared/repo')`, which runs `git config --global --add safe.directory /shared/repo`. [5](#0-4) 
4. Later, an attacker (another user of the shared/mounted path, or a process that repopulates that same path) replaces `/shared/repo`'s contents with a repository containing a malicious `core.hooksPath`/`core.fsmonitor` local config entry.
5. Reopen Desktop and interact with `/shared/repo` again: `getRepositoryType()` no longer reports `unsafe` for this path (Git considers it safe-listed), so no warning is shown and the malicious local configuration/hooks execute during normal Git operations, since there is no `removeSafeDirectory` mechanism to have revoked trust after step 3.

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

**File:** app/src/ui/add-repository/add-existing-repository.tsx (L129-166)
```typescript
  private buildRepositoryUnsafeError() {
    const { repositoryUnsafePath, path } = this.state
    if (
      !this.state.path.length ||
      !this.state.showNonGitRepositoryWarning ||
      !this.state.isRepositoryUnsafe ||
      repositoryUnsafePath === undefined
    ) {
      return null
    }

    // Git for Windows will replace backslashes with slashes in the error
    // message so we'll do the same to not show "the repo at path c:/repo"
    // when the entered path is `c:\repo`.
    const convertedPath = __WIN32__ ? path.replaceAll('\\', '/') : path

    const displayedMessage = (
      <>
        <p>
          The Git repository
          {repositoryUnsafePath !== convertedPath && (
            <>
              {' at '}
              <Ref>{repositoryUnsafePath}</Ref>
            </>
          )}{' '}
          appears to be owned by another user on your machine. Adding untrusted
          repositories may automatically execute files in the repository.
        </p>
        <p>
          If you trust the owner of the directory you can
          <LinkButton onClick={this.onTrustDirectory}>
            {' '}
            add an exception for this directory
          </LinkButton>{' '}
          in order to continue.
        </p>
      </>
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
