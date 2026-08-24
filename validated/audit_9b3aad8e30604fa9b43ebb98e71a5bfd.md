Based on the local evidence, there's a concrete Desktop analog to the report's core invariant: **a decision granted for one point in time (an owner/content state) is never re-validated or revoked when circumstances change**, and the app blindly trusts based on a stale, persistent flag rather than the current situation.

### Title
Trusted `safe.directory` entries are never revoked when a repository is removed, allowing stale trust to silently apply to different content later placed at the same path - ([File: app/src/lib/git/config.ts])

### Summary
When GitHub Desktop encounters a Git repository owned by a different user, it treats it as "unsafe" and offers a "Trust Repository" action. Accepting this action calls `addSafeDirectory()`, which permanently adds the absolute path to the **global** `safe.directory` Git config [1](#0-0) . This trust decision is bound only to the *path string*, not to the repository's identity, remote, or the actual owner at the time of trust. There is no corresponding `removeSafeDirectory`/cleanup function anywhere in the codebase, and `_removeRepository()` in `app-store.ts` only deletes Desktop's local database record — it never revokes the `safe.directory` entry [2](#0-1) .

### Finding Description
The invariant that should hold is: "a directory is only exempted from Git's ownership-safety check for as long as the specific trusted content occupies that path." Instead, Desktop's implementation makes trust permanent and content-agnostic:

- `AddExistingRepository.onTrustDirectory()` and `MissingRepository.onTrustDirectory()` both call `addSafeDirectory(unsafePath)` on user confirmation [3](#0-2) [4](#0-3) .
- `addSafeDirectory` writes the path to the global `safe.directory` list via `addGlobalConfigValueIfMissing`, with no expiry, scoping to a repo identity, or ownership binding [5](#0-4) .
- When the user later removes that repository from Desktop (`_removeRepository`), only the app database entry is deleted — the global `safe.directory` config is left untouched [2](#0-1) .
- `getRepositoryType`'s "unsafe" classification (exercised by `git.ts`/`rev-parse`) relies purely on Git's own `safe.directory` check, which only compares paths, not ownership history [6](#0-5) .

Because trust is keyed only by absolute path, if new content — controlled by a different, potentially untrusted party — later occupies that same path (e.g., a shared/synced folder, a container mount, a temp directory, a symlink swap, or a path reused after the original repository is deleted and something else is cloned or extracted there), Git and Desktop will silently treat it as trusted with zero warning, even though the user never evaluated *that* content. Desktop's own warning text acknowledges the risk this bypasses: "Adding untrusted repositories may automatically execute files in the repository" [7](#0-6) .

### Impact Explanation
Once a path is silently treated as safe, Desktop will read and act on the repository's local Git config (and any hook-relevant settings) without prompting the user again, since it never re-evaluates the trust decision against the current content/owner. This can enable config-driven code execution paths inside GitHub Desktop's normal repository operations (fetch/checkout/refresh) against content the user never actually reviewed or trusted, satisfying the "code execution via attacker-controlled repository the user opens" impact class.

### Likelihood Explanation
Exploitation requires: (1) the user trusts a directory at least once (a common, expected workflow step, e.g. corporate shared drives, CI checkout caches, USB drives, or reused temp/build directories), and (2) that same path is later repopulated with attacker-influenced content (e.g. shared network paths, reused CI workspace directories, or a symlink attack). No admin rights or pre-existing malware are needed — only that the previously-trusted path is reused, which is a realistic scenario for shared/team or automated environments where paths are conventionally fixed (e.g., `~/dev/repo`, `/mnt/shared/project`).

### Recommendation
Bind the trust decision to more than just the raw path — e.g., re-validate ownership/identity at the time of use, prompt again if the directory was deleted and recreated, or remove the corresponding `safe.directory` entry when the repository is removed from Desktop (mirroring `addSafeDirectory` with a `removeSafeDirectory` counterpart called from `_removeRepository`). Alternatively, treat trust as tied to the repository's initial commit/remote, not solely the filesystem path.

### Proof of Concept
1. User points Desktop ("Add Existing Repository") at a shared path `/shared/proj` owned by another user; Desktop flags it "unsafe" and offers "Trust Repository" — user accepts (`addSafeDirectory('/shared/proj')`), permanently adding it to the global `safe.directory` list.
2. User later removes this repository from Desktop via `_removeRepository` (with or without "move to trash"). No cleanup call removes `/shared/proj` from `safe.directory`.
3. An attacker (another user on the same shared filesystem, or a subsequent build/CI step) creates or clones a different, malicious repository at the exact same path `/shared/proj`, configuring hostile local Git config values (e.g. `core.fsmonitor`, hooks-relevant settings).
4. The same user (or any user on that machine) re-adds/opens `/shared/proj` in Desktop. Git's ownership-mismatch warning is suppressed entirely because the path is still listed in `safe.directory` from step 1 — Desktop shows no "unsafe"/"Trust Repository" prompt at all, and proceeds to read/act on the attacker's repository configuration as if the user had explicitly reviewed and trusted it.

### Citations

**File:** app/src/lib/git/config.ts (L176-188)
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

**File:** app/src/ui/add-repository/add-existing-repository.tsx (L145-171)
```typescript
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
    )

    const screenReaderMessage = `The Git repository appears to be owned by another user on your machine.
      Adding untrusted repositories may automatically execute files in the repository.
      If you trust the owner of the directory you can add an exception for this directory in order to continue.`
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
