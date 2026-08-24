### Title
Clone/submodule recursion explicitly disables Git's built-in clone-protection check, re-enabling a known RCE class - ([File: app/src/lib/git/clone.ts])

### Summary
The report's underlying invariant break is "a safety check that should gate an operation is skipped/deferred, letting attacker-influenced data pass through unchecked." In `clone()`, GitHub Desktop explicitly sets `GIT_CLONE_PROTECTION_ACTIVE: 'false'` while also passing `--recursive`, deliberately disabling Git's own built-in clone-time protection mechanism for every clone (including recursive submodule clones) that Desktop performs. [1](#0-0) 

### Finding Description
Modern Git ships a runtime guard (surfaced through `GIT_CLONE_PROTECTION_ACTIVE`) that is active by default during `git clone`, intended to catch unsafe repository layouts before they are materialized on disk — this is the mechanism Git added after the class of clone/submodule vulnerabilities where a malicious repository (or a malicious nested submodule) can place files (e.g. inside a symlinked or case-confusable `.git` directory) that get written into a location Git itself will later interpret as configuration or hooks, leading to code execution as soon as the victim clones the repository.

Desktop's `clone()` function unconditionally sets this variable to `'false'` for every single clone operation, and it does so while also passing `--recursive`, meaning nested, attacker-controlled submodules are cloned in the same protection-disabled context: [1](#0-0) 

The attacker primitive is direct: the URL passed to `clone()` (and by extension every submodule URL reachable from `--recursive`) is fully attacker-controlled content — a user only has to enter or click a link to a malicious/compromised repository URL in Desktop's Clone or "Open in Desktop" flow. Desktop's own guard, `isClonePathSensitive`, only validates the destination *path* is not a sensitive system directory; it does nothing to validate the *content* being cloned, and does not compensate for turning off Git's own content-related clone protection: [2](#0-1) 

No other check in this code path re-enables or substitutes for the disabled protection — `envForRemoteOperation` only manages proxy/auth environment, not repository-content safety: [3](#0-2) 

### Impact Explanation
If `GIT_CLONE_PROTECTION_ACTIVE=false` disables the specific Git-side defense against malicious repository layouts (the class of issue that historically enabled RCE via crafted `.git`-adjacent paths in submodules on case-insensitive or symlink-vulnerable filesystems), then every clone performed by Desktop — triggered by nothing more than a user pasting/clicking a URL — runs with that defense turned off. This can lead to arbitrary file writes outside the intended repository tree and, depending on the underlying OS/filesystem, execution of attacker-supplied hook/config content immediately upon clone, satisfying the "attacker controls a cloned/fetched repository ... resulting in code execution / file write outside the repo" impact criterion.

### Likelihood Explanation
Likelihood is high for the trigger (any clone of a malicious/compromised repo or "Open in Desktop" deep link reaches this exact code path unconditionally — there is no opt-out or user prompt), but the ultimate exploitability depends on which specific Git-side condition the `GIT_CLONE_PROTECTION_ACTIVE` flag guards and the platform/filesystem it defends against; I could not confirm from the indexed code the exact upstream Git protection this flag maps to or the Git/dugite version bundled, so the precise exploitable condition is unverified from local evidence alone.

### Recommendation
Do not unconditionally disable `GIT_CLONE_PROTECTION_ACTIVE`. Confirm exactly which Git safety check this variable controls in the bundled dugite/Git version, and either leave it enabled (default) or explicitly gate the override behind a justified, narrowly-scoped exception with compensating validation (e.g., verifying no submodule/config path escapes the working tree, rejecting symlinked or case-confusable `.git` paths) before disabling it. At minimum, restrict any override to be as narrow as the specific edge case it was added to work around, rather than applying it to every clone including recursive submodule clones of arbitrary attacker-supplied URLs.

### Proof of Concept
1. Attacker publishes a repository containing a submodule (or top-level layout) crafted to exploit the specific condition that `GIT_CLONE_PROTECTION_ACTIVE` is designed to catch (e.g., a case-confusable or symlinked path colliding with `.git` on the victim's filesystem, per the known clone/submodule RCE class).
2. Victim clicks "Clone" in Desktop for that URL, or uses an "Open in Desktop" `x-github-client` deep link pointing at the malicious/compromised repo.
3. Desktop calls `clone(url, path, options)`, which runs `git … clone --recursive -- url path` with `GIT_CLONE_PROTECTION_ACTIVE=false` set in the environment: [1](#0-0) 
4. Because the protection check is disabled and `--recursive` fetches nested attacker-controlled submodules in the same pass, the malicious repository layout is materialized on disk without the guard Git would otherwise apply, potentially writing files outside the intended repo directory or triggering hook execution — with no additional Desktop-side content validation (`isClonePathSensitive` only checks the destination path, not cloned content).

**Uncertainty note:** I was unable to confirm from the indexed codebase (a single "Initial commit" snapshot with no prior history, and no local Git/dugite source available) the exact upstream Git mechanism `GIT_CLONE_PROTECTION_ACTIVE` maps to, or why Desktop explicitly disables it. This is a strong code-level red flag (a security-sounding environment variable being force-disabled for every clone) but the concrete end-to-end exploit chain could not be fully verified without access to the bundled Git/dugite version's source and changelog. I recommend a Devin session with full repository and dependency access to pin down the exact Git behavior this flag controls before treating severity as fully confirmed.

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
