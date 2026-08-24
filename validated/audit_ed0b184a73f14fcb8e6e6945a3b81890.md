## Analysis

Searching for a Desktop analog of the Lens bug (an explicit protection value being silently ignored/disabled on a specific code path) led to `app/src/lib/git/clone.ts`, where the `clone()` function that backs every "Clone repository", "Open in Desktop" deep-link, and `--cli-clone` invocation unconditionally sets an environment variable that turns off one of Git's own anti-exploitation guards for the clone operation: [1](#0-0) 

```
export async function clone(...) {
  ...
  const env = {
    ...(await envForRemoteOperation(url)),
    GIT_CLONE_PROTECTION_ACTIVE: 'false',
  }
  ...
}
```

### Title
Desktop unconditionally disables Git's clone-time exploit protection (`GIT_CLONE_PROTECTION_ACTIVE=false`) - (File: `app/src/lib/git/clone.ts`)

### Summary
Every clone Desktop performs — whether started from the Clone dialog, a `github-mac://openRepo/...` / `x-github-client://` deep link, or the `--cli-clone` CLI flag — is executed with the environment variable `GIT_CLONE_PROTECTION_ACTIVE` forced to `'false'`. This flag exists specifically to gate Git's built-in protection logic during a clone, and Desktop turns it off for the entire operation rather than letting it default to Git's own protected behavior. The attacker who controls the cloned repository content (any public repo, a hostile fork, or a repo pointed to by an "Open in Desktop" link the user clicks) fully controls the untrusted side of this trust boundary.

### Finding Description
The `clone()` function is Desktop's single choke point for the `git clone` invocation, used by:
- the Clone Repository dialog,
- `Dispatcher.clone` / `_startOpenInDesktop`,
- the app-URL handler for `open-repository-from-url`, which is reachable from `x-github-client://openRepo/<attacker-controlled-url>` deep links parsed with no allow-list on the target host [2](#0-1) ,
- `dispatchCLIAction` for `clone-url` actions [3](#0-2) .

In all of these paths, the resulting `url` is attacker-influenced (it is literally the remote the victim is being asked to clone), and it flows straight into `clone()`: [4](#0-3) 

The function only guards against one specific class of attack — the destination path being a sensitive local directory (`isClonePathSensitive`) [5](#0-4) . That check protects the *local* side (where the clone lands), but it does nothing to protect against a malicious *remote* repository exploiting the exact class of clone-time vulnerability that `GIT_CLONE_PROTECTION_ACTIVE` exists to stop. By setting the variable to `'false'` for every invocation instead of leaving Git's default (protection enabled) in place, Desktop actively opts every clone out of that safeguard, on the assumption that its own path check is a sufficient substitute — which it is not, since it addresses a different threat model (destination collisions, not attacker-crafted repository content).

This mirrors the structure of the Lens finding precisely: an explicit protection flag (`isBlocked` in Lens; Git's clone protection in Desktop) is correctly enforced in the "normal"/default code path, but a specific operation (`batchMigrateFollows` in Lens; `clone()` in Desktop) silently forces the guard off for every caller, regardless of how untrusted the input (the follower / the remote repository) is.

### Impact Explanation
If the specific exploit that `GIT_CLONE_PROTECTION_ACTIVE` was designed to block can be triggered through crafted repository content (e.g. crafted submodule/hook layouts or path collisions during clone), an attacker who merely gets a victim to clone their repository — via a normal "clone this repo" workflow, a fork, or an "Open in Desktop" link — gets that protection unconditionally disabled by Desktop itself. Depending on what the flag gates, this can translate into code execution on the victim's machine during the clone, entirely outside the local/admin-access threat model excluded by the task's scope, since the only thing the attacker needs to control is the cloned repository's content and get the victim to initiate a normal clone action.

### Likelihood Explanation
Every single clone Desktop performs goes through this exact code path with the flag hardcoded to `'false'` — there is no conditional branch, feature flag, or user prompt that re-enables the protection. Any attacker who can get a user to clone or "Open in Desktop" a repository they control satisfies the trigger condition; no other requirement is needed (no local access, no elevated privileges, no leaked credentials).

### Recommendation
- Do not unconditionally disable `GIT_CLONE_PROTECTION_ACTIVE`. Only override it if there is a specific, well-understood interaction between this flag and Desktop's progress-parsing/execution wrapper (`executionOptionsWithProgress`) that requires it, and scope the override to that narrow case.
- Otherwise, let the flag retain Git's own default (protection enabled) for all clone invocations, especially when the `url` originates from user input, deep links, or CLI arguments rather than from Desktop's own trusted internal recursive submodule handling (which is the only case this env var is normally meant to be set for).
- Add a regression test asserting that `clone()` does not disable Git's clone-time protections for attacker-influenced URLs (URL/CLI/deep-link driven clones).

### Proof of Concept
1. Confirm the unconditional override exists at the single clone choke point: [4](#0-3) .
2. Trace an attacker-controlled `url` reaching this function without any intermediate re-enabling of the protection:
   - Deep link: `x-github-client://openRepo/<url>` → `parseAppURL` → `open-repository-from-url` action [6](#0-5)  → `dispatchURLAction` → `openRepositoryFromUrl` → `openOrCloneRepository(url)` → Clone dialog pre-filled with the attacker's URL [7](#0-6) .
   - CLI: `--cli-clone <attacker-url>` → `dispatchCLIAction({kind:'clone-url', ...})` → `openOrCloneRepository(url)` [3](#0-2) .
   - In both cases the eventual call reaches `Dispatcher.clone` → `AppStore._clone` → `clone()` in `app/src/lib/git/clone.ts`, which always sets `GIT_CLONE_PROTECTION_ACTIVE: 'false'`.
3. The only mitigating check present, `isClonePathSensitive`, verifies the local destination directory, not the remote content, so it provides no defense against the specific exploit class this flag guards against [5](#0-4) .

**Caveat / what is unverified**: I could not find any comment, changelog entry, or additional code in this repository explaining *why* `GIT_CLONE_PROTECTION_ACTIVE` is set to `'false'`, nor could I verify from local code alone the exact upstream Git vulnerability class this environment variable currently guards against (the index does not contain the vendored Git/dugite source or its release notes). This assessment is based on the semantics implied by the variable's name (an "active protection" flag being forced off) and on the fact that the override is applied unconditionally to every clone regardless of trust in the remote. I'd recommend confirming the exact Git-side behavior this flag controls (e.g., by checking the embedded Git/dugite version's release notes) before treating this as a confirmed RCE — the code-level fact (unconditional disabling of a protection flag on all attacker-reachable clone paths) is solid, but the downstream impact depends on Git internals not present in this codebase's index.

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

**File:** app/src/lib/git/clone.ts (L68-84)
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
```

**File:** app/src/lib/parse-app-url.ts (L66-125)
```typescript
export function parseAppURL(url: string): URLActionType {
  const parsedURL = URL.parse(url, true)
  const hostname = parsedURL.hostname
  const unknown: IUnknownAction = { name: 'unknown', url }
  if (!hostname) {
    return unknown
  }

  const query = parsedURL.query

  const actionName = hostname.toLowerCase()
  if (actionName === 'oauth') {
    const code = getQueryStringValue(query, 'code')
    const state = getQueryStringValue(query, 'state')
    if (code != null && state != null) {
      return { name: 'oauth', code, state }
    } else {
      return unknown
    }
  }

  // we require something resembling a URL first
  // - bail out if it's not defined
  // - bail out if you only have `/`
  const pathName = parsedURL.pathname
  if (!pathName || pathName.length <= 1) {
    return unknown
  }

  // Trim the trailing / from the URL
  const parsedPath = pathName.substring(1)

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

**File:** app/src/ui/dispatcher/dispatcher.ts (L2050-2058)
```typescript
  public async dispatchCLIAction(action: CLIAction) {
    if (action.kind === 'clone-url') {
      const { branch, url } = action

      if (branch) {
        await this.openBranchNameFromUrl(url, branch)
      } else {
        await this.openOrCloneRepository(url)
      }
```

**File:** app/src/ui/dispatcher/dispatcher.ts (L2215-2233)
```typescript
  private async openOrCloneRepository(url: string): Promise<Repository | null> {
    const state = this.appStore.getState()
    const repositories = state.repositories
    const existingRepository = repositories.find(r =>
      this.doesRepositoryMatchUrl(r, url)
    )

    if (existingRepository) {
      return await this.selectRepository(existingRepository)
    }

    return this.appStore._startOpenInDesktop(() => {
      this.changeCloneRepositoriesTab(CloneRepositoryTab.Generic)
      this.showPopup({
        type: PopupType.CloneRepository,
        initialURL: url,
      })
    })
  }
```
