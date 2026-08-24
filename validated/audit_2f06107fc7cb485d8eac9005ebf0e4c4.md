## Title
`git clone` sets `GIT_CLONE_PROTECTION_ACTIVE=false`, disabling Git's built-in protection against malicious repository content during recursive submodule clone - (File: `app/src/lib/git/clone.ts`)

### Summary
Git 2.45.1+ introduced `GIT_CLONE_PROTECTION_ACTIVE` as a safety mechanism (fix for CVE-2024-32004) that prevents a freshly cloned repository from having its just-fetched, attacker-controlled config/hooks/`.gitmodules` content acted upon during the same clone operation, closing a class of remote-code-execution bugs where a crafted repository (e.g., with symlinked `.git` internals or a malicious `core.fsmonitor`/hooks entry combined with `--recursive` submodule cloning) can execute code as soon as it is cloned. GitHub Desktop's `clone()` function explicitly disables this protection.

### Finding Description
In `clone()`, Desktop constructs the clone environment and unconditionally sets: [1](#0-0) 

and then always runs the clone with `--recursive`: [2](#0-1) 

The attacker primitive here is the report's "attacker controls a cloned/fetched repository" — a malicious remote (the exact object the report's PoC exploits from the other side, but here it's a Git repository/URL a user is enticed to clone via "Clone repository" or the `x-github-client://openRepo/...` deep link handled in `parseAppURL`/`openRepositoryFromUrl`) can ship a crafted `.gitmodules` (relative/absolute submodule paths, hook payloads, or nested submodule chains) that Git's own clone-protection mechanism was specifically designed to neutralize. Desktop's environment override (`GIT_CLONE_PROTECTION_ACTIVE: 'false'`) turns that protection off for every clone, including ones reached from an untrusted, attacker-supplied URL through the deep-link flow: [3](#0-2) [4](#0-3) 

This is structurally analogous to the report's bug class: a verifier/guard (`id_leak_verifier` in Move / `GIT_CLONE_PROTECTION_ACTIVE` in Git) that is supposed to hold an invariant ("objects with Key ability were constructed safely" / "content fetched from a remote in this clone must not be trusted until the clone completes") is bypassed by an explicit mechanism the host application controls (upgrade capability addition / explicit environment override), letting attacker-supplied data cross the trust boundary the guard was meant to enforce.

The path-traversal and sensitive-directory checks in this same file (`isClonePathSensitive`, `resolveWithin`) show Desktop is aware of and defends against some clone-based attacks, but there is no equivalent compensating control for the specific class of bug `GIT_CLONE_PROTECTION_ACTIVE` addresses: [5](#0-4) 

### Impact Explanation
If Git's protection is required to stop a known bug class of "malicious repository content executed during clone" (this is exactly what `GIT_CLONE_PROTECTION_ACTIVE` was created for upstream), disabling it means Desktop is unconditionally re-exposed to that class of issue for every clone, most dangerously ones reached with `--recursive` submodule initialization, satisfying the report's valid-impact criteria: "attacker controls a cloned/fetched repository ... result is code execution."

### Likelihood Explanation
Any repository the user clones through Desktop's UI or through the `x-github-client://openRepo` deep-link handler triggers this exact code path with no additional user action beyond confirming the clone, and the override is unconditional (`GIT_CLONE_PROTECTION_ACTIVE: 'false'` is always set, not feature-flagged or scoped to trusted hosts).

### Recommendation
Remove the `GIT_CLONE_PROTECTION_ACTIVE: 'false'` override (or scope it narrowly with a documented justification and additional compensating checks) so Desktop clones benefit from Git's upstream clone-time protections; if a specific legitimate feature (e.g., initializing submodules from local/relative paths) requires disabling it, gate that behind an explicit, narrowly-scoped flag rather than a blanket override for all clones.

### Proof of Concept
I could not fully verify an end-to-end exploit chain (e.g., confirm which specific `.gitmodules`/hook payload Git's protection currently blocks in the bundled Git version, or reproduce code execution) because that requires running the embedded Git binary and constructing a matching malicious repository, which is outside what the index/tools available to me can execute. What is confirmed from local code evidence is only the disabling of the guard itself: [6](#0-5) 

I recommend a Devin session with terminal/git access to (1) identify the exact Git version bundled in `dugite`, (2) confirm what `GIT_CLONE_PROTECTION_ACTIVE` guards against in that version, and (3) build a concrete malicious repository/`.gitmodules` PoC that is blocked with the protection enabled but succeeds when cloned through Desktop's `clone()` with the override in place.

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

**File:** app/src/lib/parse-app-url.ts (L66-99)
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
```
