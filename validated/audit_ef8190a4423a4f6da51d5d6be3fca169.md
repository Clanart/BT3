### Title
Stale `safe.directory` git-config trust exception is never revoked when a repository is removed, letting a later attacker-controlled repo at the same path auto-execute — (File: `app/src/lib/git/config.ts`, `app/src/lib/stores/repositories-store.ts`)

### Summary
The Curve report's broken invariant is: a user's per-gauge state (voting power) is granted, the gauge is later removed, but the per-user state tied to that gauge is never reset, so it silently persists and can't be corrected. GitHub Desktop has a directly analogous pattern with `safe.directory`, the mechanism that gates whether Git will operate on a repository "owned by another user" (dubious ownership) without prompting the user [1](#0-0) . When a user clicks "Trust Repository"/"add an exception for this directory," Desktop calls `addSafeDirectory(path)`, which permanently appends the path to the **global** `safe.directory` git config [2](#0-1) . This exception is keyed purely on the filesystem path, not on repository identity (no commit hash, no remote URL, no owner check).

When the repository is later removed from Desktop (`_removeRepository` → `repositoriesStore.removeRepository`), only the DB record is deleted and push-tag cache cleared — nothing calls a `removeSafeDirectory` equivalent (no such function exists anywhere in the codebase) [3](#0-2) . The trust grant in the user's global `~/.gitconfig` is never revoked.

### Finding Description
The path-scoped `safe.directory` allow-list is the exact analog of "voting power for a gauge": it's user-granted state tied to an external identifier (a filesystem path) that governance-equivalent actions (repository removal, directory relocation, disk cleanup) do nothing to reset.

- Trust is granted via `onTrustDirectory` in both `AddExistingRepository` and `MissingRepository`, calling `addSafeDirectory(unsafePath)` → `addGlobalConfigValueIfMissing('safe.directory', path)`, which writes to the **global** gitconfig, not scoped to the specific repository instance [4](#0-3) [5](#0-4) .
- The check exists specifically because "Adding untrusted repositories may automatically execute files in the repository" — the UI's own warning text acknowledges the code-execution risk being guarded against [6](#0-5) .
- `getRepositoryType` treats a path as `unsafe` only when git detects "dubious ownership" (i.e., a different OS-level owner than the current user) at that exact path [7](#0-6) . Once a path is in `safe.directory`, this check is permanently bypassed for that path for every future repository that happens to reside there.
- Repository removal (`_removeRepository` → `repositoriesStore.removeRepository`) deletes only the Dexie DB record and push-tag cache; it performs no git-config cleanup [3](#0-2) [8](#0-7) .
- Relocating a repository (`_relocateRepository`) similarly never revisits the old path's trust entry [9](#0-8) .
- `matchExistingRepository`/`addRepository` key entirely off the normalized filesystem path with no ownership or identity binding [10](#0-9) [11](#0-10) .

Attack path: a user clones/trusts a repository at path `P` (e.g., a shared drive, a synced cloud folder, `/tmp`-style shared location, or a path handed out by an "Open in Desktop" deep link), trusts it once, then removes it from Desktop or deletes the directory. Later, another (differently-owned, i.e., attacker-controlled on a shared machine, or a container/VM image) writes a malicious Git repository — with a hostile `core.hooksPath`, `core.fsmonitor`, filter/driver config, or hooks — at the exact same path `P`. Because `P` is still present in the user's global `safe.directory` list, `getRepositoryType` never returns `unsafe` for that new repository; Desktop treats it as fully trusted, `_addRepositories`/re-add flows proceed straight through the `regular` branch, skip the "This directory appears... owned by another user" gate, and git operations Desktop performs (fetch, checkout, status, LFS smudge/filters, hooks) execute with the attacker's repo-local config with no confirmation prompt.

### Impact Explanation
This matches the requested impact classes: the attacker controls the git repository object placed at a previously-trusted path, and the result is bypass of the "dubious ownership"/untrusted-repository code-execution guard, enabling automatic execution of attacker-supplied Git hooks/filters/config (`core.hooksPath`, smudge/clean filters, `core.fsmonitor`) the moment Desktop operates on the repository — i.e., code execution outside any sandbox, achieved without the user ever being warned. This is exactly the class of harm the existing `isRepositoryUnsafe` UI flow was built to prevent, and the stale trust entry silently defeats it.

### Likelihood Explanation
Requires a scenario where a path is reused by a different owner after Desktop previously trusted it there — realistic on shared/multi-user machines, CI-adjacent dev boxes, shared network/cloud-synced folders, or removable/temp working directories, all legitimate no-privilege-escalation scenarios (no malware or admin rights needed on the victim's own session; the "attacker" is simply another actor able to write to that shared path or a party who can lure the user into cloning at a path they previously used). Likelihood is moderate: it depends on path reuse, but the guard exists specifically to cover this class of risk, and nothing in the codebase revokes it once granted, so the window never closes on its own.

### Recommendation
- Track `safe.directory` grants per tracked repository (e.g., store the trusted path in the repositories DB alongside the repo record) and remove the corresponding `safe.directory` entry when that repository is removed from Desktop (`repositoriesStore.removeRepository`) or relocated away from that path (`_relocateRepository`).
- Alternatively/additionally, re-validate ownership at the exact path on every subsequent access rather than trusting a global config allow-list indefinitely (e.g., pair the `safe.directory` exception with an identity check such as a stored initial-commit hash or remote URL for that path, invalidating trust if the repository at the path materially changes).
- Provide a user-facing "Manage trusted directories" surface so users can audit and revoke stale exceptions, mirroring the report's mitigation of allowing users to explicitly reclaim/reset stale state.

### Proof of Concept
1. Create repo `A` at path `P`, owned by a different OS user (simulate via `GIT_TEST_ASSUME_DIFFERENT_OWNER=1` as in the existing test [12](#0-11) ).
2. In Desktop, add repository at `P` → dialog shows "potentially unsafe" → click "Trust Repository" (`onTrustDirectory`) → `addSafeDirectory(P)` writes `safe.directory=P` to global gitconfig.
3. Remove the repository from Desktop (`_removeRepository`) — confirm `safe.directory=P` is still present in `~/.gitconfig` (no cleanup call exists in `repositories-store.ts`).
4. Delete directory `P`; recreate a new git repository at the same path `P`, now owned by a different (attacker) user, containing a malicious `core.hooksPath` or filter config.
5. Re-add path `P` in Desktop: `getRepositoryType(P)` returns `regular` (not `unsafe`) because `safe.directory` still lists `P`, so no warning is shown and Desktop proceeds to run git operations (`loadRemotes`, fetch, checkout) against the attacker's config/hooks without any trust prompt.

### Citations

**File:** app/src/lib/git/rev-parse.ts (L18-63)
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
```

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

**File:** app/src/lib/stores/repositories-store.ts (L237-270)
```typescript
  public async addRepository(
    path: string,
    gitDir: string | undefined,
    opts?: AddRepositoryOptions
  ): Promise<Repository> {
    const repository = await this.db.transaction(
      'rw',
      this.db.repositories,
      this.db.gitHubRepositories,
      this.db.owners,
      async () => {
        const existing = await this.db.repositories.get({ path })

        if (existing !== undefined) {
          return await this.toRepository(existing)
        }

        const dbRepo: IDatabaseRepository = {
          path,
          gitHubRepositoryID: null,
          missing: opts?.missing ?? false,
          lastStashCheckDate: null,
          alias: null,
          gitDir,
        }
        const id = await this.db.repositories.add(dbRepo)
        return this.toRepository({ id, ...dbRepo })
      }
    )

    this.emitUpdatedRepositories()

    return repository
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

**File:** app/src/ui/missing-repository.tsx (L35-49)
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
```

**File:** app/src/ui/add-repository/add-existing-repository.tsx (L150-157)
```typescript
            <>
              {' at '}
              <Ref>{repositoryUnsafePath}</Ref>
            </>
          )}{' '}
          appears to be owned by another user on your machine. Adding untrusted
          repositories may automatically execute files in the repository.
        </p>
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

**File:** app/src/lib/repository-matching.ts (L54-65)
```typescript
export function matchExistingRepository<T extends { readonly path: string }>(
  repos: ReadonlyArray<T>,
  path: string
): T | undefined {
  // Windows is guaranteed to be case-insensitive so we can be a bit less strict
  const normalize = __WIN32__
    ? (p: string) => Path.normalize(p).toLowerCase()
    : (p: string) => Path.normalize(p)

  const needle = normalize(path)
  return repos.find(r => normalize(r.path) === needle)
}
```

**File:** app/test/unit/git/rev-parse-test.ts (L131-154)
```typescript
    it('returns unsafe for unsafe repository', async t => {
      const testRepoPath = await setupFixtureRepository(t, 'test-repo')
      const repository = new Repository(testRepoPath, -1, null, false)

      const previousHomeValue = process.env['HOME']

      // Creating a stub global config so we can unset safe.directory config
      // which will supersede any system config that might set * to ignore
      // warnings about a different owner
      //
      // This is because safe.directory setting is ignored if found in local
      // config, environment variables or command line arguments.
      const testHomeDirectory = await createTempDirectory(t)
      const gitConfigPath = path.join(testHomeDirectory, '.gitconfig')
      await writeFile(
        gitConfigPath,
        `[safe]
directory=`
      )

      process.env['HOME'] = testHomeDirectory
      process.env['GIT_TEST_ASSUME_DIFFERENT_OWNER'] = '1'

      assert((await getRepositoryType(repository.path)).kind === 'unsafe')
```
