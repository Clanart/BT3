Based on code evidence, I found a strong structural analog: a boolean flag that silently downgrades a git security default is threaded through `checkoutBranch`/`checkoutCommit` into submodule initialization, exactly mirroring the report's pattern of "two code paths for the same operation, one of which skips a protection the other enforces, with the choice made by client-supplied state rather than by validating the untrusted content itself."

### Title
Submodule `file://` protection is bypassed via the `allowFileProtocol` flag threaded through branch/commit checkout - ([File: app/src/lib/git/submodule.ts])

### Summary
`updateSubmodulesAfterOperation` accepts an `allowFileProtocol` boolean that, when `true`, prepends `-c protocol.file.allow=always` to the `git submodule update --init --recursive` invocation, overriding Git's own default protection against `file://` submodule URLs. [1](#0-0) 
This flag is passed straight through from `checkoutBranch` and `checkoutCommit`. [2](#0-1) [3](#0-2) 

### Finding Description
Git's `protocol.file.allow` default (introduced after CVE‑2022‑39253) exists specifically to stop a hostile repository's `.gitmodules` from pointing a submodule at a `file:///` path on the victim's own disk (e.g. `~/.ssh`, another local repo, or arbitrary sensitive files) and having Git silently "clone" that local path into the working tree, exposing/duplicating its contents. Desktop's `updateSubmodulesAfterOperation` explicitly re-enables `file://` submodule URLs whenever `allowFileProtocol` is `true`: [4](#0-3) 

This is structurally identical to the contract bug: there are two invocation paths for the same operation (checkout → submodule update), one that keeps Git's default safety guard and one that turns it off, and the choice of which path runs is controlled by a caller-supplied boolean rather than by validating what the actual `.gitmodules` content in the checked-out tree contains. Just as `referralMint` let the caller pick a more favorable `winChance` for itself while the contract still trusted the caller-supplied `passportId`, here the caller decides whether Git's file-protocol guard is honored, independent of whether the branch/commit being checked out is attacker-controlled.

I was not able to trace, within the available search budget, every call site in `app-store.ts` that invokes `checkoutBranch`/`checkoutCommit` with `allowFileProtocol=true` to confirm which UI/business condition sets it. That is a real gap in this analysis and should be verified directly in the repo before treating this as a confirmed exploitable bug: the risk is only realized if the `true` path can be reached while checking out a branch/commit whose contents (and thus `.gitmodules`) are attacker-influenced (e.g. a fetched remote branch, a PR checkout, or a forked/cloned repository) — not if `true` is only ever used for the app's own known-safe test fixtures.

### Impact Explanation
If the permissive path is reachable for a branch/commit obtained from an untrusted remote (fetched PR, added remote, or opened via a `x-github-client://openrepo` deep link that already triggers `open-repository-from-url` handling, see `app/src/lib/parse-app-url.ts:98-125`), a malicious repository could ship a `.gitmodules` with a `file://` URL and have Desktop "clone" a local, sensitive path into the working tree as a submodule — a read of file/data outside the intended repo boundary, and a form of silent corruption of what ends up committed if the user then stages and pushes the exposed content.

### Likelihood Explanation
Likelihood hinges entirely on the (unconfirmed) call sites that pass `allowFileProtocol=true`. If that flag is only ever set for internally-trusted flows (e.g. re-checking out the app's own generated fixtures), likelihood is low/none. If it is reachable when checking out attacker-supplied branches/commits (e.g., a "retry" or "force checkout" UI path after a submodule failure), likelihood is high, because the guard bypass requires no special privileges beyond the attacker controlling repository content the user chooses to open/fetch/checkout — consistent with the accepted threat model (attacker-controlled cloned/fetched repository).

### Recommendation
- Confirm every caller of `checkoutBranch(..., allowFileProtocol=true)` / `checkoutCommit(..., allowFileProtocol=true)` and ensure the flag can never be `true` when the branch/commit originates from an untrusted remote, fork, or deep-link-driven open/clone flow.
- Prefer never disabling `protocol.file.allow` for content the user did not locally author; if a legitimate use case requires local-path submodules, restrict it to submodule URLs that resolve within the same repository/working copy root rather than globally re-enabling `file://` for the whole `submodule update` invocation.
- Add a regression test that checks out a crafted branch containing a `.gitmodules` with a `file://` URL through every code path that can set `allowFileProtocol=true`, asserting the submodule is not initialized/read.

### Proof of Concept
1. Attacker publishes a repository (or a branch/PR of a repository the victim already has open) containing a `.gitmodules` entry such as `url = file:///Users/victim/.ssh`.
2. Victim, using GitHub Desktop, checks out that branch/commit through whichever UI action ultimately calls `checkoutBranch`/`checkoutCommit` with `allowFileProtocol=true` (needs to be confirmed in `app-store.ts`).
3. `updateSubmodulesAfterOperation` runs `git -c protocol.file.allow=always submodule update --init --recursive`, which "clones" the local `file://` path into the working tree as a submodule directory, exposing its contents inside the checked-out repository. [5](#0-4)

### Citations

**File:** app/src/lib/git/submodule.ts (L29-55)
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

  if (!progressCallback) {
    await git(args, repository.path, 'updateSubmodules', opts)
    return
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
