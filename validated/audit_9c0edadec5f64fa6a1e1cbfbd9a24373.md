## Title
`git clone --recursive` explicitly disables Git's upstream submodule-clone protection via `GIT_CLONE_PROTECTION_ACTIVE=false`, re-enabling RCE from malicious repositories - (File: `app/src/lib/git/clone.ts`)

## Summary
`clone()` in `app/src/lib/git/clone.ts` always sets the environment variable `GIT_CLONE_PROTECTION_ACTIVE: 'false'` when invoking `git clone --recursive` and passes attacker-controlled `url`/`path` straight to the command. [1](#0-0) 
This is the same class of "trust an untrusted/attacker-provided object as if it were validated" bug seen in the Sherlock report: there, a malicious observer got a failing/forged Solana transaction treated as a valid deposit because a safety check (transaction success) was skipped. Here, Desktop actively disables the safety mechanism Git itself ships to stop a hostile upstream repository (or its submodules/`.gitmodules`) from executing code during clone, exposing users the moment they clone a link, "Open in Desktop" deep-link target, or CLI clone URL that points to attacker infrastructure.

## Finding Description
`GIT_CLONE_PROTECTION_ACTIVE` is Git's own kill-switch for the hardening added to `git clone` (and `git submodule update --recursive`) to stop code execution triggered by maliciously crafted repositories/submodules (e.g. symlinked `.git` directories, crafted `.gitmodules` hook paths, or nested/duplicate submodule names that clash on case-insensitive or Unicode-normalizing filesystems). Setting it to `'false'` deliberately turns that protection off for every clone Desktop performs: [2](#0-1) 

The `url` and `path` values passed into `clone()` come directly from attacker-influenceable surfaces already present in the codebase:
- The `x-github-client://openRepo/...` deep link, parsed by `parseAppURL` and dispatched to `openOrCloneRepository` → clone dialog → `clone()` with the URL taken verbatim from the link. [3](#0-2) [4](#0-3) 
- The `github clone <url>` CLI command, which similarly funnels an externally supplied URL into the same clone path. [5](#0-4) 

Because `--recursive` is always passed, any submodules declared in the attacker's `.gitmodules` are fetched and checked out automatically as part of the same operation that has protection disabled: [6](#0-5) 

Unlike the observer bug in the report (which skipped verifying `Meta.Err == nil`), this is not a missing check — it is an explicit override of an upstream-provided check, with no accompanying compensating validation of the repository/submodule contents before or after the recursive clone completes.

## Impact Explanation
If the fix behind `GIT_CLONE_PROTECTION_ACTIVE` (or the class of clone/submodule RCE issues it addresses) applies to the user's installed Git version, an attacker who gets a victim to open an "Open in Desktop" link, click a crafted clone URL, or run `github clone <malicious-url>` can achieve code execution or file writes outside the intended repository directory the moment Desktop performs the recursive clone — i.e., before the user has reviewed or committed anything. This matches the accepted impact classes: code execution and file write outside the repo triggered by an attacker-controlled remote/repository reached via a link the user clicks, with no local access, admin rights, or social engineering beyond a single click required.

## Likelihood Explanation
Likelihood is high for any user running a Git version where this protection is meaningful and not superseded by other hardening, since:
- The disabling flag is unconditional — it's not scoped to trusted remotes or gated behind any prompt.
- All three practical entry points (deep link, "Clone Repository" URL flow, CLI `clone`) reach the same `clone()` function.
- `--recursive` is always used, ensuring untrusted submodule content is processed automatically without user opt-in.
It is somewhat mitigated if the bundled/embedded Git version does not implement or need `GIT_CLONE_PROTECTION_ACTIVE` at all, or if Desktop's embedded Git is patched such that the variable has no effect — this could not be verified from the available code/index (the variable's semantics live in the `dugite`/embedded Git binary, not in this repository), so the severity should be validated against the exact embedded Git version Desktop ships.

## Recommendation
- Remove the explicit `GIT_CLONE_PROTECTION_ACTIVE: 'false'` override so Git's built-in clone/submodule protections remain active by default.
- If this override exists to work around a specific compatibility issue, scope it narrowly (e.g., only for known-trusted, already-added repositories) rather than applying it unconditionally to all clones, including first-time clones of unknown/untrusted URLs reached via deep links or CLI.
- Add explicit validation/sanitization of clone URLs originating from `parse-app-url.ts` and `cli/main.ts` and consider deferring `--recursive` submodule initialization until after the top-level clone has been inspected, or warn users before recursively initializing submodules from an unfamiliar remote.

## Proof of Concept
1. Host a malicious Git repository containing a `.gitmodules`/submodule configuration crafted to exploit the recursive-clone submodule vulnerability class that `GIT_CLONE_PROTECTION_ACTIVE` guards against (e.g., a submodule path/name designed to collide with `.git` on the victim's filesystem).
2. Send the victim a link of the form `x-github-client://openRepo/<attacker-repo-url>` or have them run `github clone <attacker-repo-url>`.
3. Desktop calls `clone()` in `app/src/lib/git/clone.ts`, which sets `GIT_CLONE_PROTECTION_ACTIVE=false` and runs `git clone --recursive -- <attacker-repo-url> <path>`. [1](#0-0) 
4. Because Git's clone-time protection is disabled, the exploit trigger during recursive submodule initialization executes as it would on an unpatched/unprotected Git installation, achieving code execution or writing files outside the target clone directory without further user interaction.

Note: I was unable to determine from the indexed code alone which exact upstream Git CVE/protection `GIT_CLONE_PROTECTION_ACTIVE` corresponds to in Desktop's bundled `dugite`/Git version, since that logic lives in the embedded Git binary rather than this repository. A Devin session with full repository and build-environment access would be needed to confirm the exact embedded Git version and verify whether the disabled protection is currently exploitable in that version.

### Citations

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

**File:** app/src/lib/parse-app-url.ts (L66-128)
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

  return unknown
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

**File:** app/src/cli/main.ts (L53-69)
```typescript
if (args.help || args._.at(0) === 'help') {
  usage(0)
} else if (args._.at(0) === 'clone') {
  const urlArg = args._.at(1)
  // Assume name with owner slug if it looks like it
  const url =
    urlArg && /^[^\/]+\/[^\/]+$/.test(urlArg)
      ? `https://github.com/${urlArg}`
      : urlArg

  if (!url) {
    usage(1)
  } else if (typeof args.branch === 'string') {
    run(`--cli-clone=${url}`, `--cli-branch=${args.branch}`)
  } else {
    run(`--cli-clone=${url}`)
  }
```
