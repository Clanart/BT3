Confirmed: there is no `removeSafeDirectory` function anywhere in the codebase — once a path is trusted, it stays in the global `~/.gitconfig` `safe.directory` list permanently, and the "unsafe" classification returned by `getRepositoryType()` (`app/src/lib/git/rev-parse.ts:57-63`) is derived solely from Git's own ownership check against that global path list, with no binding to repository identity, content, or ownership hash.

### Title
Permanent, content-unbound `safe.directory` allow-listing lets a later attacker-controlled repository at a previously-trusted path execute hooks without any warning - (File: `app/src/lib/git/config.ts`)

### Summary
GitHub Desktop's "Trust Repository" flow calls `addSafeDirectory()` to permanently add a directory *path* to the global `safe.directory` Git allow-list whenever Git flags a repository as having "dubious ownership." This is the direct functional analog of the SphereX "recording phase" bug: a security decision is recorded once, keyed only by a coarse identifier (here, an absolute path string), and forever after trusted without re-validation against the actual content or ownership of whatever now occupies that path.

### Finding Description
The trust decision is implemented as: [1](#0-0) 

`addSafeDirectory` calls `addGlobalConfigValueIfMissing('safe.directory', path)`, writing the exact path string into the user's **global** `~/.gitconfig`. This is invoked from two UI entry points after the user clicks "Trust Repository/directory": [2](#0-1) [3](#0-2) 

The classification of a directory as "unsafe" is determined purely by whether Git's own `safe.directory` allow-list contains the path — not by any content or ownership fingerprint of the repository currently sitting there: [4](#0-3) 

Once a path is present in `safe.directory`, Git (and therefore `getRepositoryType`) will report `kind: 'regular'` for **any** repository subsequently found at that same path, regardless of who owns it or what it contains now. There is no `removeSafeDirectory` counterpart anywhere in the codebase (confirmed via search), no expiry, and no binding to a content hash, remote URL, or ownership SID — trust is keyed on the path string alone and persists indefinitely across repository lifecycles (delete/re-clone/re-add).

This is architecturally identical to the audited "recording phase" flaw: a broad allow-list is built up from individual, weakly-scoped approvals (one per path) with no mechanism to re-sanitize or revoke entries once conditions change, letting a later actor who controls the *content* at an already-approved location bypass the very check the allow-list was meant to enforce.

### Impact Explanation
Git's "dubious ownership" check exists specifically to stop automatic execution of repository-controlled code (hooks, `core.fsmonitor`, `core.pager`, `.gitattributes`-driven filters, etc.) when a directory could plausibly be planted or tampered with by another party. Because `addSafeDirectory` whitelists the *path* forever rather than validating on each use, an attacker who can place a malicious `.git` tree at a path that was previously trusted (e.g. a shared/mounted network drive, a synced cloud folder, or any location reused across users or sessions) causes GitHub Desktop to silently treat the malicious repository as fully trusted. Desktop then runs ordinary Git plumbing (checkout, commit, merge, fetch) against it without ever re-surfacing the "potentially unsafe" warning shown in `MissingRepository`/`AddExistingRepository`, enabling hook-based arbitrary code execution on the victim's machine with no further user consent.

### Likelihood Explanation
Requires two realistic, non-privileged conditions: (1) the victim previously trusted a directory whose ownership can later change or be reused (common on team file shares, USB drives shared between machines, or cloud-sync folders), and (2) the entry is never cleaned up because no removal path exists in the codebase. Both are plausible in normal collaborative workflows without requiring physical/local access to the victim's machine or any credential compromise — the attacker only needs write access to the shared location, not to the victim's host.

### Recommendation
Do not persist `safe.directory` trust indefinitely and unconditionally on path alone. Consider: (a) binding the trust decision to a content/ownership fingerprint (e.g., recorded owner SID/UID plus the initial commit SHA or `.git` object checksum) and re-validating it before treating the path as safe again; (b) exposing a UI/API to enumerate and revoke previously trusted directories, especially prompting re-confirmation when a previously-missing repository reappears at a trusted path (`_relocateRepository`/`cloneAgain`/`_updateRepositoryMissing` flows in `app/src/lib/stores/app-store.ts`); (c) scoping trust to remain valid only while the directory continues to exist and be tracked by Desktop, removing the `safe.directory` entry when the repository is removed from Desktop.

### Proof of Concept
1. Victim connects a shared/mounted path (e.g. `\\shared\team\project` or a synced Dropbox folder) containing a legitimate repo owned by a teammate; Desktop calls `getRepositoryType` → `kind: 'unsafe'` → victim clicks "Trust Repository" in `AddExistingRepository`/`MissingRepository`, invoking `addSafeDirectory(path)` (`app/src/lib/git/config.ts:181-189`), permanently adding the path to global `safe.directory`.
2. Victim later removes the repository from Desktop (`dispatcher.removeRepository`) or the folder is emptied; the `safe.directory` entry is **not** removed (no such code path exists in the repo).
3. An attacker with write access to that same shared path (e.g. a compromised or malicious collaborator on the share) replaces the directory contents with a new `.git` repository containing a malicious `post-checkout`/`post-merge` hook.
4. Victim re-adds or re-clones into the same path via `AddExistingRepository`, `MissingRepository.cloneAgain`, or CLI `open-repository` dispatch (`app/src/ui/dispatcher/dispatcher.ts:2059-2096`); `getRepositoryType` now returns `kind: 'regular'` (not `'unsafe'`) purely because the path is globally allow-listed, so no warning is shown and Desktop proceeds to run Git commands that trigger the malicious hook — silent code execution.

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

**File:** app/src/lib/git/rev-parse.ts (L18-65)
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
```
