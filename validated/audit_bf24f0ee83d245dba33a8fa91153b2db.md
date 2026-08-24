## Title
Permanent, unrevocable `safe.directory` trust grants persist after repository removal, allowing delayed code execution via git hooks/config on attacker-controlled paths — (File: `app/src/lib/git/config.ts`)

### Summary
GitHub Desktop lets a user "trust" a directory that Git considers unsafe (owned by a different user, e.g. a removable drive, network share, or extracted archive) by calling `addSafeDirectory`, which appends the path to the **global** `safe.directory` Git config. There is no corresponding `removeSafeDirectory` function or UI anywhere in the app to revoke that trust — mirroring the reported `SingleLiquidityProvider` flaw where `addNewPool` exists but no `removePool` does. Once granted, the trust exception is permanent, machine-wide (affects every git invocation on that path, not just Desktop), and outlives the Desktop repository record even if the user removes it from the app.

### Finding Description
The trust flow is implemented in:
- `getRepositoryType` in [1](#0-0) , which detects Git's `dubious ownership` error and returns `{ kind: 'unsafe', path }`.
- `addSafeDirectory` / `addGlobalConfigValueIfMissing` in [2](#0-1) , which appends the path to `--global safe.directory` if not already present.
- Two UI entry points call this with no removal counterpart: `onTrustDirectory` in [3](#0-2)  and `onTrustDirectory` in [4](#0-3) .

A search of the codebase confirms `removeSafeDirectory`/`safe.directory` removal logic does not exist anywhere — the only writers are `addGlobalConfigValue`/`addGlobalConfigValueIfMissing`. Once a path is trusted, `git config --global --unset safe.directory <path>` is never invoked by Desktop, no matter what the user does afterward (removing the repo via `MissingRepository`'s "Remove" button, which calls `dispatcher.removeRepository`, only deletes Desktop's own repository record — it never touches the git global config).

This breaks the intended safety invariant of Git's dubious-ownership protection: that protection exists specifically to stop an attacker who can write files into a path owned by a different user (e.g., another OS account, a mounted network share, a USB drive, an extracted zip from a browser download) from having Git silently execute attacker-controlled hooks/config for that path. Once the user (often under UI-driven pressure, e.g. cloning a fork, opening a shared drive, or extracting a downloaded archive shown as "Add Existing Repository") clicks "Trust Repository" one single time, that specific path is *permanently* whitelisted globally, for the lifetime of the machine's Git config, for all tools using it — not just Desktop's own future validations.

### Impact Explanation
Because there is no revoke/removal capability:
1. **Persistent attack window**: if an attacker later gains write access to that same path (e.g. a shared/synced folder, a re-mounted external drive, a container mount point, or CI-shared workspace reused by other users), any `.git/config`, `core.hooksPath`, `core.fsmonitor`, or hook scripts placed there will be trusted and can execute arbitrary commands the next time Desktop (or any git client/CLI on that machine) operates in that directory — with no warning, because Git no longer considers it "unsafe."
2. **Silent, irreversible state growth**: exactly as in the report, the trust list only grows; it can't be pruned via the UI, so users lose track of what they trusted and why, increasing the odds of accidental future exploitation.
3. Removing the repository from Desktop's list gives users a false sense that the exposure is gone; the underlying global Git trust remains active and exploitable by any other git-consuming tool on the same machine.

### Likelihood Explanation
Requires a plausible, low-friction path: a user clones/adds a repository located on a location not owned by their OS user account (common on shared drives, some CI runners, restored backups, or external media) and clicks "Trust Repository" once, which is the app's advertised recovery flow. No admin rights, malware, or leaked credentials are needed — only normal use of a documented feature and later reuse/compromise of the same path.

### Recommendation
Add a `removeSafeDirectory`/`removeGlobalConfigValue` function mirroring `addSafeDirectory`, and surface UI (e.g., in Settings/Git tab) to list and revoke previously trusted directories. Additionally, consider automatically offering to un-trust a path when the corresponding Desktop repository is removed via `removeRepository`, so trust doesn't outlive the tracked repository.

### Proof of Concept
1. Create a directory owned by a different OS user (or simulate via `GIT_TEST_ASSUME_DIFFERENT_OWNER=1` as in [5](#0-4) ) containing a git repository with a malicious `core.hooksPath` or post-checkout hook.
2. In Desktop, use "Add Existing Repository" and point at the path; Desktop reports it "appears to be owned by another user" per `buildRepositoryUnsafeError` in [6](#0-5) .
3. Click "add an exception for this directory," invoking `onTrustDirectory` → `addSafeDirectory(path)` [3](#0-2) , which permanently writes the path to `~/.gitconfig`'s `safe.directory`.
4. Remove the repository from Desktop.
5. Inspect `~/.gitconfig` — the `safe.directory` entry for the path is still present; no Desktop code path ever removes it. Any future git operation (via Desktop or the CLI) in that path will execute hooks/config without the dubious-ownership warning, even though the original repository was "removed."

### Citations

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

**File:** app/src/ui/add-repository/add-existing-repository.tsx (L129-167)
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
    )
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
