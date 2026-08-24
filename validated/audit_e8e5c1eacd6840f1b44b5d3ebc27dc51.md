Confirmed: there is no code path anywhere in the codebase that calls `removeGlobalConfigValue('safe.directory', ...)` or any equivalent "untrust" function. The only writer of `safe.directory` is `addSafeDirectory` in [1](#0-0)  and it is never paired with a corresponding removal.

### Title
Permanent `safe.directory` trust grant is never revoked when a repository is removed from GitHub Desktop, causing global Git ownership-check bypass to outlive the repo - (File: app/src/lib/git/config.ts)

### Summary
When GitHub Desktop encounters a repository whose directory is owned by a different user than the current process (Git's "dubious ownership" / `unsafe` repository type), it offers the user a "Trust Repository" action. That action calls `addSafeDirectory`, which appends the path to the **global** `safe.directory` Git configuration (`~/.gitconfig`), permanently disabling Git's ownership-mismatch protection for that exact path, machine-wide, for every git operation done through the user's global config — not only within GitHub Desktop. [2](#0-1)  When the user later removes that repository from GitHub Desktop (via `_removeRepository`), the corresponding `safe.directory` entry is never unset. [3](#0-2) [4](#0-3)  This is a mint-without-burn pattern directly analogous to the `vMaia`/`ERC4626PartnerManager` bug: a trust "token" is granted on one action (`addSafeDirectory`) and is never revoked on the logically inverse action (repository removal), silently degrading a security invariant (git ownership verification) over the lifetime of the application.

### Finding Description
`addSafeDirectory(path)` writes to the global, machine-wide `safe.directory` list using `git config --global --add safe.directory <path>`. [1](#0-0)  This function is invoked from three UI entry points that a user can reach when Desktop reports a repository as "unsafe" (owned by another user):
- `AddExistingRepository.onTrustDirectory` [5](#0-4) 
- `MissingRepository.onTrustDirectory` [6](#0-5) 

Both flows are reachable purely by pointing GitHub Desktop at (or re-locating to) a path that git flags as owned-by-a-different-user — no admin rights, malware, or leaked credentials are needed; the "different owner" condition is naturally hit for shared machines, mounted network shares, removable media, or containers/VMs where UID mapping differs, none of which require unnatural user steps beyond the normal "Add Existing Repository" or "Locate…" flows Desktop already exposes.

Once trusted, the path is never removed from `safe.directory`. Searching the whole codebase for any unset/removal of this config key returns nothing: `removeGlobalConfigValue` exists as a generic primitive [7](#0-6)  but is never called with `safe.directory`. The repository-removal code paths (`AppStore._removeRepository` and `RepositoriesStore.removeRepository`) only delete the Desktop database record and clear push-tag bookkeeping — they never touch Git's global config. [3](#0-2) [4](#0-3) 

Because `safe.directory` is a global, path-keyed allowlist (not scoped to GitHub Desktop, nor to a specific repository identity/fingerprint — just a plain path string), the corrupted persistent value is the user's `~/.gitconfig` `safe.directory` list, which continues to contain a path that Desktop itself no longer tracks or considers "known." If that path is later repopulated with different content — e.g., a shared folder, mount point, USB drive, or CI workspace path that gets reassigned to a different repository or a different (attacker-controlled) owner — any Git tooling on the machine (not just Desktop) that reads the global config will silently treat that path as trusted and skip the "detected dubious ownership" protection that Git added specifically to prevent local privilege boundary bypasses (e.g., another local user planting a malicious `.git/hooks` or `core.fsmonitor`/`core.pager` config that executes on git invocation).

### Impact Explanation
Git's `safe.directory` protection exists specifically to stop a different-owner repository directory (which could contain attacker-planted `.git/config` settings like `core.fsmonitor`, `core.pager`, `core.editor`, or hooks) from executing code merely by being operated on with git. By permanently whitelisting a path once and never revoking it on repository removal, GitHub Desktop leaves a standing bypass of this protection at that path indefinitely, independent of Desktop's own notion of "which repos exist." Any future filesystem object at that same path — including one repopulated by an attacker who controls a shared/removable/mounted location the victim previously trusted and later removed — regains the ownership-check bypass automatically the next time the victim (in Desktop or any other Git client sharing the same `~/.gitconfig`) touches that path, enabling malicious `.git/config`-driven code execution without the victim re-confirming trust.

### Likelihood Explanation
The trigger conditions are ordinary Desktop usage: adding/relocating a repository at a path with mismatched ownership (common on shared drives, mounted volumes, restored backups, multi-user machines, and some container/dev-container setups) and later removing that repository, which is a routine cleanup action. No elevated privileges or pre-existing compromise are required to reach the vulnerable state; only the subsequent step (an attacker repopulating that exact path with malicious content) requires the attacker to have some ability to write to a location the victim previously used — a plausible scenario for shared/network/removable storage, which is precisely the class of paths that triggers "dubious ownership" in the first place.

### Recommendation
Mirror the mitigation applied to the analogous `vMaia` finding by pairing every "mint" with a "burn": when a repository is removed from GitHub Desktop (`AppStore._removeRepository` / `RepositoriesStore.removeRepository`), if that repository's path was previously added via `addSafeDirectory`, remove it from the global `safe.directory` list (e.g., `git config --global --unset safe.directory <path>` using the existing `removeGlobalConfigValue` primitive, being careful to only remove the specific value with `--fixed-value` since `safe.directory` is multi-valued). Track which paths Desktop itself added (versus pre-existing user/system configuration) so the removal only cleans up entries Desktop is responsible for.

### Proof of Concept
1. On a machine with a mounted/shared directory `D` owned by a different local user (or simulate via `GIT_TEST_ASSUME_DIFFERENT_OWNER=1` as used in the test suite [8](#0-7) ), use "Add Existing Repository" in Desktop pointing at `D`.
2. Desktop reports `D` as unsafe; click "Trust Repository", triggering `onTrustDirectory` → `addSafeDirectory(D)`. [5](#0-4) 
3. Verify `git config --global --get-all safe.directory` now lists `D`.
4. In Desktop, remove the repository at `D` from the app (regular "Remove" flow, no "delete from disk").
5. Re-run `git config --global --get-all safe.directory` — `D` is still present, confirming the entry is never revoked. [3](#0-2) 
6. Replace the contents at path `D` (simulating an attacker repopulating a shared/removable path) with a malicious repository containing a `.git/config` that sets `core.fsmonitor` or a hook to run an arbitrary command.
7. Run any `git` command against `D` (from Desktop or a terminal) — the ownership-mismatch protection is bypassed because `D` remains in `safe.directory`, and the malicious config/hook executes.

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

**File:** app/src/lib/stores/repositories-store.ts (L272-278)
```typescript
  /** Remove the given repository. */
  public async removeRepository(repository: Repository): Promise<void> {
    await this.db.repositories.delete(repository.id)
    clearTagsToPush(repository)

    this.emitUpdatedRepositories()
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

**File:** app/test/unit/git/rev-parse-test.ts (L131-158)
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

      process.env['GIT_TEST_ASSUME_DIFFERENT_OWNER'] = undefined
      process.env['HOME'] = previousHomeValue
    })
```
