### Title
Trust granted via `safe.directory` is permanently bound to a path and is never revoked, allowing stale trust to be silently reapplied to different content at the same location - ([File: app/src/lib/git/config.ts](app/src/lib/git/config.ts))

### Summary
The reported OpenDollar bug is a case where a permission (`safeCan[owner][safe][user]`) is scoped to an identifier (`safe id`) but never reset when that identifier's ownership/content changes, so stale trust silently persists across a resource-identity transition. GitHub Desktop has the same broken invariant for the `safe.directory` trust mechanism: once a filesystem path is marked trusted, `git`/Desktop never re-validates or clears that trust when the actual content/ownership at that path changes, and Desktop provides no way to revoke it.

### Finding Description
`addSafeDirectory()` permanently appends the repository path to the user's **global** git config `safe.directory` list the first time the user clicks "Trust Repository" / "add an exception for this directory": [1](#0-0) 

This value is written once and, per `git`'s own semantics, causes `git` to permanently skip the "dubious ownership" check for that exact path — for every future invocation, forever. A `grep` across the repository confirms there is **no `removeSafeDirectory`, no expiry, and no re-validation logic anywhere in the codebase** — the trust decision, once made, is never revisited even though `getRepositoryType()` is called again on every repository load/refresh to re-derive the "unsafe" state: [2](#0-1) 

The UI surfaces for granting this trust are `AddExistingRepository.onTrustDirectory` and `MissingRepository.onTrustDirectory`: [3](#0-2) [4](#0-3) 

Critically, `MissingRepository` also exposes a **"Clone Again"** action that re-clones a remote URL into the *exact same path* previously recorded for the repository: [5](#0-4) [6](#0-5) 

The broken invariant: the trust grant is keyed only on **path**, not on any invariant of the repository's actual content or ownership. Git's own warning text, shown in the UI itself, acknowledges the risk this check exists to prevent — "Adding untrusted repositories may automatically execute files in the repository" — yet nothing in Desktop ever removes a path from `safe.directory` once granted, even when:
- the repository at that path is removed and a completely different repository/content later occupies the same path (e.g. via "Clone Again", "Locate…", reused temp/build directories, restored backups, or shared/synced folders),
- ownership of the underlying directory changes,
- the repository is deleted from Desktop's list and re-added later.

This is structurally identical to the ODSafeManager bug: a privilege (`safe.directory` trust / `safeCan` permission) is anchored to a reusable identifier (path / safe-id) and is never cleared when the resource behind that identifier is effectively "returned" to a different, potentially untrusted, owner or content set.

### Impact Explanation
If an attacker can cause different content to occupy a path Desktop previously marked as trusted (for example, by controlling what gets checked out/cloned into a shared, synced, reused, or predictable path, or by supplying repository content that a user re-clones into the same recorded path after the original was removed), Git will silently skip its dubious-ownership protection for that path forever. Combined with Desktop's execution of git hooks/config during normal operations (fetch, checkout, clone, LFS, etc.), this can lead to code execution using content the user never explicitly re-vetted, since the "Trust Repository" consent was only ever given once, for different content.

### Likelihood Explanation
Exploitation requires a scenario where an attacker-influenced repository/content ends up at a path Desktop already trusts (e.g., reused clone-again path, synced/shared directories, or predictable temp/checkout locations) — no local/admin access or already-planted malware within Desktop's own control is required, only control over what content the user ends up cloning/fetching into that already-trusted location. This is a plausible but not trivial precondition, consistent with a medium-severity finding, analogous to the "multiple unlikely conditions" characterization the judge gave the original ODSafeManager report.

### Recommendation
- Do not persist `safe.directory` trust indefinitely and unconditionally by path alone.
- Re-validate trust whenever the repository is re-associated with a path (e.g., on "Clone Again", "Locate…", or re-add flows) by removing the corresponding `safe.directory` entry before the operation and re-prompting the user if `getRepositoryType()` reports `unsafe` again.
- Consider binding trust to a more durable identity (e.g., a hash of the initial commit / initial remote URL) rather than solely the filesystem path, or at minimum provide a `removeSafeDirectory` path invoked whenever Desktop forgets/relocates a repository.

### Proof of Concept
1. User adds/clones a legitimate repository at path `P`. Git reports `unsafe` ownership (e.g., due to a shared/managed filesystem); user clicks "Trust Repository", which calls `addSafeDirectory(P)`, permanently adding `P` to the global `safe.directory` list.
2. User later removes the repository from Desktop (or the directory is deleted/cleared) but Desktop still remembers `gitHubRepository.cloneURL` and `path === P` for that entry (`MissingRepository` state).
3. An attacker who can influence what gets cloned into `P` (e.g., by controlling redirected clone content, a shared/synced folder, or a maliciously prepared archive placed at a well-known reused path) causes different, attacker-controlled repository content (with malicious hooks/config) to occupy `P`.
4. User clicks "Clone Again" (`MissingRepository.cloneAgain` → `dispatcher.cloneAgain` → `AppStore._cloneAgain`), which clones into the same path `P`.
5. Because `P` is already in `safe.directory`, `getRepositoryType()` never returns `unsafe` for the new content, so no "Trust Repository" warning is ever re-shown — the attacker's content is treated as fully trusted and any subsequent git operation (fetch, checkout, LFS filters, hooks) executes without the ownership check that would otherwise have warned the user.

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

**File:** app/src/ui/missing-repository.tsx (L169-188)
```typescript
  private cloneAgain = async () => {
    const gitHubRepository = this.props.repository.gitHubRepository
    if (!gitHubRepository) {
      return
    }

    const cloneURL = gitHubRepository.cloneURL
    if (!cloneURL) {
      return
    }

    try {
      await this.props.dispatcher.cloneAgain(
        cloneURL,
        this.props.repository.path
      )
    } catch (error) {
      this.props.dispatcher.postError(error)
    }
  }
```

**File:** app/src/lib/stores/app-store.ts (L8248-8266)
```typescript
  public async _cloneAgain(url: string, path: string): Promise<void> {
    const { promise, repository } = this._clone(url, path)
    await this._selectRepository(repository)
    const success = await promise
    if (!success) {
      return
    }

    const repositories = this.repositories
    const found = repositories.find(r => r.path === path)

    if (found) {
      const updatedRepository = await this._updateRepositoryMissing(
        found,
        false
      )
      await this._selectRepository(updatedRepository)
    }
  }
```
