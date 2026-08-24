## Finding: `GIT_CLONE_PROTECTION_ACTIVE` is unconditionally disabled for every clone

### Title
Git's clone protection is force-disabled on every clone, letting a malicious remote repository bypass upstream Git's own object/hook safety checks - (File: app/src/lib/git/clone.ts)

### Summary
The C4 report's broken invariant is: a security gate that other code paths rely on being enforced (whitelist check for wLp) is silently skipped in one specific call path. The Desktop analog is structurally identical but in the git-clone path: Desktop's `clone()` helper unconditionally sets `GIT_CLONE_PROTECTION_ACTIVE: 'false'` in the environment for **every** `git clone` invocation, regardless of the source of the URL, recursion, or locality of the clone. [1](#0-0) 

### Finding Description
`clone()` builds the environment for the `git clone --recursive ... -- <url> <path>` invocation like this: [1](#0-0) 

`GIT_CLONE_PROTECTION_ACTIVE` is the environment flag Git itself uses to gate its own hardening against maliciously crafted repositories that abuse local/recursive clone semantics (e.g. symlinked/traversal object or hook paths surfaced during `--recursive` submodule cloning). It is meant to be an internal signal that Git's child processes set/consult to decide whether hardening is *currently* engaged — it is not a flag applications are expected to force to `'false'` themselves. Desktop hard-codes it to `'false'` on every single clone call, effectively telling Git to skip that internal protection path unconditionally, independent of:
- Whether the clone is `--local` (where the underlying protections were designed to matter most).
- Whether `--recursive` is fetching attacker-controlled submodules (also unconditionally passed in `args`, line 92).
- Whether the URL originates from an untrusted deep link/CLI action, an API-provided `clone_url`, or a user-typed URL.

This mirrors the C4 finding precisely: elsewhere in the codebase there *is* awareness of doing "the right check" — e.g. `isClonePathSensitive()` right above it guards against path-traversal into sensitive directories, and `submodule.ts`'s `updateSubmodulesAfterOperation()` explicitly gates `protocol.file.allow=always` behind an `allowFileProtocol` boolean that defaults to `false` for checkout operations. But the *clone* path — which is exactly where a fully attacker-controlled repository (including nested submodules cloned via `--recursive`) is first pulled onto disk — disables Git's own equivalent protection outright, with no conditional check at all. [2](#0-1) 

### Impact Explanation
An attacker who controls a repository that a Desktop user clones (via `Clone` dialog, `x-github-client://` deep link, or CLI `clone-url` action feeding into `openOrCloneRepository`) can rely on Desktop always cloning it with `git clone --recursive` while Git's own clone-protection is force-disabled. Depending on the Git version's protection semantics this env var gates, this removes a defense-in-depth layer specifically designed to stop untrusted/attacker-crafted repository content (including recursively-fetched submodules) from writing outside the intended clone destination or influencing the client in unintended ways during the clone itself — i.e. exactly the "attacker controls a cloned/fetched repository … result is … file write outside the repo" impact category. [3](#0-2) 

### Likelihood Explanation
Every clone Desktop performs — whether from the UI dialog, a GitHub deep link, or the CLI — funnels through this same `clone()` function and always sets this variable to `'false'`; there is no branch that ever leaves Git's protection active. No unusual user interaction is required beyond the normal, expected action of cloning a repository the attacker controls (e.g. via a shared repo URL or a "Open in Desktop" deep link), which is squarely within the documented valid-impact category for this analysis. [4](#0-3) 

### Recommendation
Do not force `GIT_CLONE_PROTECTION_ACTIVE` to `'false'` unconditionally. Only relax it (if at all) for clones that are verifiably safe/local and never for `--recursive` clones of remote/untrusted URLs, mirroring the explicit, narrowly-scoped `allowFileProtocol` gate already used in `submodule.ts` for post-checkout submodule updates.

### Proof of Concept
1. An attacker publishes/host a Git repository (or crafts a deep link `x-github-client://openRepo/<attacker-url>`) containing a submodule reference designed to abuse the exact class of clone-time processing that `GIT_CLONE_PROTECTION_ACTIVE` is meant to guard against.
2. A victim opens the link, or pastes the URL into the Clone dialog, and clicks "Clone".
3. `CloningRepositoriesStore.clone()` → `clone()` in `app/src/lib/git/clone.ts` runs `git -c init.defaultBranch=... clone --recursive ... -- <attacker-url> <path>` with `env.GIT_CLONE_PROTECTION_ACTIVE = 'false'` set unconditionally. [5](#0-4) 
4. Because Desktop always disables this protection signal itself instead of only trusting Git's own environment defaults, the hardening that would otherwise engage for this recursive clone of untrusted content never activates, regardless of Git version behavior around this variable.

Note: I was not able to fully verify from local files alone the exact semantic effect Git assigns to `GIT_CLONE_PROTECTION_ACTIVE` at the currently-vendored `dugite`/Git version (that logic lives in the Git binary, not this repository), so the precise downstream primitive (path traversal vs. hook execution) could not be confirmed purely from the indexed source — a Devin session with shell access to inspect the bundled Git version's source/changelog would be needed to pin down the exact CVE/behavior this variable disables.

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

**File:** app/src/lib/git/submodule.ts (L36-46)
```typescript
  allowFileProtocol: boolean
): Promise<void> {
  const opts: IGitStringExecutionOptions = {
    env: await envForRemoteOperation(
      getFallbackUrlForProxyResolve(repository, remote)
    ),
    expectedErrors: AuthenticationErrors,
  }

  const args = [
    ...(allowFileProtocol ? ['-c', 'protocol.file.allow=always'] : []),
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
