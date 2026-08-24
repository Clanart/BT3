This is exactly the kind of finding fitting the pattern: an existing security control exists in the codebase, but a nearby line deliberately disables it — mirroring the original report's "control exists but is never engaged" theme, except here it's actively defeated rather than merely absent.

### Title
Clone operation explicitly disables Git's clone-time hook/symlink protection (`GIT_CLONE_PROTECTION_ACTIVE=false`) - (File: `app/src/lib/git/clone.ts`)

### Summary
`app/src/lib/git/clone.ts` builds the environment for every `git clone` invocation and unconditionally sets `GIT_CLONE_PROTECTION_ACTIVE: 'false'` [1](#0-0)  for all clones, including recursive clones that also clone submodules (`args.push('clone', '--recursive')`) [2](#0-1) . This is Git's own runtime safety check (added upstream to address CVE-2024-32004-class attacks, where a malicious repository/submodule tree can plant symlinks that get followed into `.git/hooks` or other sensitive metadata paths and later trigger arbitrary command execution when Git or a subsequent operation runs a hook). By actively forcing this protection off for every clone Desktop performs, the application removes an upstream-provided defense against attacker-controlled repository content, similar in spirit to the report's "guard exists but is never armed" pattern — except here Desktop goes further and explicitly disarms it.

### Finding Description
The only guard visible in `clone.ts` against a malicious clone target is `isClonePathSensitive`, which validates the destination directory on the local filesystem (blocking things like `~/.ssh`) [3](#0-2) . That check says nothing about the content of the remote/attacker-controlled repository being cloned. Git itself is the second line of defense against malicious repository content (e.g., crafted submodules with hook-shaped symlinks), and that defense is implemented as an internal safety gate that is enabled by default in modern Git releases. Desktop's `clone` function overrides this by injecting `GIT_CLONE_PROTECTION_ACTIVE: 'false'` into the environment for every clone, with no accompanying comment explaining why it needs to be disabled [1](#0-0) . There is no compensating check afterward that scans the freshly cloned tree (or its submodules) for suspicious symlinks pointing into `.git/hooks` or similar sensitive locations before Desktop performs any subsequent git operation (status, fetch, etc.) that could invoke a hook.

### Impact Explanation
If Git's clone-time protection is the thing standing between "attacker publishes a repository with a rigged submodule/symlink layout" and "arbitrary code execution when the victim runs `git` inside the resulting working directory," then disabling it for every single clone performed by Desktop reintroduces that code-execution class for any user who clones or forks an attacker-authored repository through the GUI. This satisfies the "attacker controls a cloned/fetched repository ... result is code execution" criterion directly, since the trigger is simply using Desktop's normal Clone Repository / Clone Again flow against a hostile URL.

### Likelihood Explanation
Likelihood is high for any user who clones a public/untrusted repository through Desktop, since the environment variable is set unconditionally in the shared `clone()` function used by both the "Clone repository" dialog flow (`CloningRepositoriesStore.clone` → `clone()`) [4](#0-3)  and the "Clone Again" flow, and `--recursive` is always passed so nested submodules are cloned automatically without further prompting [2](#0-1) . No opt-in or trust decision is required from the user, unlike the separate "unsafe repository" ownership check that does require explicit user action (`addSafeDirectory`) [5](#0-4) .

### Recommendation
Remove the `GIT_CLONE_PROTECTION_ACTIVE: 'false'` override so Git's built-in clone protection remains active, unless there is a specific, narrowly-scoped, documented reason it must be disabled (e.g., a compatibility workaround for a specific Git version) — in which case that reason should be recorded in a comment and the override should be conditioned on that exact scenario rather than applied to every clone. If Desktop must support environments where the protection causes false positives, add a post-clone integrity check that inspects the resulting `.git` directory and submodules for symlinks escaping expected locations before allowing any further git operations against the repository.

### Proof of Concept
1. An attacker publishes a public Git repository containing a submodule structured to place a symlink such that, once checked out under `--recursive`, a path resolves into `.git/hooks/`.
2. A GitHub Desktop user clones the repository (or a fork of it) via the normal "Clone repository" dialog, which calls `CloningRepositoriesStore.clone` → `clone()` [4](#0-3) .
3. Because `clone()` sets `GIT_CLONE_PROTECTION_ACTIVE=false` in the child process environment [1](#0-0) , Git's built-in refusal/abort behavior for this class of malicious layout is bypassed, and the symlink is planted successfully.
4. Any later git invocation by Desktop that triggers hook execution in that working directory (e.g., commit, checkout, or a background status refresh that runs a configured hook) executes attacker-supplied code with the user's privileges, outside the boundary the user believed they were operating within.

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

**File:** app/src/lib/git/clone.ts (L88-93)
```typescript
  const args = [
    '-c',
    `init.defaultBranch=${defaultBranch}`,
    'clone',
    '--recursive',
  ]
```

**File:** app/src/lib/stores/cloning-repositories-store.ts (L20-38)
```typescript
  public async clone(
    url: string,
    path: string,
    options: CloneOptions
  ): Promise<boolean> {
    const repository = new CloningRepository(path, url)
    this._repositories.push(repository)

    const title = `Cloning into ${path}`

    this.stateByID.set(repository.id, { kind: 'clone', title, value: 0 })
    this.emitUpdate()

    let success = true
    try {
      await cloneRepo(url, path, options, progress => {
        this.stateByID.set(repository.id, progress)
        this.emitUpdate()
      })
```

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
