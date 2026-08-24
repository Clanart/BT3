## Title
Clone protection is explicitly disabled during recursive clone, exposing users to malicious-repository RCE (CVE-2024-32002 class) - (File: `app/src/lib/git/clone.ts`)

### Summary
When GitHub Desktop clones a repository, it explicitly sets the environment variable `GIT_CLONE_PROTECTION_ACTIVE` to `'false'` for the `git clone --recursive` invocation. This variable controls a safety check that upstream Git added specifically to stop malicious repositories (including ones with malicious submodules) from writing files into the `.git` directory during clone — the exact vulnerability class fixed in Git 2.45.1 (CVE-2024-32002/32004/32020/32021). By force-disabling this protection on every clone, Desktop reintroduces a code-execution/arbitrary-file-write path that upstream Git deliberately closed, mirroring the report's pattern of an attacker-controlled object silently bypassing a safety flag that should gate a sensitive operation.

### Finding Description
The `clone()` function builds the git invocation like this: [1](#0-0) 

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
```

`GIT_CLONE_PROTECTION_ACTIVE` is the switch Git uses to enable/disable the hardening that blocks clones (especially recursive clones with submodules) from placing files where they can escape the working tree and land inside `.git` — e.g. via case-insensitive filesystem collisions or crafted submodule paths that alias `.git/hooks` or `.git/modules`, letting an attacker plant an executable hook that runs on the next git operation. Desktop forcibly sets this to `false`, i.e., it opts every clone out of Git's own built-in defense, and does so unconditionally for `--recursive` clones (which also fetch and check out attacker-controlled submodules).

The only clone-time guard Desktop adds is `isClonePathSensitive`, which just blocks a small denylist of destination directories (home dir, `.ssh`, `.gnupg`, `.config/git`, etc.): [2](#0-1) 

This guard checks the destination path chosen by the *user*, not the content of the *attacker-controlled* repository being cloned, so it does nothing to stop the class of attack `GIT_CLONE_PROTECTION_ACTIVE` was designed to mitigate: files inside the cloned repo (or its submodules) that are crafted to escape into `.git` during the clone/checkout itself.

### Impact Explanation
The attacker fully controls the repository content that Desktop clones (a git remote/repo, matching the allowed attacker model). If the repository (or one of its submodules, since `--recursive` is always used) is crafted to exploit the exact class of collision Git's clone protection blocks, the victim's clone can result in files being written outside the intended working directory and into `.git/hooks` or similar locations, leading to code execution on the next git operation Desktop performs in that repository (fetch, checkout, commit, etc.). This is a direct match for the requested impact categories: code execution / file write outside the repo caused by cloning attacker-controlled content.

### Likelihood Explanation
No special user action beyond a normal "Clone repository" (or "Clone Again" from `missing-repository.tsx`) is required — the victim only needs to clone a URL supplied or advertised by the attacker (e.g., a malicious GitHub repo or a fork). Since `--recursive` is always passed and `GIT_CLONE_PROTECTION_ACTIVE` is unconditionally forced to `false`, every clone through Desktop is exposed, with no configuration to opt back into the upstream protection.

### Recommendation
Do not override `GIT_CLONE_PROTECTION_ACTIVE`; let Git's default (protection enabled) apply, or set it to `'true'` explicitly. If disabling it was done to work around a specific compatibility issue, gate that behavior narrowly and document why, rather than disabling the protection globally for all clone operations. Consider also auditing submodule recursion for the same class of path-escape issues before checking out submodule content unattended.

### Proof of Concept
1. Attacker hosts (or forks) a git repository containing a crafted submodule/path structure designed to trigger the collision that `GIT_CLONE_PROTECTION_ACTIVE=true` normally blocks (the same payload shape used to demonstrate CVE-2024-32002 against `git clone --recursive`).
2. Victim uses GitHub Desktop's "Clone repository" (or "Clone Again") feature with that URL.
3. Desktop calls `clone()` in [3](#0-2)  which sets `GIT_CLONE_PROTECTION_ACTIVE: 'false'` and runs `git -c init.defaultBranch=... clone --recursive -- <url> <path>`.
4. Because Git's protection is disabled, the crafted repository content is allowed to write into `.git/hooks` (or an equivalent escape) during the clone/submodule checkout.
5. The planted hook executes on a subsequent git command Desktop issues against the repository (e.g. commit, fetch, or the recursive checkout of another submodule), achieving code execution on the victim's machine.

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
