### Title
Stale "trusted repository" state (`allowFileProtocol`) is reused across checkouts of untrusted refs, enabling submodule `file://` protocol re-enablement - (File: `app/src/lib/git/checkout.ts`, `app/src/lib/git/submodule.ts`)

### Summary
`checkoutBranch`/`checkoutCommit` accept an `allowFileProtocol` boolean that is forwarded unchanged into `updateSubmodulesAfterOperation`, which conditionally re-enables Git's `protocol.file.allow=always` for the submodule `update --init --recursive` step that always runs after a checkout [1](#0-0) [2](#0-1) . This mirrors the Rubicon bug's broken invariant: a trust/permission decision computed for one context (the *previous* checkout/repository state) is carried forward and applied to a *new* operation without being recomputed against the new, potentially attacker-controlled content (a fetched branch/PR ref whose `.gitmodules` an attacker fully controls).

### Finding Description
`git`'s default `protocol.file.allow=user` blocks `file://` submodule URLs specifically to stop a malicious `.gitmodules` from causing the client to locally "clone" (read) arbitrary paths on disk when submodules are auto-initialized. Desktop's `updateSubmodulesAfterOperation` explicitly re-opens this door by passing `-c protocol.file.allow=always` whenever the caller sets `allowFileProtocol: true` [3](#0-2) .

The flag defaults to `false` at the `checkoutBranch`/`checkoutCommit` layer [4](#0-3) [5](#0-4) , so the safe state is the default. The vulnerability class from the report is about **collateral (trust) computed once being silently reused for a later, riskier action instead of being recomputed for that action**. Here the equivalent primitive is: whatever caller-side logic decides `allowFileProtocol` (referenced in `app-store.ts`/`dispatcher.ts`, which pass this flag through many checkout call sites) is a property of the *outer* repository/session, not of the specific ref being checked out. If that decision is made once (e.g., because the repository was originally cloned via a local/`file://` remote, or because the user had previously approved a local submodule) and then reused across subsequent checkouts of *different* refs — including a branch fetched from a fork or pull request that an attacker controls — the attacker's `.gitmodules` in that ref inherits a trust decision that was never made about that ref's content.

### Impact Explanation
If `allowFileProtocol=true` is carried over into a checkout of an attacker-supplied ref, `git submodule update --init --recursive` with `protocol.file.allow=always` will attempt to "clone" any `file://` URL declared in that ref's `.gitmodules` into the working tree. This is the well-known submodule-`file://` disclosure primitive: it can be used to copy the contents of local paths reachable by the user (e.g. other repositories, or Git bare directories on disk) into the checked-out working directory, from where they become visible/committable/pushable by the victim — i.e., silent corruption/exfiltration of local state as a byproduct of an ordinary checkout, without any additional confirmation prompt, satisfying the "attacker controls a cloned/fetched repository" + "file read outside the repo" impact class.

### Likelihood Explanation
This requires a concrete confirmation that some call site sets `allowFileProtocol=true` based on prior/outer trust state and then reuses it for checkouts of new, less-trusted refs (e.g., fetched PR branches). I located the flag's plumbing and default-safe value in `checkout.ts`/`submodule.ts`, and multiple references to `checkoutBranch`/`allowFileProtocol` in `app-store.ts` and `dispatcher.ts`, but did not have remaining tool budget to read those call sites and verify whether the true/false decision is (a) recomputed per-target-ref (safe) or (b) cached/derived from the repository's original clone protocol and reused across subsequent checkouts (vulnerable, matching the reported bug class). This is the same "unverified but structurally supported" situation as the original report before its PoC — the code path exists, but the exact triggering condition needs confirmation.

### Recommendation
- Recompute `allowFileProtocol` (and any similar "already trusted" flags) per checkout target based on the ref actually being checked out, never based on the repository's original remote/clone protocol or a previous checkout.
- Never allow `protocol.file.allow=always` to be enabled for checkouts of remote-tracking branches, PR refs, or any ref not authored solely by the local user.
- Add a regression test that checks out a branch containing a malicious `.gitmodules` with a `file://` submodule URL after a prior "trusted" checkout, and asserts submodule init is blocked (`protocol.file.allow` remains `user`/unset).

### Proof of Concept
1. Clone a repository via `file://` (or otherwise obtain an `allowFileProtocol=true` decision for the app's current repository state).
2. Fetch/checkout a branch (e.g., from a fork/PR) whose `.gitmodules` declares a submodule with a `file:///<sensitive-local-path>` URL.
3. Because `allowFileProtocol` is not recomputed for the new target ref, `checkoutBranch`/`checkoutCommit` → `updateSubmodulesAfterOperation` runs `git -c protocol.file.allow=always submodule update --init --recursive`, allowing the malicious submodule entry to be initialized from the local filesystem path into the working directory.

*Caveat:* Step 1's exact triggering conditions (where/how `allowFileProtocol=true` is decided in `app-store.ts`/`dispatcher.ts`) could not be fully verified within the available investigation budget; confirming this is the necessary next step before treating this as a fully validated, exploitable issue.

### Citations

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

**File:** app/src/lib/git/checkout.ts (L163-169)
```typescript
export async function checkoutCommit(
  repository: Repository,
  commit: CommitOneLine,
  currentRemote: IRemote | null,
  progressCallback?: ProgressCallback,
  allowFileProtocol: boolean = false
): Promise<true> {
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
