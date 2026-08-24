## Finding: Git's Anti-Exfiltration Guard for Recursive Submodule Clones Is Explicitly Disabled

### Title
Recursive clone of attacker‑controlled repository executes with Git's clone‑protection guard disabled - (`app/src/lib/git/clone.ts`)

### Summary
Every clone performed by Desktop — including clones triggered by an unprivileged, attacker‑controlled deep link (`x-github-client://openRepo/<url>`) — is executed with `--recursive` and with the environment variable `GIT_CLONE_PROTECTION_ACTIVE` explicitly forced to `'false'`. This is the exact analog of the "contract can be destructed"/"no way to roll back" class of bug: a normally-guarded, security-relevant operation (Git's internal safety check that prevents a maliciously crafted repo+submodule tree from writing/reading files outside the clone destination during recursive submodule clones) is unconditionally disabled by application code, with no way for the invariant to be restored once the clone runs.

### Finding Description
`clone()` builds the argument list for `git clone --recursive` and merges a fixed environment override into every invocation: [1](#0-0) 

```
const env = {
  ...(await envForRemoteOperation(url)),
  GIT_CLONE_PROTECTION_ACTIVE: 'false',
}
...
const args = ['-c', `init.defaultBranch=${defaultBranch}`, 'clone', '--recursive']
```

`GIT_CLONE_PROTECTION_ACTIVE` is a Git-internal safety flag that Git itself sets when it re-invokes a protected clone step for submodules, so that submodule/local/`file://` clones performed as part of a `--recursive` top-level clone cannot be abused to write hardlinks/symlinks or read files outside the intended checkout directory. By forcing this variable to `'false'` in the environment Desktop supplies to `git`, the app tells every clone/submodule-clone process it is safe to skip that check — regardless of whether the repository content (which is entirely attacker controlled, since the attacker owns the source repo) contains a `.gitmodules` file with a hostile submodule URL (e.g. a local `file://` path, or a path designed to trigger hardlink/symlink based file exfiltration during recursive submodule population).

The `url` parameter driving this clone is attacker controlled through an unprivileged vector: the `x-github-client://openRepo/<url>` deep link is parsed by `parseAppURL` and forwarded almost unchanged as the clone target: [2](#0-1) 

and then flows through `Dispatcher.openOrCloneRepository` → `AppStore._clone` → `CloningRepositoriesStore.clone` → this `clone()` function: [3](#0-2) [4](#0-3) 

Note that `parseAppURL` only validates the `pr`/`branch`/`filepath` query parameters (rejecting invalid ref characters and non-relative paths); it performs **no validation of the `url` itself**, so the destination repository content and any submodules it declares are entirely under attacker control once the user is convinced to click the link (or, equivalently, once they clone/fetch any malicious repository through the normal "Clone repository" URL flow, which uses the same `clone()`).

### Impact Explanation
Because the recursive-clone protection is force-disabled for every clone, a repository that a user opens via link (or otherwise adds/clones) with a crafted `.gitmodules`/submodule configuration can potentially cause Git to write files (via hardlinks/symlinks created during submodule population) outside of the intended clone directory on the user's disk — i.e., file write outside the repo, matching the "Valid Impact" criteria (attacker controls a cloned/fetched repository, result is file write/read outside the repo). This is functionally the same "irrecoverable, over-privileged destructive action with no confirmation and no way to roll back" pattern described in the source report: a safety mechanism designed to gate a dangerous, hard-to-reverse operation (writing arbitrary filesystem entries via recursive submodule clone) has been categorically switched off rather than conditionally/safely applied.

### Likelihood Explanation
The disabling is unconditional and applies to 100% of clone operations, including the fully unprivileged deep-link-triggered flow (`x-github-client://openRepo/...`) which requires only that the victim click a link — no local access, no admin rights, and no pre-existing malware are needed. The only remaining variable is whether the installed Git binary is a version whose submodule-clone protections are gated by this env var; on any such version, Desktop actively opts out of the protection for every clone, including ones sourced from completely untrusted, attacker-hosted repositories.

### Recommendation
Do not force `GIT_CLONE_PROTECTION_ACTIVE` to `'false'`. Let Git apply its default (protected) behavior for recursive/submodule clones, especially for repositories/URLs originating from untrusted sources such as deep links. If there is a legitimate reason certain internal recursive re-invocations need this disabled, scope the override narrowly (only for the specific trusted sub-invocation) instead of globally for every `clone()` call, and add regression tests that a malicious `.gitmodules` cannot cause writes outside the destination directory.

### Proof of Concept
1. Attacker creates a public GitHub repository containing a `.gitmodules` file whose submodule URL is crafted to exploit the recursive-submodule-clone hardlink/symlink exfiltration class of issue that `GIT_CLONE_PROTECTION_ACTIVE` is meant to prevent.
2. Attacker sends the victim a link: `x-github-client://openRepo/https://github.com/attacker/evil-repo`.
3. Victim (who has Desktop installed and registered as the protocol handler, per `app/src/main-process/main.ts`) clicks the link.
4. `handleAppURL` → `parseAppURL` accepts the URL without validating repository content → `Dispatcher.openOrCloneRepository` → `AppStore._clone` → `clone()` executes `git clone --recursive` with `GIT_CLONE_PROTECTION_ACTIVE=false` against the attacker's repo.
5. Because the protection guard is disabled, the malicious submodule configuration is processed without Git's normal safety check, allowing file writes/reads outside the destination clone directory during the recursive submodule population step.

**Unverified/uncertain:** I could not execute Git or inspect the installed Git version's C source from this environment to confirm the exact CVE/behavior this specific protection guards against, so the precise exploitation mechanics (which submodule/hardlink technique triggers the write) should be validated against the actual bundled Git version in a live Desktop build.

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

**File:** app/src/lib/parse-app-url.ts (L98-124)
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

**File:** app/src/lib/stores/app-store.ts (L5669-5690)
```typescript
  /** This shouldn't be called directly. See `Dispatcher`. */
  public _clone(
    url: string,
    path: string,
    options: { branch?: string; defaultBranch?: string } = {}
  ): {
    promise: Promise<boolean>
    repository: CloningRepository
  } {
    const promise = this.cloningRepositoriesStore.clone(url, path, options)
    const repository = this.cloningRepositoriesStore.repositories.find(
      r => r.url === url && r.path === path
    )!

    promise.then(success => {
      if (success) {
        this.statsStore.recordCloneRepository()
      }
    })

    return { promise, repository }
  }
```
