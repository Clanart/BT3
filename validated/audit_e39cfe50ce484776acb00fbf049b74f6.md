## Title
`GIT_CLONE_PROTECTION_ACTIVE=false` disables Git's CVE-2024-32002-class recursive-clone RCE protection in Desktop's clone path - ([File: app/src/lib/git/clone.ts])

### Summary
The reported bug (`ERC721F.burn`) has one broken invariant: a privileged/destructive operation is executed without confirming that the operand is one the caller is actually entitled to act on ("only burn what you own"). The Desktop analog with the same shape is `clone()` in `app/src/lib/git/clone.ts`, which explicitly forces `GIT_CLONE_PROTECTION_ACTIVE: 'false'` for every `git clone --recursive` invocation, unconditionally disabling the exact Git-side safety check ("only write inside the intended checkout, refuse crafted repos that try to escape it via case-insensitive/symlink tricks") that upstream Git ships enabled by default.

### Finding Description
`clone()` builds its execution environment like this: [1](#0-0) 

`GIT_CLONE_PROTECTION_ACTIVE` is the environment variable Git introduced to gate the clone-time protections added in response to the class of vulnerabilities culminating in CVE-2024-32002 (recursive/submodule clone RCE via crafted repository content exploiting case-insensitive filesystems or symlinked `.git`/hook directories, allowing a hostile repository to write files, including executable hooks, outside the intended working tree during `--recursive` clone). Desktop passes `'false'` for this variable on every single clone, including the `--recursive` submodule-following clone path shown at: [2](#0-1) 

The clone function does have a separate, narrower guard — `isClonePathSensitive()` — which blocks the *top-level* destination directory from resolving to a small hardcoded deny-list (`~/.ssh`, `~/.gnupg`, `~/.config`, home root, etc.): [3](#0-2) 

That check only validates the caller-supplied destination path. It does nothing to stop a malicious remote repository (the attacker-controlled object here) from using submodule names/paths, case-folding collisions, or symlinked directory entries during the `--recursive` clone to write files *outside* the resolved clone directory once cloning is underway — which is precisely the class of attack `GIT_CLONE_PROTECTION_ACTIVE` (left at its default "on") is designed to stop. By force-disabling it, Desktop removes Git's own runtime defense and relies solely on the pre-flight destination-path check, which is not equivalent — it never inspects the actual repository content being cloned.

### Impact Explanation
If exploitable, a malicious/compromised remote (a repository the victim is enticed to clone or an existing repo whose upstream is later poisoned) can, during Desktop's `--recursive` clone, place files (potentially including executable hook files) at attacker-chosen paths outside the intended destination tree via the submodule/case-insensitive-filesystem primitive that Git's protection is meant to reject. That constitutes file write outside the repo and a path to code execution, matching the "attacker controls a cloned/fetched repository" + "file write outside the repo" / "code execution" impact class explicitly listed as valid.

### Likelihood Explanation
The trigger requires nothing beyond the normal user action of cloning a repository through Desktop's UI (or `x-github-client://openrepo/...` deep link / CLI clone flow), which is a fully expected, unprompted user interaction — not local access, admin rights, or social engineering. Any attacker who can get a victim to enter/click a clone URL for a repository they control satisfies the precondition. The only mitigating factor is that current upstream Git also gates the underlying vulnerable behavior behind filesystem characteristics (case-insensitive FS, or filesystems that support the specific symlink trick), so exploitability is platform-dependent (more relevant on Windows/macOS default filesystems than Linux ext4).

### Recommendation
Remove the forced `GIT_CLONE_PROTECTION_ACTIVE: 'false'` override (or set it to `'true'`/omit it so Git's compiled-in default applies) in `clone()`, and audit `updateSubmodulesAfterOperation()` in `app/src/lib/git/submodule.ts` (which runs `submodule update --init --recursive` against attacker-controlled submodule URLs) for the same override. If the override was added to work around a specific compatibility problem, it should be scoped/conditioned rather than applied unconditionally to every clone.

### Proof of Concept
Not independently reproducible from the index alone — full verification requires cloning a crafted upstream repository (with submodules/paths engineered for the case-insensitive-filesystem or symlinked-directory primitive addressed by CVE-2024-32002) against a Desktop build on an affected filesystem, and confirming files land outside the resolved destination path despite `isClonePathSensitive()` passing. This step needs the full local Desktop build/environment; the index-visible evidence is the explicit disabling call: [1](#0-0) 
Recommend a Devin session with terminal/filesystem access to build a PoC crafted repository and validate against the embedded Git version pinned by Desktop's `dugite` dependency.

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

**File:** app/src/lib/git/clone.ts (L81-84)
```typescript
  const env = {
    ...(await envForRemoteOperation(url)),
    GIT_CLONE_PROTECTION_ACTIVE: 'false',
  }
```

**File:** app/src/lib/git/clone.ts (L86-93)
```typescript
  const defaultBranch = options.defaultBranch ?? (await getDefaultBranch())

  const args = [
    '-c',
    `init.defaultBranch=${defaultBranch}`,
    'clone',
    '--recursive',
  ]
```
