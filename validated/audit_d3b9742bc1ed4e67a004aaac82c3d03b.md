## Analysis

The seed report describes a broken invariant: a function performs a privileged operation (approving a token) without checking it against an allow-list, so any value is accepted when only some should be. The strongest structural analog in GitHub Desktop is a place where the app unconditionally disables a security check that Git itself performs on untrusted repository content, rather than restricting/validating it.

That is exactly what happens in `clone()`: [1](#0-0) 

Desktop hardcodes `GIT_CLONE_PROTECTION_ACTIVE: 'false'` for every clone it performs, and always clones with `--recursive`: [2](#0-1) 

### Title
GitHub Desktop unconditionally disables Git's clone-time symlink/hook protection via `GIT_CLONE_PROTECTION_ACTIVE=false` - (File: `app/src/lib/git/clone.ts`)

### Summary
`clone()` in `app/src/lib/git/clone.ts` builds the environment for every `git clone --recursive` invocation and force-sets `GIT_CLONE_PROTECTION_ACTIVE: 'false'` unconditionally, regardless of what repository/submodule structure is being cloned. This is the same class of bug as the seed report: a function that should only permit a validated/allow-listed value (protection state reflecting whether Git's own clone hardening has actually run) instead accepts and forwards an unconditionally hardcoded, attacker-agnostic value, effectively turning off a defense that Git performs on content that comes entirely from an untrusted remote.

### Finding Description
`git clone --recursive` recurses into nested submodules, each performed as its own clone step. Git's clone-protection mechanism (introduced to guard against maliciously crafted repositories where a submodule's `.git` entry, hooks path, or worktree metadata is a symlink pointing outside the intended destination) relies on this environment signal being managed faithfully across the top-level and nested clone invocations so the check is actually exercised for content supplied by the remote. Desktop's `clone()` does not compute or forward a value derived from Git's own protection state — it always injects the literal string `'false'`: [3](#0-2) 

Notably, elsewhere in the same file Desktop *does* implement an explicit, careful allow-list-style guard against a related class of attack — `isClonePathSensitive()` blocks resolving the clone destination itself into sensitive directories like `~/.ssh` or `~/.gnupg`: [4](#0-3) 

That guard only validates the destination path the *user* supplied — it does nothing to constrain what a malicious remote's submodule tree can do internally during the recursive clone, which is precisely the surface `GIT_CLONE_PROTECTION_ACTIVE` is meant to defend. By force-disabling that separate, remote-content-facing protection for every single clone, Desktop removes a layer of defense specifically over content it does not control: the cloned repository and its nested submodules.

### Impact Explanation
An attacker who controls a repository (directly, or as a fork/PR head that Desktop clones, e.g. via `clone()` invoked from the UI's "Clone repository" flow or PR-based fork cloning) can structure a submodule so that paths normally protected by Git's clone hardening resolve outside the intended destination directory. With the protection forced off, this can result in file writes or symlink placement outside the repo working directory during the clone, i.e. corruption or escape of the sandboxed clone destination — matching the "file write outside the repo" impact category in the report's valid-impact list. This requires no local access, no prior malware, and no unusual user action beyond cloning a repository the attacker controls, which is a normal, expected Desktop workflow.

### Likelihood Explanation
Every clone Desktop performs goes through this exact code path — `clone()` is unconditionally invoked with `--recursive` and the hardcoded `GIT_CLONE_PROTECTION_ACTIVE: 'false'`, so there is no scenario where the protection is active for a Desktop-initiated clone. This makes the path always reachable whenever a user clones any attacker-influenced repository (public repo, fork, or PR branch), which is a core, frequently used Desktop feature.

### Recommendation
Do not force `GIT_CLONE_PROTECTION_ACTIVE` to `'false'`. Instead, let Git's own default protection behavior apply (i.e., omit the override entirely, or only set it based on a value Desktop has itself verified is safe for the specific operation), so the security check Git performs against untrusted submodule content during recursive clones is not unconditionally suppressed for every clone Desktop performs.

### Proof of Concept
1. Attacker publishes/hosts a Git repository with a submodule crafted so that clone-time symlink checks (the ones `GIT_CLONE_PROTECTION_ACTIVE` gates) would normally reject or sanitize it.
2. Victim uses GitHub Desktop's "Clone a repository" (or checks out a PR/fork whose head Desktop clones) pointing at the attacker's repository/URL.
3. Desktop calls `clone()` in `app/src/lib/git/clone.ts`, which runs `git ... clone --recursive -- <url> <path>` with `GIT_CLONE_PROTECTION_ACTIVE` forced to `'false'` in the environment.
4. Because the protection signal is force-disabled for the whole operation, Git's own recursive-submodule hardening does not have the chance to engage as it would for a bare `git clone --recursive` run without this override, allowing the crafted submodule content to place files/symlinks outside the intended clone directory. [5](#0-4)

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

**File:** app/src/lib/git/clone.ts (L68-93)
```typescript
export async function clone(
  url: string,
  path: string,
  options: CloneOptions,
  progressCallback?: (progress: ICloneProgress) => void
): Promise<void> {
  if (isClonePathSensitive(path)) {
    throw new Error(
      `The clone destination "${path}" targets a sensitive system location. ` +
        'Cloning into this directory is not allowed.'
    )
  }

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
