Confirmed: there is no `removeSafeDirectory`/revoke path anywhere in the codebase — `safe.directory` entries added via `addSafeDirectory` are never cleaned up, including when the repository is removed from Desktop (`removeRepository` in `app/src/lib/stores/repositories-store.ts` only touches Desktop's own repository list/database, not the git global config).

### Title
Permanent, path-keyed `safe.directory` trust grant is never revoked or re-validated, allowing silent auto-execution for any future repository placed at a previously trusted path - (File: `app/src/lib/git/config.ts`)

### Summary
When Desktop encounters a Git repository whose directory is owned by a different user (`getRepositoryType` returns `kind: 'unsafe'`), the user can click "Trust Repository" which calls `addSafeDirectory()`. This permanently appends the literal filesystem path to the global `safe.directory` Git config value [1](#0-0) . Once added, the check is bypassed unconditionally for that path — for the current repository and for anything that occupies that path afterward — because nothing in the codebase ever removes an entry from `safe.directory` or re-validates trust when the repository at that path changes.

### Finding Description
The protective mechanism ("is this repository safe to operate on/is a different owner") is analogous to `LendingTermOffboarding`'s poll-completion state: it is a binary decision meant to gate a risky action (in Git's case, letting hooks/filters/config execute automatically), but the code caches the "trusted" decision keyed only by path — not by repository identity, remote, commit history, or content hash.

- `addSafeDirectory(path)` calls `addGlobalConfigValueIfMissing('safe.directory', path)`, which persists the raw directory path to the user's global `.gitconfig` [1](#0-0) .
- The UI surfaces for this action (`AddExistingRepository.onTrustDirectory` and `MissingRepository.onTrustDirectory`) call this once and never revisit the decision [2](#0-1) [3](#0-2) .
- There is no code path (grep across the repo) that ever removes a path from `safe.directory`, nor any mechanism to invalidate trust when the directory's contents/ownership subsequently change.
- `removeRepository` in the repositories store only deletes Desktop's bookkeeping record; it does not touch the git global config, so the `safe.directory` grant outlives the tracked repository [4](#0-3) .

The warning text shown to the user explicitly acknowledges the danger of this trust: *"Adding untrusted repositories may automatically execute files in the repository"* [5](#0-4) . Despite that acknowledgment, the trust decision is bound only to a path string with no expiry, scope narrowing, or re-confirmation trigger — exactly the same class of bug as the report: a security gate whose state should be reset/re-evaluated when the underlying object changes, but instead persists indefinitely and can't be re-triggered.

### Impact Explanation
If a directory path is ever reused for a different repository — for example: a network-mounted/shared drive location, a CI/shared workstation temp directory, or a path the user later deletes and lets an attacker-controlled process/archive repopulate — any content at that path is now silently treated as trusted by Git/Desktop with no further prompt. Since the entire point of `safe.directory` is to gate execution of repository-controlled config/hooks that would otherwise be blocked for other users' directories, an attacker who can get their own repository content onto a previously-trusted path achieves automatic execution without any additional user interaction, matching the "code execution" and "silent corruption of what the user commits" impact classes in the reproduction criteria.

### Likelihood Explanation
Medium/low likelihood, but realistic: developers routinely reuse fixed clone paths (deploy scripts, CI runners, shared network shares called out explicitly in the changelog — "Support trusting repositories on network shares (Windows)" — and Desktop's own "Clone Again" feature in `missing-repository.tsx`, which re-clones to the exact same path that was previously trusted [6](#0-5) ). No admin rights, malware, or leaked credentials are required — only that the path be repopulated with different content, which is a normal outcome of everyday workflows like "remove and re-add", "clone again", or shared/mounted storage.

### Recommendation
Do not persist trust purely by directory path indefinitely. At minimum:
- Bind the trust decision to a more specific identifier (e.g., repository root commit / `.git` object hash / remote URL) rather than a bare path, or
- Re-validate on each `getRepositoryType` check whether the directory's git history/remote matches what was trusted originally, invalidating the `safe.directory` entry if it changed, and
- Remove the corresponding `safe.directory` entry when the repository is removed from Desktop via `removeRepository`.

### Proof of Concept
1. On a machine with a shared/network path `\\share\repoA` (or any path that can later hold different content), open it in Desktop; Git reports it as `unsafe` (different owner) via `getRepositoryType`.
2. Click "Trust Repository" — `onTrustDirectory` → `addSafeDirectory(path)` permanently writes the path into `safe.directory` in the global gitconfig [2](#0-1) [7](#0-6) .
3. Remove the repository from Desktop and delete the directory contents.
4. Have an attacker-controlled process/user repopulate the exact same path with a malicious repository (including hooks/config designed to auto-execute), or use Desktop's own "Clone Again" to reclone at the identical path.
5. Re-open Desktop and add/clone at that path again — Git no longer flags it as unsafe because the path is already in `safe.directory`; no new prompt is shown, and the attacker's repository content/hooks operate as trusted.

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

**File:** app/src/ui/add-repository/add-existing-repository.tsx (L145-167)
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

**File:** app/src/lib/stores/repositories-store.ts (L1-1)
```typescript
import {
```
