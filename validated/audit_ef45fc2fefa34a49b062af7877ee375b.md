### Title
Permanent, path-based `safe.directory` trust grant with no revocation mechanism enables silent hook execution for future untrusted owners of that path - (File: `app/src/lib/git/config.ts`)

### Summary
GitHub Desktop lets a user "trust" a directory that Git has flagged as having "dubious ownership" (a different OS-level owner than the current user). Trusting adds the path to the *global* `safe.directory` git config, which then permanently exempts that exact path from ownership checks for all future Git operations — with no UI or code path in the app to remove/revoke that trust later.

### Finding Description
When `getRepositoryType` detects Git's `dubious ownership` error, Desktop surfaces a "Trust Repository" action in both the add-existing-repository flow and the missing-repository flow. Clicking it calls `addSafeDirectory`, which appends the path to the global `safe.directory` list via `addGlobalConfigValueIfMissing('safe.directory', path)`: [1](#0-0) 

This write is permanent, global (affects the whole machine/user, not just the current repository record), and keyed purely on filesystem **path**, not on any content, remote URL, or repository identity: [2](#0-1) 

The trust decision is triggered from `AddExistingRepository.onTrustDirectory` and `MissingRepository.onTrustDirectory`: [3](#0-2) [4](#0-3) 

Once a path is in `safe.directory`, Git (and therefore Desktop, since `getRepositoryType`/`rev-parse` no longer errors for that path) will treat **any future content at that path** as safe, regardless of who owns it. Searching the codebase, there is no `removeSafeDirectory` function or any UI affordance to revoke a previously-granted trust — the only related functions are `addSafeDirectory` and the generic `removeGlobalConfigValue`/`removeConfigValue`, neither of which is wired to `safe.directory` cleanup anywhere in the app.

This mirrors the audited bug-class exactly: a trust/whitelist decision made once (`unregisterKey` adding/removing nodes only during a limited window vs. Desktop's permanent, one-way "trust exception") persists indefinitely with no mechanism for the system (or user) to walk it back, and the "malicious entity" (an attacker who later gains control of that same path) inherits the trust silently.

### Impact Explanation
Paths that are commonly reused across users/sessions — shared network drives, common temp directories, CI checkout paths, `/tmp` clones, USB drives, multi-user machines, or a directory that was deleted and later recreated by a different account — can be trusted once by a legitimate owner and then silently reused by an attacker who gains write access to that same path later (e.g., another local account, a compromised shared mount, or a directory recycled by an installer/uninstaller). Because Git's dubious-ownership warning is the primary safeguard against executing repository-controlled content (hooks, `.gitattributes` filters, etc.) from directories not owned by the current user, permanently disabling that check for a path removes the warning for all subsequent, unrelated content at that path — with the user having no way to see or undo the exception inside Desktop.

### Likelihood Explanation
Exploitation requires a specific setup (shared/reused directory path plus a later change of the actual owner/content at that path) rather than a simple one-click social-engineering trick, so likelihood is moderate rather than high. However, the underlying design flaw — a single, irreversible, path-keyed trust decision with no expiry or revocation surface — is present in shipped code and reachable purely through normal add-repository/missing-repository flows that every user encounters when Git reports dubious ownership.

### Recommendation
- Provide a way in Desktop's UI (e.g., in repository settings or a dedicated "Trusted Directories" management screen) to list and remove entries previously added to `safe.directory` via `addSafeDirectory`.
- Consider re-validating trust when repository identity changes (e.g., different `.git` object database root, different remote URL, or change in owner) rather than relying purely on the static path.
- Consider scoping trust decisions more narrowly (e.g., confirm the trust exception only applies while the directory's current owner matches the one recorded at trust time) instead of an unconditional, permanent global exemption.

### Proof of Concept
1. User adds an existing repository at a shared/reusable path (e.g., a network share or `/tmp/shared-repo`) owned by another OS user; Desktop reports it as unsafe.
2. User clicks "Trust repository" (`onTrustDirectory` in `app/src/ui/add-repository/add-existing-repository.tsx`), which calls `addSafeDirectory(path)` and permanently writes the path to the global `safe.directory` git config [5](#0-4) .
3. Later, the original directory content at that path is deleted and an attacker (another local user, or someone with write access to the shared mount) creates a new, malicious Git repository at the exact same path, including malicious hooks/config.
4. Because the path is already in `safe.directory`, `getRepositoryType` no longer returns `unsafe` for it, and Desktop performs Git operations against it without any warning, allowing the attacker-controlled hooks/config to execute as though the repository were trusted — with no way for the user to have revoked the earlier trust grant through the app.

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
