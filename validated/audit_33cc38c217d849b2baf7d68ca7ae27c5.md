## Analysis

The core pattern in the report is: **a security-relevant restriction is enforced on one operation but explicitly disabled/skipped on a related operation that touches the same untrusted input**, letting an attacker choose the path that lacks the guard. Searching Desktop's git submodule/clone handling for a similar “guard applied on one path, disabled on another” shows exactly this pattern in the recursive-clone flow versus the checkout submodule-update flow.

- `clone()` unconditionally disables Git's own submodule-clone protection by setting `GIT_CLONE_PROTECTION_ACTIVE: 'false'` in the environment for **every** `git clone --recursive` invocation, regardless of the source of the URL: [1](#0-0) 
- By contrast, `checkoutBranch()`/`checkoutCommit()` treat file-protocol/submodule handling as something to be explicitly gated behind an `allowFileProtocol` parameter (default `false`) that is threaded into `updateSubmodulesAfterOperation()`, which only appends `-c protocol.file.allow=always` when explicitly told to: [2](#0-1) [3](#0-2) 
- `pull()` follows yet a third behavior: it runs `git pull --recurse-submodules` directly, with no call to `updateSubmodulesAfterOperation` and no explicit protocol control at all: [4](#0-3) 

`GIT_CLONE_PROTECTION_ACTIVE` is the internal Git environment variable introduced as part of the fix for the recursive-clone hardlink/local-clone vulnerability class (the class covered by the Git security advisories around GHSA-related CVEs on `clone --recursive` handling of malicious submodule configurations). Desktop's `clone.ts` disables this protection for every clone, while a sibling code path (`checkout.ts`) treats the equivalent submodule protocol risk as something that must be explicitly opted into. This is the same shape of bug as the audit report: a fee/guard applied consistently in one function (`openPosition`) but omitted in a related function that manipulates the same state (`addPremium`), letting the attacker pick the unguarded path.

I could not fully verify, within the indexed code, what upstream logic (if any) decides the `allowFileProtocol` value passed into `checkoutBranch`/`checkoutCommit` from `app-store.ts`, nor confirm the exact current Git version's default behavior for `protocol.file.allow` during `--recurse-submodules` clone/pull in this Electron-bundled Git. Given the ask-only mode and the residual uncertainty about whether this divergence is currently exploitable end-to-end (i.e., whether Git's own default `protocol.file.allow=user` still blocks a *newly-introduced* `file://` submodule URL even with `GIT_CLONE_PROTECTION_ACTIVE=false`), I do not have enough conclusive evidence from local code alone to assert a fully exploitable file-read/write primitive with certainty.

### Title
Recursive clone unconditionally disables Git's clone-protection guard while checkout gates the same risk behind an explicit flag - (File: app/src/lib/git/clone.ts)

### Summary
`clone()` sets `GIT_CLONE_PROTECTION_ACTIVE: 'false'` for every `git clone --recursive` call regardless of trust in the remote URL, while the sibling submodule-update code in `checkout.ts`/`submodule.ts` treats the analogous file-protocol risk as opt-in via an `allowFileProtocol` flag, and `pull.ts` has no equivalent control at all.

### Finding Description
`clone()` builds its execution environment as: [5](#0-4) 
This disables the internal Git safeguard meant to prevent local-clone/hardlink-style abuse during a recursive submodule clone, for every clone Desktop performs — including clones of arbitrary, attacker-controlled remote URLs entered by the user or reached via a deep link/API browse flow.

By contrast, when Desktop updates submodules from `checkoutBranch`/`checkoutCommit`, the file-protocol allowance is threaded explicitly as a boolean parameter that defaults to `false` and is only turned on by callers that decide it's safe: [2](#0-1) [6](#0-5) 

`pull()` is a third, inconsistent path: it invokes `git pull --recurse-submodules` directly with no call into `updateSubmodulesAfterOperation` and no protection controls at all: [4](#0-3) 

This mirrors the audit finding's structure: the same conceptual guard (protection against a malicious/crafted submodule configuration) is enforced deliberately and conditionally in one operation (`checkout`) but is bypassed entirely (`clone`, via explicit disable) or omitted (`pull`, via no control) in others that process the same untrusted repository content.

### Impact Explanation
If `GIT_CLONE_PROTECTION_ACTIVE=false` meaningfully weakens Git's defenses against maliciously crafted recursive-submodule repositories (the class of issue this variable was introduced for), then simply cloning an attacker-authored repository through Desktop's normal clone flow — the most common, least suspicious user action — would be the *least* protected path, while a more cautious, opt-in path (`checkout`) is the one that actually gates the risk behind a flag. This inverts the expected security posture: the highest-exposure operation (first-time recursive clone of an untrusted repo) has the guard turned off unconditionally.

### Likelihood Explanation
Cloning attacker-controlled repositories (via URL paste, GitHub search results, or deep links) is a core, everyday Desktop workflow, so the unguarded path is reached with no unusual user action. This satisfies the "attacker controls a cloned/fetched repository" primitive in the valid-impact scope.

### Recommendation
Audit why `GIT_CLONE_PROTECTION_ACTIVE` is unconditionally disabled in `clone.ts` and confirm whether this reintroduces the class of local-clone/submodule risk the flag was designed to prevent, particularly for user-supplied/generic clone URLs. Align the submodule/file-protocol protection posture between `clone.ts`, `checkout.ts`, and `pull.ts` so that the same trust decision (e.g., "is this a known/trusted host") governs whether file-protocol/clone protections are relaxed across all three operations, rather than having each function make an independent, inconsistent decision.

### Proof of Concept
Not independently verified end-to-end (would require confirming current Git's exact default enforcement of `protocol.file.allow` during `clone --recursive` with `GIT_CLONE_PROTECTION_ACTIVE=false` versus `checkout`'s explicit `-c protocol.file.allow=always` gating). Conceptually: an attacker publishes a repository with a submodule configuration crafted to exploit the recursive-clone protection that `GIT_CLONE_PROTECTION_ACTIVE` guards against; a victim clones this repository via Desktop's `clone()` (which disables the guard unconditionally), while the same repository, if instead reached via `checkoutBranch`/`checkoutCommit` with `allowFileProtocol=false` (the default), would have the guard intact — demonstrating the inconsistency in code but not confirmed as independently exploitable from local evidence alone.

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

**File:** app/src/lib/git/pull.ts (L96-106)
```typescript
  const args = [
    ...gitRebaseArguments(),
    'pull',
    ...(await getDefaultPullDivergentBranchArguments(repository)),
    '--recurse-submodules',
    ...(options?.progressCallback ? ['--progress'] : []),
    ...(options?.noVerify ? ['--no-verify'] : []),
    remote.name,
  ]

  await git(args, repository.path, 'pull', opts)
```
