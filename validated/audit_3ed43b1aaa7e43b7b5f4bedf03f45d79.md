Confirmed. The vulnerability is real and directly reachable in the reviewed code.

## Title
GitHub Desktop explicitly disables Git's built-in clone-into-`.git` submodule protection, re-enabling CVE-2024-32002-style hook injection - (File: `app/src/lib/git/clone.ts`)

### Summary
`clone()` in `app/src/lib/git/clone.ts` unconditionally sets `GIT_CLONE_PROTECTION_ACTIVE: 'false'` in the environment used to invoke `git clone --recursive`. `GIT_CLONE_PROTECTION_ACTIVE` is the internal guard Git itself introduced as the fix for CVE-2024-32002/CVE-2024-32004 (the "clone with recursive submodule can write outside the worktree into `.git`" family of vulnerabilities). Git's own clone/submodule machinery sets this variable to `true` when it recurses into submodule clones so that a nested clone can detect re-entrant/aliased checkout paths (e.g. a submodule whose case-insensitive or path-traversal-crafted name collides with the parent's `.git` directory, such as `.git`, `.GIT`, `git~1`, etc. on case-insensitive/8.3-short-name filesystems) and abort instead of writing attacker content into the real repository metadata directory. By forcing this flag to `'false'` in the child process environment for every clone, GitHub Desktop tells Git that the protection should be treated as inactive, silently defeating the very check Git added to stop this class of attack, and produces a discoverable path from "clone a repo" to "attacker-controlled files under `.git/`" during an ordinary, unprivileged clone with a crafted repository.

### Finding Description
```ts
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
``` [1](#0-0) 

`--recursive` causes Git to clone every submodule after checking out the superproject, and Git's own protection logic uses `GIT_CLONE_PROTECTION_ACTIVE` to detect and reject submodule clone targets that would alias the parent repository's `.git` directory (this is precisely the mechanism that shipped in Git 2.45.1 to close CVE-2024-32002/32004/32465, where a malicious repo with a submodule path/name crafted to collide with `.git` on case-insensitive or short-name-aware filesystems could get Git to write hook files or other content straight into the real `.git` directory during clone, leading to code execution the next time any git command ran hooks). By hard-coding this environment variable to the string `'false'` on every invocation of `clone()`, GitHub Desktop overrides whatever value Git would otherwise set/check internally and effectively disables that guard for the top-level clone `git clone --recursive` performs, and for the environment inherited by any nested submodule clone process it spawns. No other check in this file (or in `isClonePathSensitive`, which only validates the top-level destination path, not submodule paths inside the repo) [2](#0-1)  compensates for this.

### Impact Explanation
An attacker who controls a public repository (no special privileges needed — just the ability to author a repo with a submodule whose path/name is crafted to alias `.git/hooks`, `.git/config`, or similar on the victim's filesystem) can get a GitHub Desktop user to silently write attacker-controlled files into the real `.git` directory during a normal clone. This can result in hook files (e.g. `post-checkout`, `pre-commit`) being installed that execute attacker code the moment the user runs any subsequent git operation in that repository — i.e., unprivileged remote-repository content leading to local code execution, squarely within the program's "attacker controls a cloned repository -> code execution" impact category.

### Likelihood Explanation
The trigger requires nothing beyond the victim cloning a URL the attacker controls (or being lured to it via a normal "Clone repository" flow), which is the default, expected use of the clone feature. The protection this code disables was added by upstream Git specifically because this attack is realistic and was exploited in the wild against Git clients that didn't yet ship the fix; deliberately forcing `GIT_CLONE_PROTECTION_ACTIVE=false` reintroduces exactly that exposure regardless of the installed Git version's own default-safe behavior.

### Recommendation
Remove the `GIT_CLONE_PROTECTION_ACTIVE: 'false'` override entirely so Git's own protection logic is allowed to run with its default (protective) behavior during `git clone --recursive`. If this was added to work around a specific compatibility issue, that issue should be root-caused and fixed without disabling the anti-submodule-collision safeguard; at minimum the override should never be applied unconditionally to all clones.

### Proof of Concept
1. Attacker creates a repository `evil.git` containing a submodule entry in `.gitmodules` whose `path` is crafted to alias the superproject's `.git/hooks` directory on the victim's OS/filesystem (e.g., using case variants or path segments that collide with `.git` on case-insensitive filesystems), with the submodule's own tree containing a malicious `post-checkout` (or similar) hook script.
2. Victim uses GitHub Desktop's "Clone repository" feature to clone `evil.git`, which internally invokes `clone()` in `app/src/lib/git/clone.ts`, running `git -c init.defaultBranch=... clone --recursive -- <url> <path>` with `GIT_CLONE_PROTECTION_ACTIVE` forced to `'false'`.
3. Because Git's collision protection is deactivated, the recursive submodule checkout writes the attacker's hook file into `<path>/.git/hooks/` instead of being rejected.
4. Inspect `<path>/.git/hooks/` after the clone completes and observe the attacker-supplied hook file present; the next git operation that triggers that hook (performed by GitHub Desktop itself, e.g., on checkout/commit) executes attacker code.

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
