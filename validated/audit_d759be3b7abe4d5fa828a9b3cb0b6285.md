Confirmed: `removeRepository` in `app/src/lib/stores/repositories-store.ts` only touches the local IndexedDB record and never calls `removeSafeDirectory` (in fact no such function exists anywhere in the codebase). Combined with `addSafeDirectory` writing permanently to the user's **global** `~/.gitconfig`, this gives a solid DSClock-style analog: a permanent, path-keyed trust cache that is never invalidated even though the content living at that path can change.

### Title
Permanent, unrevoked `safe.directory` trust cache lets an attacker who reuses a previously-trusted path execute arbitrary Git hooks/config - (File: app/src/lib/git/config.ts)

### Summary
GitHub Desktop's "Trust Repository" flow permanently whitelists a filesystem path in the user's **global** Git config via `safe.directory`. This whitelist is never revoked, even when the user removes the repository from Desktop. If an attacker can later place a different, hostile Git repository at that same path (e.g., a shared drive, removable media, a temp/scratch directory, or any location the user previously trusted and later deleted/re-provisioned), Desktop and the embedded `git` binary will treat the new, attacker-owned repository as trusted and skip Git's "dubious ownership" protection — the exact mechanism designed to stop automatic execution of repository-supplied hooks/config for content owned by someone else.

### Finding Description
`addSafeDirectory` appends the path to the global `safe.directory` config value and is called from the "Trust Repository" UI action: [1](#0-0) 

This is invoked both from `MissingRepository.onTrustDirectory` and `AddExistingRepository.onTrustDirectory`: [2](#0-1) [3](#0-2) 

The entry is added via `addGlobalConfigValueIfMissing('safe.directory', path)`, which persists indefinitely in the global gitconfig file, keyed purely by path string — analogous to `DSProxyCache.write` keying an address by a hash with no expiry or ownership re-check: [1](#0-0) 

Crucially, there is no corresponding "untrust"/removal step anywhere in the codebase. `RepositoriesStore` only deletes the local IndexedDB metadata for a repository and never touches `safe.directory`: [4](#0-3) 

`getRepositoryType`, the function used everywhere in Desktop (add repository, relocate repository, missing-repository check, clone flows) to decide whether a directory is safe to operate on, relies entirely on Git's own dubious-ownership detection, which is bypassed once the path is in `safe.directory`: [5](#0-4) 

Because the trust decision is bound to a **path string** rather than to the actual repository identity/content (e.g., no check of the `.git` directory's original owner, no hash of remote URL/HEAD, no verification that it's still "the same" repository the user trusted), the invariant "a path marked safe still refers to the repository the user vetted" silently breaks the moment someone else's content occupies that path. This mirrors the DSClock bug: the cache (here, `safe.directory`) returns a stale trust decision for a *location* whose backing content has been swapped out by an attacker, and every subsequent Desktop git invocation on that path (`getRepositoryType`, `git status`, `git fetch`, hook execution, `core.fsmonitor`/`core.sshCommand`/credential helpers defined in the attacker's repo config) is "delegated" to attacker-controlled content without re-validation.

### Impact Explanation
Once `safe.directory` contains the path, Git will run with the local repository config of whatever now sits there — including config keys that trigger code execution such as `core.fsmonitor`, `core.sshCommand`, custom credential helpers, or hooks invoked implicitly by Desktop's git operations (fetch/pull/checkout/status). This can lead to arbitrary command execution under the user's account and/or exfiltration of Git credentials, satisfying the "code execution via attacker-controlled repository content" impact bar.

### Likelihood Explanation
Likelihood is moderate: it requires (1) the user to have trusted a path once (a supported, intended workflow for legitimate shared/network repos), and (2) the same path later being repopulated by attacker-controlled content — plausible for shared drives, USB/removable media, CI scratch directories, temp folders, or any multi-user/multi-tenant filesystem location, none of which require local admin rights, prior malware, or leaked credentials. The attacker only needs write access to a path the victim previously trusted and later vacated (e.g., after deleting/relocating the repo in Desktop, which — per the analysis above — never revokes the trust entry).

### Recommendation
- When a repository is removed from Desktop (or found "missing"/relocated to a different actual directory), remove the corresponding `safe.directory` entry via a new `removeSafeDirectory` helper, mirroring `addSafeDirectory`.
- Bind trust to repository identity, not just path: before relying on an existing `safe.directory` entry, verify some invariant about the repository (e.g., its `.git` directory owner, or a previously recorded remote URL/initial commit) hasn't changed; if it has, prompt the user to re-confirm trust.
- Consider scoping trust additions to `--local`/per-machine state that Desktop fully owns and can garbage-collect, rather than mutating the user's global `~/.gitconfig` indefinitely.

### Proof of Concept
1. User adds/opens a shared or removable-media repository at path `P`; Desktop reports it as "unsafe" (different owner) and the user clicks "Trust Repository", causing `addSafeDirectory(P)` to add `P` to the global `safe.directory` list permanently: [6](#0-5) .
2. User later removes that repository from Desktop (or the drive is reformatted/reused) — no code path ever calls anything to strip `P` from `safe.directory`.
3. Attacker (another user of the shared drive, or anyone with write access to `P` after it's vacated) creates a new Git repository at the same path `P`, with a malicious `.git/config` (e.g., `core.fsmonitor = calc.exe` or a malicious `core.sshCommand`) and/or hostile hooks.
4. Victim reopens/re-adds the same path `P` in Desktop (e.g., via "Add Existing Repository" or it still being in recent-repositories list). `getRepositoryType(P)` reports `kind: 'regular'` without any "unsafe" warning because `P` is already in `safe.directory`: [7](#0-6) .
5. Any subsequent Desktop git operation against `P` (fetch, status, checkout) executes with the attacker's local config/hooks, achieving code execution under the victim's account with no additional user consent beyond opening the (previously legitimate) path again.

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

**File:** app/src/lib/stores/repositories-store.ts (L36-59)
```typescript
/** The store for local repositories. */
export class RepositoriesStore extends TypedBaseStore<
  ReadonlyArray<Repository>
> {
  // Key-repo ID, Value-date
  private lastStashCheckCache = new Map<number, number>()

  /**
   * Key is the GitHubRepository id, value is the protected branch count reported
   * by the GitHub API.
   */
  private branchProtectionSettingsFoundCache = new Map<number, boolean>()

  /**
   * Key is the lookup by the GitHubRepository id and branch name, value is the
   * flag whether this branch is considered protected by the GitHub API
   */
  private protectionEnabledForBranchCache = new Map<string, boolean>()

  private emitQueued = false

  public constructor(private readonly db: RepositoriesDatabase) {
    super()
  }
```

**File:** app/src/lib/git/rev-parse.ts (L18-76)
```typescript
export async function getRepositoryType(path: string): Promise<RepositoryType> {
  if (!(await directoryExists(path))) {
    return { kind: 'missing' }
  }

  try {
    const result = await git(
      ['rev-parse', '--is-bare-repository', '--show-cdup', '--git-dir'],
      path,
      'getRepositoryType',
      { successExitCodes: new Set([0, 128]) }
    )

    if (result.exitCode === 0) {
      // Bare repositories will not include gitdir so we handle that separately
      if (result.stdout.startsWith('true\n')) {
        return { kind: 'bare' }
      }

      // --is-bare-repository and --show-cdup each produce a single line but
      // --git-dir could theoretically contain newlines so we parse the known
      // fields first and treat the remainder as the git dir. We use [\s\S]*
      // instead of .* for the git dir capture group because .* doesn't match
      // newlines whereas [\s\S]* matches any character including newlines.
      const match = result.stdout.match(/^(true|false)\n(.*)\n([\s\S]*)\n$/)

      if (match) {
        const [, isBare, cdup, gitDir] = match

        return isBare === 'true'
          ? { kind: 'bare' }
          : {
              kind: 'regular',
              topLevelWorkingDirectory: resolve(path, cdup),
              gitDir: resolve(path, gitDir),
            }
      }
    }

    const unsafeMatch =
      /fatal: detected dubious ownership in repository at '(.+)'/.exec(
        result.stderr
      )
    if (unsafeMatch) {
      return { kind: 'unsafe', path: unsafeMatch[1] }
    }

    return { kind: 'missing' }
  } catch (err) {
    // This could theoretically mean that the Git executable didn't exist but
    // in reality it's almost always going to be that the process couldn't be
    // launched inside of `path` meaning it didn't exist. This would constitute
    // a race condition given that we stat the path before executing Git.
    if (err.code === 'ENOENT') {
      return { kind: 'missing' }
    }
    throw err
  }
}
```
