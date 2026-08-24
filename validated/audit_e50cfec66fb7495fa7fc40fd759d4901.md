## Finding: Stale `safe.directory` trust decisions persist for a path regardless of who now controls its contents

The OpenZeppelin bug's broken invariant is: **a security decision (quorum/vote outcome) is evaluated once, then treated as permanently valid even after the underlying state that justified it changes.** The Desktop analog is in the Git "unsafe repository" ownership-trust flow.

### Title
Git ownership-trust decisions recorded via `addSafeDirectory` are keyed only by path string and never re-validated, letting attacker-controlled content at a previously-trusted path bypass the dubious-ownership protection - (File: `app/src/lib/git/config.ts`)

### Summary
When Desktop detects a repository with "dubious ownership" (i.e. the working directory is owned by a different user/account than the one running Desktop), it surfaces a "Trust Repository" prompt. Accepting it calls `addSafeDirectory`, which permanently appends the literal path string to the user's **global** `safe.directory` git config entry.

### Finding Description
`getRepositoryType` detects the unsafe condition by parsing Git's `fatal: detected dubious ownership in repository at '<path>'` error [1](#0-0) . The remediation UI (`AddExistingRepository.onTrustDirectory` and `MissingRepository.onTrustDirectory`) calls `addSafeDirectory(path)` once the user clicks "Trust Repository" [2](#0-1) [3](#0-2) .

`addSafeDirectory` writes the exact path string into the global `safe.directory` multi-value config via `addGlobalConfigValueIfMissing`, with no binding to repository identity, commit hash, remote URL, or ownership/UID at the time of trust: [4](#0-3) .

Git's own dubious-ownership check treats any directory whose path text matches an entry in `safe.directory` as trusted from then on, independent of who owns it, forever — Desktop never removes entries or re-prompts once a path has been trusted. There is no mechanism that ties the trust grant to the specific repository/owner that was inspected at the moment of consent; it is purely a path-string allowlist that outlives the reason it was created.

### Impact Explanation
The unsafe-ownership check exists specifically to stop Desktop from running Git operations (which can execute repository-controlled configuration such as `core.fsmonitor`, `core.hooksPath`, filters, and other config-driven executables) against a directory the user doesn't actually control. Once a path is trusted, any future filesystem content that later occupies that same path — e.g. after a removable/network drive is swapped, remounted under a different account, or a directory is deleted and recreated by another user/process on a shared machine — is silently treated as trusted by Git and Desktop, with no re-prompt. This mirrors the advisory's core problem: a decision made under one state (original repository/owner) is later applied unconditionally to a materially different state (new content/owner at the same path), enabling execution of attacker-supplied repository configuration that the ownership check was designed to block.

### Likelihood Explanation
This requires the attacker to place their own git repository content at a path the victim previously trusted — realistic on shared/multi-user workstations, removable media, and network shares/mount points reused across sessions, all scenarios explicitly called out in Desktop's own changelog as the target use case for `safe.directory` support ("Support trusting repositories on network shares (Windows)"). No local privilege escalation, malware, or leaked credentials are needed beyond the attacker being able to write to a path the user will later reuse or that gets remounted with different ownership — well within the "attacker controls a cloned/fetched repository" scope of valid impact.

### Recommendation
Do not treat `safe.directory` trust as permanent/path-only. Consider re-verifying ownership/identity at the time of each operation (e.g., record and compare the owner UID/SID or a repository fingerprint at the time trust was granted, not just the path), prompting again if the directory's owner or git-dir identity has changed since the exception was added, and providing users a way to review/revoke previously trusted paths from within Desktop.

### Proof of Concept
1. User A trusts a directory `D` owned by them after being warned about dubious ownership (e.g. `/Volumes/SharedDrive/project`), which persists `safe.directory=/Volumes/SharedDrive/project` in the global gitconfig [5](#0-4) .
2. The drive is later unmounted, and an attacker (or a different account on a shared machine, or a different device presenting the same mount path) places a malicious git repository at the same path `D`, configuring e.g. `core.fsmonitor` or a hook to execute arbitrary code.
3. When the victim reopens Desktop and it resolves the repository at `D` via `getRepositoryType`, Git no longer reports it as unsafe because the path already exists in `safe.directory` — Desktop performs no additional ownership re-check, and subsequent Git operations execute the attacker's repository-level configuration. [6](#0-5)  confirms the unsafe-detection relies solely on the `safe.directory` config match, with no other identity binding.

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
