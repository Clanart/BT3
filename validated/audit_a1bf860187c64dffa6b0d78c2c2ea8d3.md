### Title
Recursive clone unconditionally disables Git's clone-time protection against malicious repository content - (File: app/src/lib/git/clone.ts)

### Summary
`clone()` in [1](#0-0)  runs every user-initiated clone with `--recursive` while explicitly setting `GIT_CLONE_PROTECTION_ACTIVE: 'false'` in the child process environment. This unconditionally disables a Git-native safety check that exists specifically to stop malicious repository content (crafted via nested/embedded `.git` directories, symlinked submodule paths, or a `.gitmodules` file) from being able to execute code or write files outside the intended repository during a recursive clone/submodule checkout. The attacker-controlled object here is the remote repository content itself — exactly the class of input the "Valid Impact" section calls out (a cloned/fetched repository the attacker controls).

### Finding Description
The `clone` function builds the git invocation like this: [2](#0-1) 

Every call unconditionally merges in `GIT_CLONE_PROTECTION_ACTIVE: 'false'` regardless of the source of `url` — it applies the same to a URL typed by the user, a URL parsed from an untrusted deep link (`x-github-client://openRepo/...`, see [3](#0-2) ), or a "Clone Again" action driven by data stored on a `GitHubRepository` model ( [4](#0-3) ). The clone is also always run with `'--recursive'` (line 92), meaning any submodules declared in the attacker's `.gitmodules` are fetched and checked out automatically, with the protection flag switched off for the whole operation, including the submodule stage.

This mirrors the report's broken invariant exactly: the report's bug is that the contract trusts an externally-supplied object (`YIELD_TOKEN`) to behave safely and lets it corrupt internal accounting/state; here, Desktop trusts externally-supplied repository content (from a URL/deep-link the attacker controls) while deliberately turning off the one guard designed to keep that content from breaking out of the intended sandboxed clone operation.

The `isClonePathSensitive` backstop added at [5](#0-4)  only checks the *destination directory* is not `~/.ssh`, `~/.gnupg`, etc. It does nothing to prevent the recursive clone/submodule-checkout process itself — which now runs with `GIT_CLONE_PROTECTION_ACTIVE` disabled — from acting on malicious repository content (e.g. clashing/embedded `.git` paths or crafted submodule structures) during the checkout it performs inside the (safe) destination path.

### Impact Explanation
If the disabled protection is the one Git relies on to prevent a malicious repository (potentially reached via `--recursive` submodule processing) from writing/overwriting files through crafted paths or triggering hook execution during checkout, disabling it for every clone means any attacker who can get a victim to clone or "Open in Desktop" their repository (a normal, unprivileged GitHub action — no admin rights, no prior host compromise) can attempt to leverage that class of attack. This satisfies the required impact bar: code execution or file writes originating from attacker-controlled repository content that the victim merely clones through the normal Desktop UI or deep link flow.

### Likelihood Explanation
Likelihood is high in terms of reachability: `clone()` is the single code path used for all clone operations in Desktop (manual clone, "Clone Again" from `missing-repository.tsx`, and `open-repository-from-url`/CLI clone actions), and the disabling flag is unconditional — there is no feature flag or opt-in required. The only variable is whether the underlying Git version being used still contains the specific exploitable behavior that `GIT_CLONE_PROTECTION_ACTIVE` was meant to guard against; I could not fully confirm the exact semantics/CVE tied to this exact environment variable name from the local codebase alone, since it isn't documented in-repo beyond its use here.

### Recommendation
- Remove the unconditional `GIT_CLONE_PROTECTION_ACTIVE: 'false'` override in `clone()`, or scope it only to cases where it is provably required (and never for clones of arbitrary/remote URLs).
- If some legitimate use case forced this flag to be turned off, gate it behind an explicit, narrowly-scoped condition and document the underlying Git safety mechanism being bypassed and why it's safe to do so in that specific case.
- Add regression tests that assert clones of repositories with crafted/embedded `.git` paths or malicious `.gitmodules` submodule structures fail safely with the protection enabled.

### Proof of Concept
1. Attacker publishes a public GitHub repository containing a `.gitmodules` file and directory layout crafted to exploit the specific condition `GIT_CLONE_PROTECTION_ACTIVE` is designed to block (e.g., an embedded/symlinked `.git` reference reached through recursive submodule checkout).
2. Attacker sends the victim a link to the repo, or a `x-github-client://openRepo/<attacker-url>` deep link (parsed by [3](#0-2) ), or simply gets the victim to click "Clone" in Desktop's UI.
3. Desktop invokes `clone(url, path, options)` ( [1](#0-0) ), running `git -c init.defaultBranch=... clone --recursive -- <url> <path>` with `GIT_CLONE_PROTECTION_ACTIVE=false` and `GIT_CLONE_PROTECTION_ACTIVE`-guarded protections disabled for the whole operation, including recursive submodule checkout.
4. Whatever payload the disabled protection would normally have blocked is allowed to execute/write during the clone, inside a destination directory the victim believed was safe because `isClonePathSensitive` only validates the top-level destination, not the recursive submodule content processed within it.

Note: I was not able to fully verify from the local index alone the precise Git-internal exploit class that `GIT_CLONE_PROTECTION_ACTIVE` guards against (the variable's semantics are not documented in this repository). I recommend a Devin session with full repository/terminal access to check the installed Git version's changelog/source for `GIT_CLONE_PROTECTION_ACTIVE`, confirm what it disables, and validate a working end-to-end PoC.

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

**File:** app/src/lib/parse-app-url.ts (L98-125)
```typescript
  if (actionName === 'openrepo') {
    const pr = getQueryStringValue(query, 'pr')
    const branch = getQueryStringValue(query, 'branch')
    const filepath = getQueryStringValue(query, 'filepath')

    if (pr != null) {
      if (!/^\d+$/.test(pr)) {
        return unknown
      }

      // we also expect the branch for a forked PR to be a given ref format
      if (branch != null && !/^pr\/\d+$/.test(branch)) {
        return unknown
      }
    }

    if (branch != null && testForInvalidChars(branch)) {
      return unknown
    }

    return {
      name: 'open-repository-from-url',
      url: parsedPath,
      branch,
      pr,
      filepath,
    }
  }
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
