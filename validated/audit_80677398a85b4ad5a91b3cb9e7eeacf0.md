Based on my investigation, the strongest analog I found relates to how GitHub Desktop handles Git submodules with the `file://` protocol — a hardcoded, non-configurable trust decision similar in spirit to the original report's hardcoded/non-adjustable trade path (a fixed, unconditioned choice that removes the ability to apply case-by-case safety judgment).

### Title
Hardcoded `protocol.file.allow=always` override for submodule updates can re-enable local-file submodule cloning from untrusted repositories - (File: app/src/lib/git/submodule.ts)

### Summary
`updateSubmodulesAfterOperation` in [1](#0-0)  accepts an `allowFileProtocol` boolean that, when `true`, unconditionally passes `-c protocol.file.allow=always` to `git submodule update --init --recursive`. This flag is threaded in from `checkoutBranch`/`checkoutCommit` in [2](#0-1)  and [3](#0-2) , with a `false` default, but callers can opt in to force-allow the `file://` submodule protocol.

### Finding Description
Git upstream disabled `file://` submodule URLs by default (via `protocol.file.allow=user`) specifically to prevent a class of attacks where a malicious repository's `.gitmodules` points a submodule at a local `file://` path, causing an unwitting `git clone --recurse-submodules` or `git submodule update` to read/clone content from arbitrary local paths on the victim's machine (the class of issue behind CVE-2022-39253). GitHub Desktop's `updateSubmodulesAfterOperation` reintroduces this permissive behavior wholesale via `-c protocol.file.allow=always` whenever `allowFileProtocol` is `true` [4](#0-3) , rather than deciding on a per-submodule-URL or per-trust-level basis. Because this is a blanket boolean rather than a scoped/configurable policy (mirroring the hardcoded/non-adjustable pattern in the report), any code path that sets `allowFileProtocol = true` when processing an attacker-controlled repository's submodules bypasses Git's own safeguard entirely, for that whole operation.

### Impact Explanation
If a caller invokes checkout/submodule-update with `allowFileProtocol: true` while operating on a cloned/fetched repository whose `.gitmodules` is attacker-controlled, a malicious submodule entry using `file:///some/local/path` could cause Git to read or copy files from arbitrary local filesystem locations into the submodule's working tree — i.e., a read (and, once copied into the repo, potential write/exposure) outside the intended repository boundary. This matches the "attacker controls a cloned/fetched repository ... result is file write or read outside the repo" impact category.

### Likelihood Explanation
This is contingent on which call sites set `allowFileProtocol = true`; I confirmed the parameter exists and defaults to `false` in `checkoutBranch`/`checkoutCommit`, and found references to `protocol.file.allow` in `app/test/unit/git/pull/pull-test.ts` and `app/test/helpers/repositories.ts`, suggesting pull operations also plumb this flag through, but I was not able to fully trace every caller (e.g., `pull.ts`, `clone.ts`) within the available iterations to confirm under what real-world conditions (e.g., every checkout/pull vs. only specific first-party flows) the flag is actually set to `true`. This is a meaningful gap — if the flag is only ever `true` for trusted, locally-created scenarios, the exploitable path may not exist in practice; if it is set broadly for any checkout/pull against an arbitrary cloned repository, the likelihood is high.

### Recommendation
Avoid a blanket `allowFileProtocol` boolean; make the protocol policy configurable/scoped (e.g., only allow `file://` submodules for a specific list of pre-vetted, same-machine relative paths or never automatically override Git's default `protocol.file.allow=user`). At minimum, audit and document every call site that passes `allowFileProtocol: true` to ensure it is never reachable when processing submodules from an untrusted, externally-supplied repository.

### Proof of Concept
Not independently verified end-to-end due to inability to trace all `allowFileProtocol: true` call sites in this session; the mechanism itself (`-c protocol.file.allow=always` unconditionally applied) is confirmed at [5](#0-4) . A concrete PoC would require identifying a code path (e.g., pull/fetch of an attacker-hosted repository) where Desktop calls `updateSubmodulesAfterOperation(..., allowFileProtocol=true)` against that untrusted repository's submodules containing a `file://` URL pointing outside the repo.

**Note on confidence:** I could not fully confirm all real invocation sites of `allowFileProtocol=true` within the iteration budget, so this should be verified with a full trace of `pull.ts`, `clone.ts`, and any other callers of `updateSubmodulesAfterOperation` before treating this as a confirmed, exploitable vulnerability rather than a plausible analog based on the hardcoded-flag pattern.

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
