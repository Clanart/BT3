Based on my investigation, I was unable to fully trace the exact call sites in `app-store.ts`/`dispatcher.ts` (6 matches for `checkoutBranch(`/`checkoutCommit(` I found but could not open before running out of tool calls) that decide the value passed as `allowFileProtocol`. I can confirm the sink and its wiring, but not with certainty whether an attacker-controlled branch/PR checkout can reach it with `allowFileProtocol = true`. I'm reporting what is concretely verifiable and flagging the unverified part explicitly.

### Title
Submodule `protocol.file.allow=always` override re-enables local-file submodule cloning during checkout/pull of attacker-controlled refs - (File: app/src/lib/git/submodule.ts)

### Summary
Git upstream hardened submodule handling (CVE‑2022‑39253 class of issues) by defaulting `protocol.file.allow` to `user`, which blocks a checked-out `.gitmodules` file from silently causing `git submodule update` to clone/read arbitrary local `file://` paths belonging to a different user/outside the working copy. Desktop's `updateSubmodulesAfterOperation` explicitly re-enables the disabled protection by passing `-c protocol.file.allow=always` whenever it is called with `allowFileProtocol=true` [1](#0-0) , and this same boolean is threaded through `checkoutBranch`/`checkoutCommit` [2](#0-1) [3](#0-2) , which are the code paths used when checking out a branch/PR that can originate from an untrusted fork or a fetched remote.

### Finding Description
`.gitmodules` is fully attacker-controlled content that ships inside a cloned/fetched repository (analogous to the ENS report's attacker-controlled "expired name" state — a value the victim's client trusts implicitly once obtained from an external, adversarial source). When Desktop runs `git submodule update --init --recursive` with `protocol.file.allow=always`, git will honor `file://` (and bare local path) submodule URLs unconditionally, which is exactly the setting upstream git restricted by default because a malicious `.gitmodules` can point a submodule at any local path (e.g. another user's home directory, `.ssh`, or another checked-out repository) and have git clone/read it into the victim's working tree.

The corrupted/bypassed value here is the `protocol.file.allow` git config: the code path exists specifically to *reintroduce* a state git upstream disabled by default, and it does so based on a caller-supplied `allowFileProtocol` boolean rather than on any inspection of whether the operation is happening on a trusted vs. untrusted (attacker-controlled) ref. This mirrors the ENS bug's pattern: a security check (`CANNOT_CREATE_SUBDOMAIN` / here, "don't allow file-protocol submodules") is bypassed because the code trusts a coarse boolean signal instead of validating the actual state of the object being operated on (an attacker-authored `.gitmodules`).

### Impact Explanation
If `allowFileProtocol=true` is reachable for a checkout/pull driven by content the attacker controls (a PR branch, a fork remote, or a fetched ref), a malicious `.gitmodules` entry with a `file://` (or local path) URL can cause Desktop to clone or read local filesystem paths outside the intended repository, which is a "file read outside the repo" primitive per the stated valid-impact criteria.

### Likelihood Explanation
I confirmed the vulnerable sink (`-c protocol.file.allow=always` unconditionally re-enabling local-path submodule cloning) and its wiring into checkout/pull code paths [4](#0-3) . I was **not able to confirm** (due to running out of tool-call budget) the exact conditions under which `app-store.ts`/`dispatcher.ts` pass `allowFileProtocol=true` into `checkoutBranch`/`checkoutCommit`/`updateSubmodulesAfterOperation` — i.e., whether this only happens for the user's own trusted repos (in which case likelihood is low/non-issue) or also for untrusted forks/PR checkouts (in which case it is directly attacker-reachable). This is a material gap in my analysis.

### Recommendation
Trace every call site that sets `allowFileProtocol=true` (in `app-store.ts` and `dispatcher.ts`) and confirm it is never enabled for checkouts of refs/branches originating from a remote/fork that isn't already trusted by the user; if it can be reached from PR/fork checkout flows, gate it behind an explicit, per-operation user confirmation, or scope `protocol.file.allow` to only the specific known-safe origin instead of `always`.

### Proof of Concept
Not constructible with the tool access available — this would require verifying, in a running Desktop instance, that checking out an untrusted PR/fork branch with a malicious `.gitmodules` (e.g. `url = file:///home/victim/.ssh`) reaches `updateSubmodulesAfterOperation` with `allowFileProtocol=true`. Given the uncertainty in likelihood noted above, this finding should be treated as a candidate for further verification rather than a confirmed end-to-end exploit.

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
