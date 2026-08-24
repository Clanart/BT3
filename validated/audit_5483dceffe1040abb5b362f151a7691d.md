### Title
Recursive `git clone` explicitly disables Git's built-in clone-protection safeguard, re-opening the submodule/hook RCE class - ([File: app/src/lib/git/clone.ts])

### Summary
GitHub Desktop's `clone()` helper builds every clone command with `--recursive` and explicitly sets `GIT_CLONE_PROTECTION_ACTIVE: 'false'` in the child-process environment. This environment variable is the switch Git added to guard against the class of vulnerabilities exemplified by CVE-2024-32002/32004 (clone-time hook/config execution via malicious recursive submodules, especially on case-insensitive or symlink-tolerant filesystems). By forcing this protection off for every clone, Desktop removes Git's own defense-in-depth check on a code path where the "repository" is entirely attacker-controlled content (any URL/organization the user clones), which is exactly the "attacker controls a cloned repository" primitive called out as in-scope.

### Finding Description
`clone()` constructs the environment for the `git clone --recursive ... -- url path` invocation as: [1](#0-0) 

```
const env = {
  ...(await envForRemoteOperation(url)),
  GIT_CLONE_PROTECTION_ACTIVE: 'false',
}
...
const args = [
  '-c',
  `init.defaultBranch=${defaultBranch}`,
  'clone',
  '--recursive',
]
```

`--recursive` causes Git to immediately checkout and initialize every submodule referenced by the cloned repository, following URLs and paths that are entirely defined by the (potentially malicious/attacker-controlled) remote repository content — this is precisely the mechanism abused by the clone-time submodule RCE bug class that upstream Git hardened against by introducing a "clone protection" check (rejecting clones where a submodule's checkout path collides with or aliases the repository's own `.git` directory, e.g., via case-folding or symlinks, which previously allowed a malicious repo to plant hooks or config that execute during the same clone). Setting `GIT_CLONE_PROTECTION_ACTIVE=false` disables that specific safeguard for every single Desktop clone, regardless of whether the target is trusted. There is no local guard in Desktop that substitutes for this check — the only defensive code nearby, `isClonePathSensitive`, only prevents cloning into a handful of sensitive host directories and does nothing to validate submodule paths/URLs inside the cloned content: [2](#0-1) 

Because the protection is unconditionally disabled and `--recursive` is unconditionally enabled, any attacker who can get a victim to clone their repository (a very common, unprivileged action — via a link, an org invite, a "clone in Desktop" deep link, or simply publishing a public repo) fully controls the submodule graph that Desktop will process without Git's own opt-out check available to stop it.

### Impact Explanation
If the underlying Git binary bundled with Desktop is affected by the class of clone-time hook/config execution bugs this protection guards against, disabling it converts an ordinary "clone this repository" action into a code-execution primitive with no additional user interaction beyond the clone the user already intended to perform. This satisfies the in-scope impact bar: "the attacker controls a cloned/fetched repository ... and the result is code execution." Even short of full RCE, disabling a hardening flag on the highest-risk operation (ingesting untrusted third-party content recursively) removes a safety net Desktop otherwise offers no equivalent for.

### Likelihood Explanation
Likelihood depends on the exact Git version bundled with Desktop and whether it is otherwise patched against the specific CVEs this flag was introduced for, which I could not verify from the indexed files (Desktop vendors its own Git binary, and I could not confirm the exact version in scope here). However, the mere presence of an explicit, unconditional opt-out of a security control on the single riskiest operation (cloning + recursive submodule expansion of untrusted repositories) is a real weakening of defense-in-depth regardless of the currently bundled Git version, since it removes protection against both currently-known and any future issues that this specific safeguard is designed to catch.

### Recommendation
- Remove the unconditional `GIT_CLONE_PROTECTION_ACTIVE: 'false'` override, or at minimum ensure the bundled Git version and configuration is verified safe without this override before disabling it.
- If protection had to be disabled for a legitimate functional reason (e.g., compatibility with some file systems), scope the override narrowly and document the specific issue it works around, and re-enable a check/verification for the specific unsafe submodule-path collision case in Desktop's own code as compensating control.
- Add regression tests that clone a crafted repository containing a submodule whose path collides with `.git` (case-insensitively and via symlink) and assert Desktop refuses/aborts rather than silently proceeding.

### Proof of Concept
Conceptual reproduction (exact PoC repository contents depend on the specific upstream Git CVE the protection defends against, which requires validating against the bundled Git version):
1. Attacker publishes a public GitHub repository containing a submodule entry whose configured path is crafted to collide with the repository's own `.git` directory under case-insensitive or symlink-tolerant filesystem semantics (the pattern used in the Git clone-protection CVEs).
2. Victim uses "Clone repository" in GitHub Desktop (or follows an `x-github-client://openRepo` deep link that triggers `openOrCloneRepository`) to clone the attacker's repository.
3. Desktop invokes `clone()` in `app/src/lib/git/clone.ts` with `--recursive` and `GIT_CLONE_PROTECTION_ACTIVE=false` set, per [1](#0-0) , bypassing the check Git would otherwise perform to refuse the clone.
4. If the bundled Git is vulnerable to the underlying submodule-collision issue, hooks/config planted via the crafted submodule execute during the clone, achieving code execution on the victim's machine without any further user action beyond the initial clone.

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
