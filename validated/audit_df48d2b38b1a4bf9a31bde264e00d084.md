Based on the investigation, the closest structural analog to the PerpDepository bug — an unprivileged/attacker-influenced parameter that silently controls a security-relevant path with no server-side re-validation — is the `allowFileProtocol` flag that Desktop threads through checkout/pull operations into `git submodule update`, re-enabling `file://` submodule URLs (a protocol Git disabled by default specifically to stop local-file-read attacks via malicious `.gitmodules` entries, following CVE-2022-39253).

### Title
Cloned/fetched repository can re-enable `file://` submodule protocol via forced `protocol.file.allow=always`, enabling out-of-repo file read - (File: app/src/lib/git/submodule.ts)

### Summary
`updateSubmodulesAfterOperation` accepts a boolean `allowFileProtocol` and, when true, prepends `-c protocol.file.allow=always` to the `git submodule update --init --recursive` invocation. This is invoked from `checkoutBranch` and `checkoutCommit`, both of which accept `allowFileProtocol` and default it to `false`, but the actual repository being checked out — and its `.gitmodules` entries — are fully attacker-controlled (a cloned/fetched repository). [1](#0-0) [2](#0-1) [3](#0-2) 

### Finding Description
Git upstream disabled `file://` as a default-allowed submodule protocol (`protocol.file.allow=user`, i.e. only allowed when explicitly invoked by the user, not embedded in someone else's repository config) precisely because a `.gitmodules` file controlled by a repository author can point a submodule at an arbitrary local path (`file:///Users/victim/.ssh`, `file:///etc/passwd`, etc.). When cloned and `git submodule update --init --recursive` runs, Git will "clone" that local path into the submodule directory inside the victim's working tree, exposing file contents that Git can read as part of a repository (e.g. any local git repo, or via `file://` traversal to non-repo files it can still stat) — content the attacker did not otherwise have any way to see.

Desktop's `updateSubmodulesAfterOperation` explicitly overrides this hardening with `protocol.file.allow=always` whenever the `allowFileProtocol` parameter is `true`. [4](#0-3) 
The broken invariant is the same shape as the PerpDepository bug: a caller-supplied boolean silently changes trust boundaries for content that is not controlled by the calling user, but by a third party (here, the cloned repository's `.gitmodules`, there, the `account` parameter). Git's own default already treats this as unsafe unless the *user* explicitly requests it; Desktop's code, however, threads a plain boolean through `checkoutBranch`/`checkoutCommit` without any code-visible verification that the repository/submodule set being processed was actually vetted by the user for this specific submodule URL.

### Impact Explanation
If `allowFileProtocol=true` is reachable for repositories whose submodule graph comes from an untrusted/attacker-controlled remote (e.g. a `.gitmodules` file added in a branch/PR/fork the user checks out), an attacker can point a submodule URL at sensitive local paths and have Git copy their contents into the working directory as part of `submodule update`. Once materialized inside the repository, that content could be silently committed/pushed by the user (data exfiltration), or otherwise read by the attacker if the resulting files are later inspected — this is a read-outside-the-repository primitive triggered purely by checking out an attacker-authored commit/branch.

### Likelihood Explanation
I could not fully verify, within the available index, every call site that sets `allowFileProtocol=true` in `app/src/lib/stores/app-store.ts` (the grep found 8 matches there but the surrounding logic — i.e., whether it's gated to only same-machine "trusted"/previously-initialized submodules such as the test fixtures shown, versus being applied broadly to any checkout of a freshly cloned/fetched repository — was not retrievable from the index). The test fixtures and unit tests that exercise `protocol.file.allow=always` all use local-repository submodules the test itself created, which is a legitimate, low-risk use case (local dev/test workflows), not necessarily evidence of the production gating logic. This uncertainty materially affects the real-world likelihood: if `app-store.ts` restricts `allowFileProtocol=true` to submodules that were already initialized/trusted (e.g. only re-checking out already-cloned local submodules), this would not be exploitable by a remote/attacker-controlled repository. Due to index size limits, the full logic in `app/src/lib/stores/app-store.ts` around these 8 call sites was not available to confirm this either way.

### Recommendation
Confirm (and if necessary restrict) the conditions under which `allowFileProtocol` is set to `true` in `app-store.ts`'s checkout/pull code paths, ensuring it can never be `true` for submodules whose URL comes from a `.gitmodules` file introduced by an untrusted remote/branch/PR the user has not explicitly vetted. If the current behavior is unconditional or applies broadly to any checkout, gate it so `protocol.file.allow=always` is only passed when the submodule remote URL was already present/approved in a prior, user-consented state (mirroring Git's own `protocol.file.allow=user` default intent) rather than trusting the boolean flag alone.

### Proof of Concept
Not independently verified end-to-end due to inability to confirm the exact `app-store.ts` call-site gating (see Likelihood Explanation), but the mechanism is demonstrated directly by the existing test helpers, which show that passing `-c protocol.file.allow=always` alongside `submodule add`/`submodule update` allows a `file://`-style local path to be used as a submodule source: [5](#0-4) [6](#0-5) 
Conceptually: a malicious repository adds `.gitmodules` with a submodule URL pointing at a local sensitive path (e.g. `file:///Users/victim/Library/Application Support/GitHub Desktop/`); once the victim clones/fetches and checks out the branch/commit containing this `.gitmodules`, if the reachable `checkoutBranch`/`checkoutCommit` call passes `allowFileProtocol=true`, `updateSubmodulesAfterOperation` executes `git -c protocol.file.allow=always submodule update --init --recursive`, copying the target path's contents into the repository working tree where they become visible/committable.

### Citations

**File:** app/src/lib/git/submodule.ts (L29-51)
```typescript
export async function updateSubmodulesAfterOperation<T extends Progress>(
  repository: Repository,
  remote: IRemote | null,
  progressCallback: ((progress: T) => void) | undefined,
  progressKind: T['kind'],
  title: string,
  targetOrRemote: string,
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
    'submodule',
    'update',
    '--init',
    '--recursive',
  ]
```

**File:** app/src/lib/git/checkout.ts (L102-141)
```typescript
export async function checkoutBranch(
  repository: Repository,
  branch: Branch,
  currentRemote: IRemote | null,
  progressCallback?: ProgressCallback,
  allowFileProtocol: boolean = false
): Promise<true> {
  const title = `Checking out branch ${branch.name}`
  const opts = await getCheckoutOpts(
    repository,
    title,
    branch.name,
    currentRemote,
    progressCallback
      ? clampProgress(0, CheckoutStepWeight, progressCallback)
      : undefined,
    `Switching to ${__DARWIN__ ? 'Branch' : 'branch'}`
  )

  const baseArgs = getCheckoutArgs(progressCallback)
  const args = [...baseArgs, ...(await getBranchCheckoutArgs(branch))]

  await git(args, repository.path, 'checkoutBranch', opts)

  // Update submodules after checkout
  await updateSubmodulesAfterOperation(
    repository,
    currentRemote,
    progressCallback
      ? clampProgress<ICheckoutProgress>(
          CheckoutStepWeight,
          1,
          progressCallback
        )
      : undefined,
    'checkout',
    title,
    branch.name,
    allowFileProtocol
  )
```

**File:** app/src/lib/git/checkout.ts (L163-202)
```typescript
export async function checkoutCommit(
  repository: Repository,
  commit: CommitOneLine,
  currentRemote: IRemote | null,
  progressCallback?: ProgressCallback,
  allowFileProtocol: boolean = false
): Promise<true> {
  const title = `Checking out ${__DARWIN__ ? 'Commit' : 'commit'}`
  const target = shortenSHA(commit.sha)
  const opts = await getCheckoutOpts(
    repository,
    title,
    target,
    currentRemote,
    progressCallback
      ? clampProgress(0, CheckoutStepWeight, progressCallback)
      : undefined
  )

  const baseArgs = getCheckoutArgs(progressCallback)
  const args = [...baseArgs, commit.sha]

  await git(args, repository.path, 'checkoutCommit', opts)

  // Update submodules after checkout
  await updateSubmodulesAfterOperation(
    repository,
    currentRemote,
    progressCallback
      ? clampProgress<ICheckoutProgress>(
          CheckoutStepWeight,
          1,
          progressCallback
        )
      : undefined,
    'checkout',
    title,
    target,
    allowFileProtocol
  )
```

**File:** app/test/helpers/repositories.ts (L299-309)
```typescript
  await exec(
    [
      '-c',
      'protocol.file.allow=always',
      'submodule',
      'add',
      submoduleRepo.path,
      'test-submodule',
    ],
    repo.path
  )
```

**File:** app/test/unit/git/rev-parse-test.ts (L59-73)
```typescript
      await git(
        ['commit', '--allow-empty', '-m', 'Initial commit'],
        secondRepoPath,
        ''
      )

      await git(
        [
          // Git 2.38 (backported into 2.35.5) changed the default here to 'user'
          ...['-c', 'protocol.file.allow=always'],
          ...['submodule', 'add', '../repo2'],
        ],
        firstRepoPath,
        ''
      )
```
