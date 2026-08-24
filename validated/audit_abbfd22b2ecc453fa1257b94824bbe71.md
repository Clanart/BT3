## Title
`addSafeDirectory` permanently disables Git's dubious-ownership protection with no revoke mechanism - (File: `app/src/lib/git/config.ts`)

### Summary
When GitHub Desktop encounters a repository path that Git considers "unsafe" (owned by a different user than the current process), it offers a "Trust Repository" action that calls `addSafeDirectory`, which appends the path to the **global** `safe.directory` Git config. [1](#0-0)  There is no corresponding `removeSafeDirectory` (or any "untrust"/revoke) function anywhere in the codebase, so once a path is trusted it remains permanently exempt from Git's ownership check for the lifetime of the user's global Git config, with no UI or API to undo it.

### Finding Description
Git's `safe.directory` mechanism exists specifically to prevent a directory owned by another user from having its config/hooks trusted and executed automatically (protecting against local privilege-escalation and multi-user attack scenarios) — this is precisely warned about in Desktop's own UI copy: *"Adding untrusted repositories may automatically execute files in the repository."* [2](#0-1)  and the identical warning/`onTrustDirectory` flow in the missing-repository view [3](#0-2) .

Both flows funnel into `addSafeDirectory`, which calls `addGlobalConfigValueIfMissing('safe.directory', path)` — an additive, idempotent-add-only operation against the user's global `.gitconfig`. [4](#0-3)  There is no `removeGlobalConfigValue`/`removeSafeDirectory` counterpart in `config.ts`, nor any call site elsewhere in the app that ever removes an entry from `safe.directory`. This mirrors exactly the reported invariant break in the original finding: a privilege ("trusted"/"admin" status) can be granted but never revoked once set — the corrupted, permanent value here is the user's global `safe.directory` allow-list.

The practical attacker path: an attacker controls a directory path that a Desktop user has trusted in the past (e.g., a shared/synced folder, a network share, a removable drive, a shared multi-user workstation path, or a location that gets recreated after being deleted, such as a temp/extraction directory or a re-cloned path). If that same filesystem path is later repopulated with attacker-controlled Git content (different repo, malicious hooks/config, different ownership), Desktop's `getRepositoryType` will no longer classify it as `unsafe` — the dubious-ownership check is bypassed globally and permanently for that path — and Desktop will proceed to run Git commands (fetch/checkout/etc.) against it without ever re-prompting the user, allowing hook/config-based code execution outside the sandboxed trust model Desktop itself advertises.

### Impact Explanation
Git's dubious-ownership protection exists to stop exactly this: silent execution of attacker-supplied hooks/config in a directory the current user does not solely own. Since Desktop's exemption is permanent and global (not scoped to a single session, single repository instance, or re-validated against ownership changes), a single one-time "Trust Repository" click by the user creates a standing bypass that can later be leveraged by anyone able to place content at that path — resulting in code execution via Git hooks/config, which is within the accepted impact class (code execution from an attacker-controlled repository, bypassing the intended trust boundary).

### Likelihood Explanation
This requires the user to have trusted a directory once (a legitimate, common action Desktop actively prompts for via `MissingRepository`/`AddExistingRepository`), and the attacker to later gain the ability to write content at that exact path (e.g., shared/network drives, multi-user machines, recreated temp directories, or path reuse after directory removal). This is a plausible, low-interaction scenario in shared-machine, shared-drive, or CI/build-agent-style environments, and unlike a fresh unsafe-repo prompt, the user gets **no** further warning because the exemption already exists.

### Recommendation
Provide a way to revoke trust: implement a `removeGlobalConfigValue`/`removeSafeDirectory` function that removes a specific path from `safe.directory` (analogous to `git config --global --unset-all safe.directory <path>`), expose it in Settings (e.g., a list of trusted directories with a "remove" action), and consider re-validating trust when directory ownership actually changes rather than trusting the path indefinitely based on a prior decision.

### Proof of Concept
1. On a shared machine or shared network drive, clone/create a repo at path `P` owned by another user; Desktop shows the "potentially unsafe" warning via `MissingRepository`/`AddExistingRepository` and the user clicks "Trust Repository", invoking `onTrustDirectory` → `addSafeDirectory(P)` [3](#0-2) .
2. `P` is now permanently listed in the user's global `safe.directory` config [5](#0-4) .
3. At a later time, the directory at `P` is deleted and recreated by a different (possibly malicious) user/process with a hostile `.git/config` or hook (e.g., `core.hooksPath` pointing to attacker scripts, or a `post-checkout` hook).
4. `getRepositoryType(P)` no longer returns `kind: 'unsafe'` because `safe.directory` already lists `P`, so Desktop treats it as a normal, safe repository and runs Git operations against it without any prompt, allowing the attacker's hooks/config to execute silently under the user's session.

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
