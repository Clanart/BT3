## Finding

`GIT_CLONE_PROTECTION_ACTIVE` is a Git-native safety switch (added upstream as part of the fixes for the 2024 clone/submodule RCE family, e.g. CVE-2024-32002) that makes `git clone` refuse to write into a `.git` directory that a malicious repository tries to redirect via symlinks/case-insensitive collisions/nested submodule tricks during a recursive clone. GitHub Desktop's `clone()` helper explicitly disables this protection on every clone: [1](#0-0) 

This is exactly the same bug class as the `vePeg` report: a security-relevant guard (`_locked.end > block.timestamp` in the seed; `GIT_CLONE_PROTECTION_ACTIVE` in Git) is short-circuited for a class of operations it should also apply to — here, the guard is turned off outright for **every** clone Desktop performs, including `--recursive` clones of arbitrary/untrusted URLs the user pastes or opens via `x-github-client://openRepo` deep links or "Clone" from a search result.

### Title
`git clone` explicitly disables Git's built-in clone/submodule symlink protection (`GIT_CLONE_PROTECTION_ACTIVE=false`) - (File: `app/src/lib/git/clone.ts`)

### Summary
GitHub Desktop's `clone()` function sets `GIT_CLONE_PROTECTION_ACTIVE: 'false'` in the environment for every `git clone --recursive` invocation, unconditionally disabling a Git-native hardening flag designed to stop malicious repositories from escaping their working tree during (recursive/submodule) clone operations.

### Finding Description
The `clone()` function builds the execution environment by merging `envForRemoteOperation(url)` with a hardcoded override: `GIT_CLONE_PROTECTION_ACTIVE: 'false'` [2](#0-1) . It then always passes `--recursive` to `git clone` [3](#0-2) , meaning submodules (which can point to attacker-controlled URLs different from the top-level repo) are also fetched and checked out with this protection disabled.

`GIT_CLONE_PROTECTION_ACTIVE` is a Git environment variable used to gate protections against maliciously-crafted repositories/submodules that attempt to write files outside the intended worktree (e.g. via crafted paths, symlinked `.git`/`gitdir` files, or nested submodule tricks encountered as part of the 2024 clone RCE disclosures). Explicitly forcing it to `'false'` means Desktop is opting *out* of the very protection Git ships to catch these cases, for every clone, regardless of whether the origin is a trusted GitHub host or an arbitrary attacker-supplied URL.

Note that the file's only other guard, `isClonePathSensitive()`, only checks the *destination path chosen by the user* (blocking clones into `~`, `~/.ssh`, etc.) [4](#0-3) ; it does nothing to prevent a malicious repository's *contents* (submodule config, symlinked paths) from writing outside the clone destination during the clone itself — that is precisely the class of attack `GIT_CLONE_PROTECTION_ACTIVE` exists to stop.

### Impact Explanation
If exploitable, this allows a fully attacker-controlled repository (the exact "attacker controls a cloned/fetched repository" primitive) to escape the intended clone directory during `git clone --recursive`, potentially writing arbitrary files outside the repo (e.g. overwriting hook files, config, or other filesystem locations reachable through symlink/gitdir redirection), which can lead to code execution the next time Git or Desktop touches the corrupted state. This matches the required impact bar: file write outside the repo / code execution originating from a hostile clone target.

### Likelihood Explanation
Likelihood is Medium: cloning is a routine, unprivileged action a user performs by pasting a URL or clicking a `x-github-client://openRepo`-style deep link, and `--recursive` submodule fetching happens automatically without extra user interaction. The disabling override applies unconditionally to every clone Desktop performs, so no unusual steps are required beyond convincing a user to clone the malicious repository (or its submodule), which is well within GitHub Desktop's normal usage pattern and the accepted attacker model (repo/URL is attacker controlled).

### Recommendation
Remove the `GIT_CLONE_PROTECTION_ACTIVE: 'false'` override so Desktop honors Git's built-in protection during `clone()` (and audit `fetch.ts`/`pull.ts`/`submodule.ts` for any similar overrides). If the override exists to work around a specific compatibility issue, scope it narrowly (e.g. only for verified, first-party GitHub-hosted repositories) rather than disabling it globally for all clone operations including recursive submodule fetches from arbitrary URLs.

### Proof of Concept
1. Craft a malicious Git repository containing a submodule (or nested `.gitmodules`/symlink trick) engineered to trigger the class of path-escape behavior that `GIT_CLONE_PROTECTION_ACTIVE` is designed to block in vanilla Git.
2. Have the victim clone the repository in GitHub Desktop (via URL paste, "Clone repository" dialog, or an `x-github-client://openRepo` deep link).
3. `clone()` runs `git -c init.defaultBranch=... clone --recursive -- <url> <path>` with `GIT_CLONE_PROTECTION_ACTIVE=false` set in the environment [1](#0-0) , so the protection that would normally reject/abort the malicious layout is bypassed, whereas a plain `git clone --recursive` without this override would have been protected.
4. The malicious repository content is written outside the intended clone directory (destination-path validation via `isClonePathSensitive()` does not help here because it only inspects the destination the user chose, not what the untrusted repo's submodule graph does during the clone).

### Citations

**File:** app/src/lib/git/clone.ts (L16-47)
```typescript
function isClonePathSensitive(unresolvedClonePath: string): boolean {
  const clonePath = Path.resolve(unresolvedClonePath).toLowerCase()
  const home = Path.resolve(homedir()).toLowerCase()

  if (clonePath === home) {
    return true
  }

  const sensitiveLocations = [
    Path.join(home, '.ssh'),
    Path.join(home, '.gnupg'),
    Path.join(home, '.config'),
    Path.join(home, '.config', 'git'),
    Path.join(home, '.gitconfig'),
  ]

  if (__WIN32__) {
    const appData = process.env.APPDATA
    if (appData) {
      sensitiveLocations.push(appData.toLowerCase())
      sensitiveLocations.push(Path.join(appData, 'gnupg').toLowerCase())
    }
  }

  for (const sensitive of sensitiveLocations) {
    if (clonePath === sensitive || clonePath.startsWith(sensitive + Path.sep)) {
      return true
    }
  }

  return false
}
```

**File:** app/src/lib/git/clone.ts (L81-93)
```typescript
  const env = {
    ...(await envForRemoteOperation(url)),
    GIT_CLONE_PROTECTION_ACTIVE: 'false',
  }

  const defaultBranch = options.defaultBranch ?? (await getDefaultBranch())

  const args = [
    '-c',
    `init.defaultBranch=${defaultBranch}`,
    'clone',
    '--recursive',
  ]
```
