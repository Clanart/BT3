### Title
`safe.directory` trust exceptions are permanent and cannot be revoked, letting a one-time "Trust Repository" click grant unrevocable arbitrary-code-execution trust to a path an attacker can later take over - ([File: app/src/lib/git/config.ts])

### Summary
GitHub Desktop mitigates Git's "dubious ownership" arbitrary-code-execution protection (the same class of vulnerability as CVE‑2022‑24765) by offering a "Trust Repository" button that adds the repository path to the global `safe.directory` Git config. This mirrors the reported bug class in the Winnables `Roles` contract: a grant-only primitive (`_addressRoles[user] |= role`) with no corresponding removal path when the `status` should be `false`. In Desktop, `addSafeDirectory()` performs the equivalent "OR-only" operation on `safe.directory`, and there is no `removeSafeDirectory()`/"untrust" counterpart anywhere in the codebase or UI, so once a path is trusted it stays trusted for the lifetime of the user's global git config, regardless of who later owns or controls that path.

### Finding Description
`addSafeDirectory()` unconditionally appends a path to `safe.directory` in the user's global git config and never removes it: [1](#0-0) 

This is invoked from two UI entry points whenever Desktop detects a "dubious ownership" (`unsafe`) repository, as classified by `getRepositoryType()`, which parses git's `fatal: detected dubious ownership in repository at '<path>'` error: [2](#0-1) 

The two call sites, `AddExistingRepository.onTrustDirectory` and `MissingRepository.onTrustDirectory`, both just call `addSafeDirectory(path)` and never offer a way to reverse it: [3](#0-2) [4](#0-3) 

A `grep` across the repository confirms there is no `removeSafeDirectory`, `--unset safe.directory`, or any settings-page UI to manage/revoke previously trusted directories — the only related git invocation is the `--add`/`addGlobalConfigValueIfMissing` path used by `addSafeDirectory` itself. `setConfigValueInPath()` (used elsewhere for scalar config values) uses `--replace-all`, but it is never used for `safe.directory`, and there is no analogous "remove one value from a multi-valued config key" helper in `config.ts`.

This is exactly the "grant but never revoke" invariant from the report: the underlying `safe.directory` list is a multi-valued git config key (a whitelist), and Desktop's only operation on it is an idempotent add. Once a path is trusted, git itself will forever treat any repository at that exact path as safe — bypassing its dubious-ownership/arbitrary-code-execution protection — with no way, short of manually editing `~/.gitconfig` outside of Desktop, to revoke it.

### Impact Explanation
Git's dubious-ownership check exists specifically to prevent automatic execution of repository-controlled content (hooks, `core.fsmonitor`, `.git/config` includes, etc.) when a repository directory is or becomes owned/writable by an untrusted party. Desktop's trust exception is meant to be a deliberate, path-scoped, one-time override — but because it can never be revoked:

- A path that a user trusted once (e.g., a shared/multi-user machine, a mounted network drive, removable media, a CI runner workspace, or a directory later reused by a different project/owner) remains permanently exempt from the ownership check.
- If an attacker later gains write access to that same path (e.g., by taking over a shared mount, replacing the contents of a previously-deleted-and-recreated directory, or controlling a subsequently cloned/fetched repository that lands at that exact path), Desktop/git will silently treat the attacker-controlled repository as safe and execute its hooks/config without any further prompt or warning to the user.
- This directly satisfies the "unprivileged... attacker controls a cloned/fetched repository... resulting in code execution... outside the repo" criterion, since the trust bypass is triggered purely by path reuse, not by any new user consent.

The user has no way to see, audit, or revoke previously granted exceptions from within Desktop, so there is no mitigation available to an affected user other than manually editing the global gitconfig file — an action outside Desktop's own security boundary.

### Likelihood Explanation
Likelihood is moderate: it requires a specific but realistic sequence — the user must have previously trusted a directory that is later reused by, or handed over to, an untrusted party at the same path (common on shared workstations, CI agents, USB drives, or reused temp/build directories). No admin rights, malware, or leaked credentials are required; it only relies on Desktop's own trust bookkeeping being permanent by design/bug, and on git's `safe.directory` matching by exact path with no ownership re-check once whitelisted.

### Recommendation
Mirror the mitigation the report calls for in `_setRole()`: make the trust operation bidirectional instead of add-only.
- Add a `removeSafeDirectory(path)` helper in `app/src/lib/git/config.ts` that removes a single value from the multi-valued `safe.directory` config key (e.g., `git config --global --unset-all safe.directory <value>` scoped correctly, or rewriting the config).
- Expose a "Manage trusted directories" UI (e.g., in Settings/Preferences) listing all paths currently present in `safe.directory` that Desktop added, allowing users to revoke individual entries.
- Optionally, scope trust decisions with additional context (e.g., re-validate the directory's owner/ACL at the time of use, not just at the time of granting) so that reuse of a previously-trusted path by a different owner does not silently inherit the old trust decision.

### Proof of Concept
1. On a shared machine, User A clones a repository to `/shared/project`.
2. Due to permission/ownership mismatch (e.g., different user account), Desktop shows the "unsafe" warning; User A clicks "Trust Repository," which calls `addSafeDirectory('/shared/project')` and adds this path to `~/.gitconfig`'s `safe.directory` list permanently.
3. Time passes; `/shared/project` is deleted and later reused (e.g., a new project is cloned to the exact same path, or a malicious user gains write access to the same shared mount point at that path) — this time by Attacker B, who plants a malicious `.git/hooks/post-checkout` (or similar) in the repository at that path.
4. User A reopens the repository from Desktop's recent-repositories list. Because `/shared/project` is already whitelisted in `safe.directory` (added in step 2 and never removed), git and Desktop perform no dubious-ownership check and no "unsafe" prompt is shown.
5. Any Desktop-triggered git operation (checkout, fetch, clone-again, etc.) executes Attacker B's hooks, achieving code execution as User A — silently, with no additional trust prompt, solely because the earlier trust grant in `addSafeDirectory()` (app/src/lib/git/config.ts:181-189) can never be revoked.

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
