### Title
Recursive clone of untrusted repositories runs with Git's clone-time symlink/hook protection explicitly disabled - ([File: app/src/lib/git/clone.ts])

### Summary
Every clone performed by GitHub Desktop (whether via the UI "Clone repository" dialog, `git clone <url>` from the CLI wrapper, "Open in Desktop"/`x-github-client://openrepo` deep links, or opening a PR/fork) goes through `clone()` in `app/src/lib/git/clone.ts`. This function always passes `--recursive` to `git clone` and, in the same environment block, explicitly sets `GIT_CLONE_PROTECTION_ACTIVE: 'false'`. [1](#0-0) 

`GIT_CLONE_PROTECTION_ACTIVE` is the upstream Git safety switch (default "active"/true) that was introduced to close the class of clone-time symlink/hook-execution vulnerabilities (e.g. the fix associated with CVE-2024-32004, where a malicious repository with submodules and symlinked `.git` metadata could get its hooks executed during a recursive clone before the user ever opens or trusts the repo). Desktop is unconditionally forcing this protection back off for every clone.

### Finding Description
The report's root cause pattern is: a security-relevant invariant that should gate a repeated/attacker-triggerable action is missing or bypassed, and the code path that depends on it is reachable by attacker-supplied input.

In Desktop's case the analogous broken invariant is Git's own recursive-clone symlink/hooks protection. `clone()` is the single implementation used for every "clone an arbitrary attacker-supplied URL" surface in the app — the Clone dialog (`app/src/ui/clone-repository/clone-repository.tsx`), the CLI (`app/src/cli/main.ts` → `cli-action.ts`), and the `open-repository-from-url` deep-link handler (`app/src/lib/parse-app-url.ts`, `app/src/ui/dispatcher/dispatcher.ts:2215-2233` `openOrCloneRepository`). All of these ultimately call `clone(url, path, options, ...)` with `--recursive` and `GIT_CLONE_PROTECTION_ACTIVE: 'false'`. [2](#0-1) [3](#0-2) 

While `clone.ts` does implement a defense-in-depth check (`isClonePathSensitive`) to stop the *destination path itself* from resolving into `~/.ssh`, `~/.gnupg`, `~/.config`, etc., that only protects the top-level clone target directory. It does nothing about the actual vulnerability class that `GIT_CLONE_PROTECTION_ACTIVE` exists to stop: a malicious remote repository (fully attacker-controlled content, reachable via a normal clone URL or an `x-github-client://openrepo/...` deep link) crafting its tree/submodules/`.git` metadata such that, during the recursive clone Git performs, a hook or symlinked path gets planted/executed on the victim's filesystem before the user has reviewed anything. `isClonePathSensitive` is a path check on the destination the *user* chose; it has no knowledge of and does not compensate for the protection Git itself disables via the env var.

### Impact Explanation
If `GIT_CLONE_PROTECTION_ACTIVE=false` actually re-enables the pre-fix vulnerable behavior in the underlying Git binary, an attacker who gets a victim to clone (or open via `x-github-client://openrepo/<attacker-url>`) a crafted repository with `--recursive` could achieve local code execution as the Desktop user, entirely outside the sandboxed renderer and outside the eventual repo trust/review step — satisfying the "attacker controls a cloned/fetched repository ... code execution" criterion. This is more severe than a file-open path traversal because it doesn't require the victim to click "reveal in folder" on a `filepath` param (which, unlike this issue, Desktop does defend correctly with `isAbsolute`/`resolveWithin` checks in `openRepositoryFromUrl`, `app/src/ui/dispatcher/dispatcher.ts:1940-1972`). [4](#0-3) 

### Likelihood Explanation
Every clone in the app takes this path, and cloning an unfamiliar URL/deep link is an ordinary, expected user action (not "unnatural steps") — the exact scenario the task requires (attacker-controlled cloned repository, no local/physical access, no prior malware, no admin rights). The likelihood is high assuming this env var indeed maps to Git's clone-protection feature flag in the bundled/dugite Git version Desktop ships; I could not verify from the indexed files which exact Git version is vendored or exercise the flag's real runtime effect, since that lives in the native `dugite`/git binary, not in this repository's TypeScript sources.

### Recommendation
- Remove the explicit `GIT_CLONE_PROTECTION_ACTIVE: 'false'` override in `app/src/lib/git/clone.ts` so Git's built-in clone-time symlink/hook protections stay active for all recursive clones of untrusted, user/attacker-supplied URLs.
- If the override was added to work around a specific breaking change (e.g., needed for some legitimate submodule workflow), gate it behind an explicit, narrowly-scoped opt-in rather than a blanket default, and never apply it to clones triggered by `open-repository-from-url` deep links or arbitrary user-entered URLs in the Clone dialog.
- Confirm the vendored `dugite`/Git version and validate with a proof-of-concept malicious submodule/symlink repository that recursive clone no longer executes attacker-controlled hooks.

### Proof of Concept
Not independently executable from the indexed source alone (requires the native Git/dugite binary behavior), but the reachable path is:
1. Attacker crafts a git repository containing a malicious submodule / symlinked `.git`/hooks structure of the kind blocked by Git's clone-protection feature (per the class of issue `GIT_CLONE_PROTECTION_ACTIVE` was created to mitigate).
2. Attacker sends the victim a link such as `x-github-client://openrepo/https://evil.example/attacker/repo` or simply shares the clone URL.
3. Victim clicks the link (handled by `handleAppURL` in `app/src/main-process/main.ts:159-168` → `parseAppURL` → `dispatchURLAction` → `openOrCloneRepository` in `dispatcher.ts:2215`) or pastes the URL into the Clone dialog. [5](#0-4) 
4. Desktop calls `clone()` with `--recursive` and `GIT_CLONE_PROTECTION_ACTIVE: 'false'`, executing the crafted repository's hook/symlink payload during the clone itself. [1](#0-0)

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

**File:** app/src/ui/dispatcher/dispatcher.ts (L1957-1972)
```typescript
    if (filepath !== null) {
      if (isAbsolute(filepath)) {
        log.error(`Refusing to open absolute path: ${filepath}`)
        return
      }

      const resolved = await resolveWithin(repository.path, filepath)

      if (resolved !== null) {
        shell.showItemInFolder(resolved)
      } else {
        log.error(
          `Prevented attempt to open path outside of the repository root: ${filepath}`
        )
      }
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
