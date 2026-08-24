### Title
Recursive `git clone` protection is explicitly disabled for every clone, re-exposing users to the CVE-2024-32002-class submodule/hooks RCE - (`app/src/lib/git/clone.ts`)

### Summary
GitHub Desktop's `clone()` helper unconditionally sets `GIT_CLONE_PROTECTION_ACTIVE: 'false'` and always passes `--recursive` for every clone operation, regardless of where the URL came from (clone dialog, "Open in Desktop" deep link, CLI `--cli-clone`). This bypasses the safeguard Git itself introduced to stop a malicious repository from smuggling a crafted, symlinked submodule `.git` directory (or exploiting case-insensitive/case-preserving filesystem quirks) that gets populated with attacker-controlled hook files and executed on checkout — the same broken-invariant pattern as the report (an unauthenticated, attacker-influenced input silently overriding a protective setting with no access-control/consent gate).

### Finding Description
`clone()` in `app/src/lib/git/clone.ts` builds the environment for every clone with: [1](#0-0) 
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
`GIT_CLONE_PROTECTION_ACTIVE` is the environment toggle Git added to detect and refuse clones of repositories crafted to place submodule `.git` metadata (via symlinks or case-collision tricks) in a location that overwrites the top-level `.git/hooks` (or similar sensitive paths) during a `--recursive` checkout, which then executes attacker-controlled hook scripts. Desktop force-disables this Git-side protection for *every* clone, and always clones recursively, so any protection the installed Git binary would otherwise apply against a hostile, attacker-authored repository is turned off before the URL is even inspected.

This is invoked with fully attacker-influenced input: the URL is whatever the user pasted into the clone dialog, whatever came from the "Open in Desktop" `x-github-client://openrepo` deep link (parsed in `parseAppURL`/`handleAppURL` in `app/src/main-process/main.ts`), or a `--cli-clone` argument — none of which are validated against the repository's trust level before this flag is disabled. [2](#0-1) [3](#0-2) 

The existing "unsafe directory" guard (`getRepositoryType` / `addSafeDirectory` flow surfaced in `add-existing-repository.tsx` and `missing-repository.tsx`) only protects *already-existing local* directories owned by another OS user; it never runs during `clone`, so it provides no coverage here. [4](#0-3) 

### Impact Explanation
If the attacker crafts a repository that a victim clones through Desktop's UI, deep link, or CLI flag, disabling `GIT_CLONE_PROTECTION_ACTIVE` removes Git's own defense-in-depth check against the submodule/hooks-overwrite class of RCE, on any filesystem/Git-version combination where that check would otherwise have caught the malicious layout. This is a code-execution primitive delivered purely by an attacker-controlled cloned repository, matching the strongest category in scope.

### Likelihood Explanation
Every single clone performed by Desktop (dialog clone, "Open in Desktop" deep link, and CLI `--cli-clone`) goes through this same `clone()` function, so the disabled protection is not an edge case — it is the default, unconditional behavior for all remote input. The only requirement is that the victim clones an attacker-hosted or attacker-linked repository, which is the normal, expected Desktop workflow.

### Recommendation
Do not disable `GIT_CLONE_PROTECTION_ACTIVE`; let Git's built-in clone-time protection remain active. If `--recursive` must be used, keep the protection enabled and only relax it after the top-level repository has been fetched and inspected/vetted (e.g., prompting the user the same way the "unsafe directory"/untrusted-owner flow does), rather than disabling the check globally before any content is known.

### Proof of Concept
1. Attacker publishes a Git repository whose `.gitmodules`/tree layout is crafted per the CVE-2024-32002 submodule-hooks technique (symlinked or case-colliding `.git` path for a submodule).
2. Attacker sends the victim a link such as `x-github-client://openrepo/https://github.com/attacker/evil-repo` (handled by `parseAppURL`/`handleAppURL`) or simply asks the victim to paste the clone URL into Desktop's Clone dialog.
3. Desktop calls `clone(url, path, options, ...)` in `app/src/lib/git/clone.ts`, which runs `git -c init.defaultBranch=... clone --recursive -- <url> <path>` with `GIT_CLONE_PROTECTION_ACTIVE=false` set in the environment.
4. Because the protective check is disabled, the crafted submodule layout is materialized as-is; upon the subsequent recursive checkout, the attacker-controlled hook executes in the victim's environment.

Note: I could not execute Git locally to confirm the exact minimum Git version required to reproduce the underlying overwrite (the CVE fix behavior differs slightly by platform/filesystem), so likelihood is bounded by which Git version/OS filesystem the victim is running; this should be validated against the bundled Git version in a live Desktop session.

### Citations

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

**File:** app/src/main-process/main.ts (L159-168)
```typescript
function handleAppURL(url: string) {
  log.info('Processing protocol url')
  const action = parseAppURL(url)
  onDidLoad(window => {
    // This manual focus call _shouldn't_ be necessary, but is for Chrome on
    // macOS. See https://github.com/desktop/desktop/issues/973.
    window.focus()
    window.sendURLAction(action)
  })
}
```

**File:** app/src/lib/parse-app-url.ts (L66-85)
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
