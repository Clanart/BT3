### Title
Trust decisions in `safe.directory` are bound to a mutable path, not an identity, so removing/relocating a repository never revokes trust - a new (attacker-controlled) directory at the same path inherits the trust grant - (File: `app/src/lib/git/config.ts`)

### Summary
The `offboard()` report describes a broken invariant: state that acts as a security guarantee (nonces preventing signature replay) is bound to a reusable identifier (`msg.sender`, i.e. the Safe address) rather than to the specific "session" of approvers that produced it, and it can be reset/reused when a new session assumes the same identifier. The GitHub Desktop analog is `addSafeDirectory()` / `addGlobalConfigValueIfMissing('safe.directory', path)`: once a directory path is marked trusted, that trust is stored permanently, keyed only by the filesystem path string, with no corresponding invalidation path when GitHub Desktop stops managing that repository (removal, relocation, or the disk location being reused).

### Finding Description
`addSafeDirectory()` [1](#0-0)  permanently appends a path to the global `safe.directory` git config the first time a user clicks "Trust Repository" in `MissingRepository` [2](#0-1)  or `AddExistingRepository` [3](#0-2) . This value is written via `addGlobalConfigValueIfMissing`, which only appends if the value isn't already present [4](#0-3) .

There is no `removeSafeDirectory`/revocation counterpart in the codebase (confirmed by search: only `addSafeDirectory` exists, no removal function). When a user removes a repository from Desktop via `_removeRepository` [5](#0-4)  / `RepositoriesStore.removeRepository` [6](#0-5) , only the Dexie DB record is deleted — the global git `safe.directory` entry for that path persists indefinitely.

This mirrors the `offboard()` bug pattern exactly: the trust grant (`safe.directory` entry) is scoped to a re-usable identifier (a filesystem path) instead of the specific repository/owner identity that earned the trust. Just as a Safe could be "onboarded" again with different approvers but replay old nonces because the identifier (Safe address) was reused, an attacker who can get content placed at a path a user previously trusted (e.g., a shared machine, a removable drive, a path the user deleted and later reused, or a symlink/junction swap) inherits automatic trust — Git will not warn about "dubious ownership" at that path ever again, and Desktop will never re-prompt.

### Impact Explanation
`getRepositoryType()` treats a path in `safe.directory` as always safe [7](#0-6) , bypassing the "potentially unsafe" warning that normally blocks Desktop from treating a repository owned by another user as trusted. Per Git's own documentation (referenced in the code comment), an untrusted directory can "automatically execute files in the repository" via hooks/config [8](#0-7) . If an attacker can place a malicious `.git` directory (hooks, aliases, etc.) at a path previously trusted by the user and then removed from Desktop, Desktop will silently treat it as safe again and could execute attacker-controlled hooks/config on subsequent git operations — potential code execution outside expected trust boundaries.

### Likelihood Explanation
Exploitation requires the attacker to control content that ends up at a path the victim previously trusted and later stopped tracking in Desktop (e.g., shared/managed machines, mounted network drives, container/VM re-provisioning, or path reuse after deleting a repo folder). This is a real but narrower attack surface than a purely remote vector; it does not require local/physical access by the attacker at exploit time, only that they can write to a path (e.g., via a shared filesystem, restored backup, or symlink swap) that was previously trusted — a scenario more plausible in shared, managed, or synced-storage environments.

### Recommendation
- Scope trust to the repository identity (e.g., stored ownership/ACL check or a Desktop-managed record of granted trust tied to path + owner SID/UID) rather than path alone.
- Add a `removeSafeDirectory()` call in `RepositoriesStore.removeRepository` / `_removeRepository` so that removing a repository from Desktop also revokes the corresponding `safe.directory` grant, forcing re-confirmation if the path is used again.
- Alternatively, re-validate current path ownership against the ownership recorded at the time trust was granted before relying on the `safe.directory` allowance, and re-prompt if ownership changed.

### Proof of Concept
1. User adds/clones a repository at `C:\Shared\repo` owned by their own account; if Git flags it unsafe (e.g., different owner metadata due to filesystem/backup restore), the user clicks "Trust Repository", calling `addSafeDirectory('C:\Shared\repo')` [9](#0-8) , which permanently adds the path to the global `safe.directory` config.
2. The user later removes the repository from GitHub Desktop (`Remove` button) [10](#0-9) , and/or deletes the directory from disk.
3. An attacker (e.g., another user on a shared machine, or someone with write access restored via backup/sync) creates a new, malicious Git repository at the exact same path `C:\Shared\repo`, including malicious hooks/config.
4. The victim re-adds a repository at that same path in Desktop. `getRepositoryType()` sees the path is still listed in `safe.directory` and reports `kind: 'regular'` instead of `kind: 'unsafe'` [11](#0-10) , so Desktop never shows the "potentially unsafe" warning and Git will honor the attacker's local config/hooks on subsequent operations, since no removal function ever cleared the earlier trust grant.

### Citations

**File:** app/src/lib/git/config.ts (L176-189)
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
```

**File:** app/src/lib/git/config.ts (L191-206)
```typescript
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

**File:** app/src/ui/missing-repository.tsx (L105-109)
```typescript
    buttons.push(
      <Button key="remove" onClick={this.remove}>
        Remove
      </Button>
    )
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

**File:** app/src/ui/add-repository/add-existing-repository.tsx (L140-166)
```typescript
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

**File:** app/src/lib/stores/app-store.ts (L8210-8235)
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

**File:** app/src/lib/git/rev-parse.ts (L18-65)
```typescript
export async function getRepositoryType(path: string): Promise<RepositoryType> {
  if (!(await directoryExists(path))) {
    return { kind: 'missing' }
  }

  try {
    const result = await git(
      ['rev-parse', '--is-bare-repository', '--show-cdup', '--git-dir'],
      path,
      'getRepositoryType',
      { successExitCodes: new Set([0, 128]) }
    )

    if (result.exitCode === 0) {
      // Bare repositories will not include gitdir so we handle that separately
      if (result.stdout.startsWith('true\n')) {
        return { kind: 'bare' }
      }

      // --is-bare-repository and --show-cdup each produce a single line but
      // --git-dir could theoretically contain newlines so we parse the known
      // fields first and treat the remainder as the git dir. We use [\s\S]*
      // instead of .* for the git dir capture group because .* doesn't match
      // newlines whereas [\s\S]* matches any character including newlines.
      const match = result.stdout.match(/^(true|false)\n(.*)\n([\s\S]*)\n$/)

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
    }

    const unsafeMatch =
      /fatal: detected dubious ownership in repository at '(.+)'/.exec(
        result.stderr
      )
    if (unsafeMatch) {
      return { kind: 'unsafe', path: unsafeMatch[1] }
    }

    return { kind: 'missing' }
```
