## Finding

The DAO exploit's broken invariant is "an untrusted, attacker-supplied value is fed into a privileged execution path whose built-in safety check was bypassed/never enforced" (the `propose`/`execute` calldata was accepted without validating it against a safe action set). The closest concrete Desktop analog is that GitHub Desktop explicitly disables Git's own anti-RCE protection for every clone it performs.

### Title
Recursive clone disables Git's `GIT_CLONE_PROTECTION_ACTIVE` safeguard, re-enabling submodule hook-planting RCE (CVE-2024-32002 class) - (File: app/src/lib/git/clone.ts)

### Summary
Every clone performed by GitHub Desktop sets `GIT_CLONE_PROTECTION_ACTIVE: 'false'` while also passing `--recursive`, deliberately turning off the protection Git added (Git ≥2.45.1) against malicious submodules planting executable hooks during clone/checkout on case-insensitive or symlink-tolerant filesystems. [1](#0-0) 

### Finding Description
`clone()` builds the environment for the `git clone` invocation and unconditionally sets `GIT_CLONE_PROTECTION_ACTIVE: 'false'`, then runs `git ... clone --recursive -- <url> <path>`. [1](#0-0) [2](#0-1) 

`GIT_CLONE_PROTECTION_ACTIVE` is the environment variable Git introduced specifically to gate the fix for CVE-2024-32002: a crafted repository containing submodules whose paths collide with `.git` (via case folding on case-insensitive filesystems, or via clever path components) can cause a recursive clone to write files into the top-level `.git/hooks` directory of the superproject. When the clone then runs a post-checkout style hook as part of finishing the checkout, arbitrary attacker-controlled code executes on the victim's machine — this is exactly the "write outside the repo + code execution" impact category. Git's fix makes clone refuse (or the calling tool must opt back in) unless `GIT_CLONE_PROTECTION_ACTIVE=false` is explicitly set, which is meant to be a rare, deliberate escape hatch for tools that have already re-implemented equivalent protections themselves.

Desktop sets this variable to `'false'` unconditionally on every clone call, with no evidence elsewhere in `clone.ts` of an equivalent path-collision/case-insensitivity check being performed before or after the clone. The existing safeguards in the same file only check the *clone destination* directory (`isClonePathSensitive`) against a fixed list of sensitive paths like `~/.ssh`, `~/.gnupg`, `~/.gitconfig`: [3](#0-2) 

That check protects against a different problem (cloning *into* a sensitive directory) and does nothing to prevent a malicious submodule from writing into `.git/hooks` of the newly-created repository itself. There is no re-implementation of Git's path-collision detection anywhere near this call, so disabling `GIT_CLONE_PROTECTION_ACTIVE` removes the only safety net without a replacement.

This is the same primitive as the DAO exploit's "unrelated, unvalidated calldata treated as a trusted primitive": in both cases a downstream execution engine's built-in guard against attacker-controlled input is deliberately turned off, and no compensating validation is substituted.

### Impact Explanation
Any repository an unprivileged user clones through GitHub Desktop's normal "Clone repository" or "Clone Again" flow (missing-repository re-clone path also passes through `clone()`) [4](#0-3) 
can be crafted by an attacker to contain a colliding submodule path. On case-insensitive filesystems (default macOS APFS, default Windows NTFS) this allows the attacker's repository content to be written into `.git/hooks` of the cloned checkout, and the corresponding hook is invoked automatically as part of the same recursive clone/checkout sequence — resulting in code execution on the victim's machine, using GitHub Desktop's own process. This satisfies the "attacker controls a cloned/fetched repository … result is code execution … outside the repo" impact category exactly, without any local/physical access, admin rights, prior malware, or social engineering beyond "clone this repo," which is intrinsic, expected usage of Desktop.

### Likelihood Explanation
Likelihood is high for any user on a case-insensitive filesystem (the majority of macOS and Windows installs) who clones an attacker-supplied or attacker-contributed repository (public fork, malicious upstream, supply-chain style repo) — no unusual interaction is required beyond the ordinary "Clone" action Desktop exists to perform. The variable is set unconditionally for all clones, not opt-in, so there's no way for a user to be on the "safe" path unless they're on a case-sensitive filesystem.

### Recommendation
Remove the unconditional `GIT_CLONE_PROTECTION_ACTIVE: 'false'` override in `clone()` and let Git's built-in protection remain active. If Desktop genuinely needs to disable it (e.g., for compatibility with an older/newer dugite-bundled Git), it must first implement an equivalent pre-clone/pre-checkout validation of submodule paths for case-insensitive collisions with `.git`, and refuse or sanitize such clones before allowing the recursive checkout to proceed.

### Proof of Concept
1. Attacker publishes a public repository containing a `.gitmodules` entry and a nested tree structured to reproduce the CVE-2024-32002 path-collision technique (a submodule gitlink whose case-folded path resolves to `.git/hooks/<hookname>` on a case-insensitive filesystem), with the submodule content being an executable hook script.
2. Victim, on macOS (APFS, case-insensitive by default) or Windows, uses GitHub Desktop's "Clone a repository from the Internet" to clone the attacker's repository URL.
3. `clone()` invokes `git clone --recursive` with `GIT_CLONE_PROTECTION_ACTIVE=false` set in the environment [1](#0-0) 
 — disabling Git's own refusal to perform the collision-prone submodule checkout.
4. During the recursive submodule checkout, the attacker's hook script is written into `<path>/.git/hooks/`.
5. The subsequently triggered hook (e.g., `post-checkout`, run automatically by Git as part of the same clone operation) executes with the privileges of the user running GitHub Desktop, achieving code execution outside the boundaries the user intended to trust (arbitrary file write/execute outside the visible repository working tree).

### Citations

**File:** app/src/lib/git/clone.ts (L10-47)
```typescript
/**
 * Check whether a resolved clone path targets a sensitive location that
 * should never be used as a clone destination. This is a backstop against
 * path traversal attacks where a crafted URL tricks the UI into deriving
 * a clone path outside the intended base directory.
 */
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

**File:** app/src/lib/git/clone.ts (L119-125)
```typescript
  if (options.branch) {
    args.push('-b', options.branch)
  }

  args.push('--', url, path)

  await git(args, __dirname, 'clone', opts)
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
