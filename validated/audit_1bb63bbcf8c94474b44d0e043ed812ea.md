### Title
Trusting an "unsafe" repository directory persists forever with no revocation mechanism, allowing later attacker-controlled content at that path to auto-execute - (File: app/src/lib/git/config.ts)

### Summary
When Desktop encounters a repository whose directory ownership doesn't match the current user, Git flags it `unsafe` and Desktop shows a "Trust Repository" prompt. Accepting it calls `addSafeDirectory()`, which permanently appends the path to the global `safe.directory` git config entry. There is no code path anywhere in the app that ever removes an entry from `safe.directory` once added — the trust decision is a one-way, permanent grant, exactly mirroring the reported pattern of a "disable" action (or here, the *absence* of any "untrust" action) that fails to actually revoke access to a resource that keeps accumulating risk over its lifetime.

### Finding Description
`getRepositoryType()` detects the "dubious ownership" condition from git's stderr and returns `{ kind: 'unsafe', path }` [1](#0-0) . The UI (`AddExistingRepository` and `MissingRepository`) offers a one-click "Trust Repository" action that calls `addSafeDirectory(path)` [2](#0-1) [3](#0-2) .

`addSafeDirectory` writes the path into the **global** (machine/user-wide) `safe.directory` git config, using `addGlobalConfigValueIfMissing`, and the accompanying comment states this "will cause Git to ignore if the path is owned by a different user than the current" [4](#0-3) . There is no `removeSafeDirectory` or equivalent function anywhere in the codebase — the only writer to `safe.directory` is this add-only function, and no revocation UI, "untrust", or expiry logic exists.

Because trust is bound to a **path** (not to repository identity, content hash, or the specific owner encountered at trust time), once a path is trusted:
- If the directory is later deleted and repopulated by another user/process on a shared machine (e.g., a shared build server, a container mount, a synced network drive, or the original untrusted owner regains control after a temporary ownership change), git will silently treat any repository at that path as trusted forever, bypassing the "dubious ownership" protection that exists specifically to stop automatic execution of repository-controlled configuration/hooks.
- Desktop's own warning explicitly says "Adding untrusted repositories may automatically execute files in the repository" [5](#0-4) , confirming the security-relevant consequence of this trust grant, yet the grant is never re-evaluated or revocable through the product.

### Impact Explanation
This breaks the invariant that Desktop's "unsafe repository" ownership check should gate execution of repository-controlled content (hooks, `.gitattributes`-driven filters, etc.) based on *current* ownership. Instead, the trust decision, once granted, is permanent and path-scoped, so a future attacker who gains write access to a previously-trusted path (a very plausible scenario on shared/multi-user machines, CI runners, or restored/reused directories) inherits full trust and can achieve code execution via repository content without any further user consent or warning — silently corrupting the safety guarantee the ownership check was designed to provide.

### Likelihood Explanation
Moderate to high on any machine that isn't single-user/single-purpose: shared workstations, lab/kiosk machines, CI agents reusing workspace paths, or environments where directories are recycled between projects/users are common in enterprise use of Desktop. No special local/admin access is needed beyond what the original ownership-mismatch scenario already required to trigger the "unsafe" prompt once; after that one-time click, the risk window is unbounded in time.

### Recommendation
Do not persist trust indefinitely and path-only. Options: (1) bind the safe.directory grant to a stored fingerprint (owner SID/UID + creation time or repo identity) and re-validate it on each use, invalidating if ownership changes again after the grant; (2) surface trusted directories in Preferences with an explicit "Revoke trust" action that calls a new `removeSafeDirectory()`/`git config --global --unset safe.directory <path>`; (3) re-prompt trust when the on-disk owner changes even if the path was previously trusted.

### Proof of Concept
1. On a shared machine, User A creates a git repo at `C:\shared\repo` (or `/shared/repo`), owned by User A.
2. User B opens Desktop, adds the same path; git reports "dubious ownership", Desktop shows the unsafe warning, and User B clicks "Trust Repository" [2](#0-1) . `addSafeDirectory` writes `C:\shared\repo` to User B's global `safe.directory` config permanently [6](#0-5) .
3. Time passes; User A's repo is deleted and an attacker later creates a new git repository at the exact same path `C:\shared\repo` containing malicious hooks/config.
4. User B reopens Desktop (or the same tracked repository entry); `getRepositoryType` no longer reports `unsafe` for this path because it's in `safe.directory`, so no warning is shown, and any hook/config execution proceeds under full trust — despite the directory now being attacker-controlled.

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

**File:** app/src/ui/add-repository/add-existing-repository.tsx (L154-157)
```typescript
          )}{' '}
          appears to be owned by another user on your machine. Adding untrusted
          repositories may automatically execute files in the repository.
        </p>
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
