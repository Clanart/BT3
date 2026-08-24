### Title
`safe.directory` trust decisions are granted per-path forever with no ownership re-check or revocation — reintroduces the Git ownership TOCTOU the feature exists to close - (File: `app/src/lib/git/config.ts`)

### Summary
The external report's broken invariant is: an access-control grant (`allowedValidators[signer][validator] = approval`) is checked once at use-time from mutable state, and there's a window where a *stale* grant can be exploited before a revocation lands. GitHub Desktop has a structurally similar (and in one respect worse) pattern in its "Trust Repository" flow for Git's dubious-ownership protection: once a directory path is approved, Desktop writes it to the user's **global** `~/.gitconfig` `safe.directory` list, and every future Git invocation against that path is trusted purely by path string comparison — with no re-check of current ownership and **no UI path to revoke the grant**.

### Finding Description
When `getRepositoryType` detects Git's `dubious ownership` error, Desktop offers a "Trust Repository" action: [1](#0-0) 

Clicking it calls `addSafeDirectory`, which unconditionally appends the path to the **global** `safe.directory` config: [2](#0-1) 

This is invoked from two UI surfaces, `AddExistingRepository.onTrustDirectory` and `MissingRepository.onTrustDirectory`: [3](#0-2) [4](#0-3) 

The invariant Git's `safe.directory` mechanism is meant to enforce is: *"only trust the repository at this path if it is still owned by the current user."* Desktop's approval is granted based on the ownership check **at the moment the user clicks Trust**, but the resulting `safe.directory` entry is a bare path string with no binding to an owner, commit, or fingerprint, and it never expires. Grepping the codebase, `addSafeDirectory` is only ever *added to*; there is no `removeSafeDirectory`/revocation function anywhere in the app. So the check-then-use gap is not "seconds while a transaction mines" as in the WETH9 report — it is unbounded and permanent: any future content that later lands at that same path (a shared network drive, a cloud-sync folder such as OneDrive/Dropbox, a CI/build workspace reused across users, or any location where a different, untrusted party can subsequently write files) will silently satisfy Git's ownership check forever, without prompting the user again.

This defeats the entire purpose of upstream Git's `safe.directory`/dubious-ownership defense (introduced to stop repository-supplied configuration/hooks from executing when a directory is populated by someone other than the invoking user, i.e. CVE-2022-24765-class attacks). Desktop's implementation collapses "trust this specific repository, right now, because I verified its ownership" into "trust this path, forever, regardless of who writes to it later."

### Impact Explanation
If an attacker (or a different account) can later place a malicious Git repository — with attacker-controlled hooks, `.gitattributes` filters, or `core.fsmonitor`/`core.hooksPath` config — at a path that was previously trusted (typical for shared network shares, cloud-synced folders, or reused CI/build directories, which Desktop explicitly supports trusting per its changelog), Git will treat that new, attacker-supplied content as fully trusted and can execute arbitrary code the next time Desktop (or the user's `git`) touches that path — with no new prompt, no ownership re-verification, and no in-app way to revoke the earlier grant. This is the same class of outcome the original report worries about (a stale/compromised authorization being usable indefinitely), but here the "revocation" path doesn't even exist, so there is no race to win — the grant is unconditionally durable.

### Likelihood Explanation
Requires a specific but realistic setup: a previously-"trusted" path must later become writable/repopulatable by another party (shared drive, cloud-sync folder, multi-tenant build agent, or reinstalled/removable media reusing the same mount path) — not local/physical access to the victim's already-authenticated session, not admin rights, and not prior malware on the host. Desktop's own changelog documents deliberate support for trusting repositories "on network shares" and CI-style directory reuse, which increases the realism of this scenario. That said, this does depend on an external condition (path reuse by another writer) that I could not verify is common in the wild from the codebase alone — this is the main caveat on likelihood.

### Recommendation
- Bind the trust decision to more than a path string — e.g., record the repository's initial commit/remote fingerprint or filesystem owner ID alongside the trusted path, and re-validate at each use rather than trusting by path alone.
- Add a "Manage trusted directories" surface so users can audit and revoke `safe.directory` entries from within Desktop (currently there is no `removeSafeDirectory`-equivalent).
- Consider scoping trust to session/workspace lifetime for shared/synced/CI-style paths rather than writing to the global config permanently.

### Proof of Concept
1. User opens a Git repository at path `P` on a shared/synced location (e.g., a corporate network share or cloud-sync folder) that is currently owned by them; Desktop reports it as "unsafe," and the user clicks **Trust Repository**, calling `addSafeDirectory(P)` (`app/src/lib/git/config.ts:181-189`), which appends `P` to the global `safe.directory` list permanently.
2. At a later time, another party with write access to that same shared/synced location (not the victim's local machine) deletes/replaces the contents at `P` with a malicious repository containing attacker-controlled hooks/config.
3. The victim reopens or refreshes the repository at `P` in Desktop. `getRepositoryType` (`app/src/lib/git/rev-parse.ts`) no longer reports "unsafe" because `P` is already in `safe.directory`, so Desktop performs Git operations against it without any new prompt, and the attacker's repository-embedded hooks/config are trusted and can execute.

Since I could not find any code path that removes or re-validates `safe.directory` entries, I'm confident this analog is grounded in the actual implementation rather than speculation, though the exploitability specifically depends on an external actor being able to repopulate a previously trusted path.

### Citations

**File:** app/src/lib/git/rev-parse.ts (L56-63)
```typescript

    const unsafeMatch =
      /fatal: detected dubious ownership in repository at '(.+)'/.exec(
        result.stderr
      )
    if (unsafeMatch) {
      return { kind: 'unsafe', path: unsafeMatch[1] }
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
