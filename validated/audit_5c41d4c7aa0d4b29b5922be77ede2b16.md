Based on the investigation, the most concrete, code-supported analog I can identify concerns Desktop's submodule-update logic re-enabling Git's `file://` protocol restriction, but I want to be explicit up front: I was **not able to fully confirm**, within my tool budget, that a production (non-test) call path passes `allowFileProtocol=true` when checking out an attacker-controlled branch/PR. I can only confirm the mechanism exists and is deliberately used with local/untrusted-style submodule URLs in test fixtures. I flag this uncertainty in the Likelihood section below rather than asserting full exploitability.

### Title
Submodule update can be run with `protocol.file.allow=always`, weakening Git's default block on `file://` submodule URLs - (File: app/src/lib/git/submodule.ts)

### Summary
`updateSubmodulesAfterOperation` accepts an `allowFileProtocol` flag that, when `true`, adds `-c protocol.file.allow=always` to `git submodule update --init --recursive` [1](#0-0) . This is exactly the git-side hardening (`protocol.file.allow`) that was introduced after CVE-2017-1000117 and later tightened by upstream Git to stop a submodule entry pointing at a `file://` URL from silently "cloning" an arbitrary local filesystem path into the working tree. `checkoutBranch` and `checkoutCommit` expose this same knob, defaulting to `false`, but callable with `true` [2](#0-1) .

### Finding Description
The invariant Git enforces by default is: submodule URLs using the `file://` transport must not be auto-cloned during `submodule update --init` unless the user explicitly opts in, because a repository's `.gitmodules` is fully attacker-controlled content (just like a smart contract's incoming call data). A malicious repository (cloned, forked, or supplied via a PR branch) can declare a submodule whose `url` is `file:///Users/victim/.ssh` (or any other locally readable path). If `protocol.file.allow=always` is forced during `submodule update --init --recursive`, Git will "clone" that local directory into the submodule's target path inside the repository's working tree.

The equivalent of the Sushi bug's "should check state hasn't already been polluted before reuse" is here: before flipping on `protocol.file.allow=always`, Desktop needs to verify the repository/submodule URLs are safe (e.g., only allow it for submodules that were added by Desktop itself using a local test path, not for arbitrary fetched/attacker content). The test helper `setupRepositoryWithUninitializedSubmodule` demonstrates the exact attack primitive: adding a submodule whose URL is a local filesystem path only works because `protocol.file.allow=always` is forced [3](#0-2) .

### Impact Explanation
If this flag is reachable from a code path that processes attacker-supplied content (e.g., checking out a branch/PR from a forked or fetched remote whose `.gitmodules` was crafted by the attacker), the result would be: contents of an arbitrary local directory (SSH keys, cloud credential files, etc.) get materialized into the repository's working tree as a "submodule." A user who then reviews/commits/pushes changes in Desktop could unintentionally exfiltrate those files to the attacker's remote, or the "submodule" clone itself may run local hooks, unlike a network-fetched submodule.

### Likelihood Explanation
Confirmed: the flag exists in shipped, non-test code (`app/src/lib/git/submodule.ts`, `app/src/lib/git/checkout.ts`), it defaults to `false`, and its true purpose (based on the docstring "Whether to allow file:// protocol for submodules") is specifically to bypass Git's protective default [4](#0-3) .

**Not confirmed**: whether any UI/dispatcher/app-store flow that checks out attacker-influenced content (e.g., `_checkoutPullRequest` → `_checkoutBranch` → `checkoutImplementation` in `app/src/lib/stores/app-store.ts`) ever passes `allowFileProtocol=true`. My searches only found `allowFileProtocol=true` used from a unit-test helper (`setupRepositoryWithUninitializedSubmodule`) to set up local fixture repos with local-path submodules — a legitimate, non-attacker-reachable use. I ran out of tool budget before I could inspect `checkoutImplementation` and every call site of `_checkoutBranch`/`_checkoutCommit` in `app-store.ts` to see whether `true` is ever passed for a general/attacker-controlled repository (as opposed to only for Desktop's own known-safe local operations).

### Recommendation
This needs verification before treating it as a confirmed, shippable finding:
1. Enumerate every call site that invokes `checkoutBranch`/`checkoutCommit` with `allowFileProtocol=true`, and confirm whether any of them are reachable from checking out a fetched/forked/PR branch, a cloned repository, or any other attacker-influenced ref.
2. If such a path exists, either remove the ability to force `protocol.file.allow=always` for non-Desktop-initiated repositories, or scope it strictly to Desktop's own internal/test-only local submodule flows.
3. If no such path exists, this should be downgraded to a "defense-in-depth" hardening suggestion rather than a vulnerability.

### Proof of Concept
Not able to construct a full end-to-end PoC without confirming a production attacker-reachable call site. The building blocks that would be needed for a PoC (and that are confirmed to work in this codebase) are:
1. A malicious repository with `.gitmodules` containing a submodule entry `url = file:///path/to/victim/sensitive-directory`.
2. Desktop invoking `updateSubmodulesAfterOperation(..., allowFileProtocol=true)` while processing that repository (checkout of a branch/PR/clone) — confirmed mechanism in `app/src/lib/git/submodule.ts:45-51`, but the attacker-reachable trigger for `true` was not located.

Given the confirmed gap in verification, I'm presenting this as a **candidate** finding requiring a follow-up code review (ideally via a full Devin session with complete file access) of `app/src/lib/stores/app-store.ts`'s `checkoutImplementation` and all `_checkoutBranch`/`_checkoutCommit`/`_checkoutPullRequest` call chains, rather than a fully confirmed vulnerability report.

### Citations

**File:** app/src/lib/git/submodule.ts (L27-27)
```typescript
 * @param allowFileProtocol - Whether to allow file:// protocol for submodules
```

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

**File:** app/test/helpers/repositories.ts (L296-309)
```typescript
  // Create a new branch and add the submodule
  await exec(['checkout', '-b', 'branch-with-submodule'], repo.path)

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
