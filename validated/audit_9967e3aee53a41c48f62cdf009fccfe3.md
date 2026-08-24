### Title
Git's clone symlink/embedded-repo protection is force-disabled via `GIT_CLONE_PROTECTION_ACTIVE=false` on every clone - (File: `app/src/lib/git/clone.ts`)

### Summary
`clone()` in `app/src/lib/git/clone.ts` unconditionally sets the environment variable `GIT_CLONE_PROTECTION_ACTIVE` to `'false'` for every clone operation, including recursive clones of arbitrary, attacker-supplied URLs. This is Git's own built-in defense (added upstream to mitigate embedded/symlinked `.git` directory attacks during `--recursive` clones) and Desktop deliberately disables it for all clones, regardless of the source's trustworthiness. This mirrors the reported bug class: a security-critical toggle is validated/reasoned about in surrounding code (`isClonePathSensitive`), yet the actual enforcement flag is hardcoded to the unsafe value, silently nullifying the protection Git itself provides.

### Finding Description
`app/src/lib/git/clone.ts` builds the clone environment like this: [1](#0-0) 

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

`GIT_CLONE_PROTECTION_ACTIVE` is the environment switch Git added to guard against clones (especially `--recursive` ones that also initialize submodules) where a malicious repository contains an embedded/symlinked `.git` directory, or a submodule whose worktree/gitdir is crafted so that checkout writes files outside the intended repository boundary (or into the local `.git`, enabling hook execution on subsequent operations). By pinning this variable to `'false'`, Desktop disables the very check that exists to stop exactly this class of attack, for every single clone — there is no branch, capability check, or user consent gate around this value; it is a constant string in the options object.

Meanwhile, the file also contains `isClonePathSensitive()`, a hand-rolled guard that only checks whether the *destination directory* resolves to a small, fixed list of sensitive folders (home, `.ssh`, `.gnupg`, `.config`, etc.): [2](#0-1) 

This bespoke check does not, and cannot, cover the threat model that `GIT_CLONE_PROTECTION_ACTIVE` addresses (symlink/embedded-repo tricks reachable via `--recursive` submodule initialization within the cloned tree itself), so its presence creates a false impression that clone-path safety is comprehensively handled while the native Git guard is turned off unconditionally.

The `url` and `path` arguments to `clone()` originate directly from user/GitHub-API-supplied data (repository clone URLs, `x-github-client` deep links, `openRepositoryFromUrl`/clone dialogs), so the attacker fully controls the repository content being cloned recursively — satisfying the "attacker controls a cloned/fetched repository" criterion.

### Impact Explanation
With `--recursive` clone and Git's clone protection disabled, a malicious repository (or one of its submodules) can be crafted to exploit the class of issues `GIT_CLONE_PROTECTION_ACTIVE` was introduced to stop — e.g. writing checked-out content to unexpected filesystem locations relative to the intended clone destination via crafted submodule/gitlink structures. This can result in file writes outside the expected repository directory and potential follow-on code execution via subsequently-triggered hooks, which matches the "file write outside repo" and "code execution" impact classes called out as valid for this analysis.

### Likelihood Explanation
Every clone performed by GitHub Desktop uses this code path (`clone()` is the single implementation backing the Clone Repository flow, deep-link "open repository" cloning, and API-driven clone actions), and `--recursive` is always passed, meaning the disabled protection is active on 100% of clones without any opt-in or condition. The only thing standing between a user and this path is entering (or clicking a link to) a malicious repository URL, which requires no special local access, admin rights, or prior compromise — it is a normal, expected user action (cloning a repo).

### Recommendation
Do not force `GIT_CLONE_PROTECTION_ACTIVE` to `'false'`. Leave Git's native clone protection enabled (its default) for recursive clones of untrusted/remote sources, and only disable it, if ever necessary, behind an explicit, narrowly-scoped, and documented condition — not as an unconditional constant. If the override exists to work around a specific compatibility issue, that justification and its safety implications should be documented and the flag should be set based on a genuine trust decision, not hardcoded.

### Proof of Concept
1. Host a public repository containing a submodule (or embedded `.git` structure) crafted to exploit the embedded-repository/symlink clone weakness that `GIT_CLONE_PROTECTION_ACTIVE` mitigates (per Git's upstream advisory for this protection).
2. In GitHub Desktop, use "Clone repository" (or a `x-github-client://openRepo` deep link) to clone the malicious URL to a normal destination path (so `isClonePathSensitive()` passes).
3. Observe that `clone()` executes `git ... clone --recursive -- <url> <path>` with `GIT_CLONE_PROTECTION_ACTIVE=false` in the environment as shown at [3](#0-2)  — Git's built-in defense against the crafted repository/submodule layout is inactive, allowing the malicious checkout behavior that the flag exists to prevent.

Note: I could not execute Git itself in this environment to confirm the exact on-disk write primitive achievable with a current Git version's protection semantics; this assessment is based on static code review of `app/src/lib/git/clone.ts` and the documented purpose of `GIT_CLONE_PROTECTION_ACTIVE`. A background Devin session with a sandboxed Git installation would be needed to build and verify a concrete exploit payload.

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

**File:** app/src/lib/git/clone.ts (L74-93)
```typescript
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
