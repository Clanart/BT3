Based on the investigation, I found a structurally similar pattern in GitHub Desktop's git wrapper: an "acceptable"/expected error class causes the operation to be treated as successful, silently skipping the caller's normal error handling and downstream integrity checks — the same broken invariant as the Berachain report (`ErrAcceptedPayloadStatus` causing `Transition` to short-circuit and skip RANDAO/withdrawals/state-root checks).

### Title
Authentication failures during submodule/branch checkout are silently treated as success, causing Desktop to report a fully checked-out working tree that is actually incomplete/mismatched - (File: `app/src/lib/git/checkout.ts`, `app/src/lib/git/submodule.ts`, `app/src/lib/git/core.ts`)

### Summary
GitHub Desktop's low-level `git()` wrapper has an `expectedErrors` mechanism: if the parsed `DugiteError` is in the caller-supplied `expectedErrors` set, `git()` returns the result object normally instead of throwing [1](#0-0) . `checkoutBranch`, `checkoutCommit`, and `updateSubmodulesAfterOperation` all pass `AuthenticationErrors` as `expectedErrors` [2](#0-1) [3](#0-2) , but none of these callers inspect `result.gitError` afterward — they unconditionally proceed to the next step and ultimately return `true` for a successful checkout [4](#0-3) .

### Finding Description
The call chain `checkoutBranch → git(..., {expectedErrors: AuthenticationErrors}) → updateSubmodulesAfterOperation → git(..., {expectedErrors: AuthenticationErrors})` mirrors the Berachain pattern exactly: a specific "acceptable" error is defined at a low level (`git()`'s `expectedErrors` filter, analogous to `ErrAcceptedPayloadStatus` handling in `verifyStateRoot`), and when it fires, the function returns as if nothing went wrong instead of surfacing a failure that the caller must react to. In `checkoutBranch`, if the `git checkout` command itself hits an auth error (e.g. because the branch checkout triggers a submodule/LFS smudge filter fetch, or the checkout is combined with a remote update) the error is swallowed at the `git()` level, and the function still moves on to `updateSubmodulesAfterOperation` and finally returns `true` [5](#0-4) . Similarly, when a malicious repository defines a submodule whose URL requires authentication that fails or is rejected, `updateSubmodulesAfterOperation`'s `git submodule update --init --recursive` will exit with an auth-related `DugiteError`, which is in `AuthenticationErrors` and therefore accepted silently — no exception propagates, no partial-checkout flag is set, and the caller (`checkoutBranch`/`checkoutCommit`) still returns `true` [6](#0-5) .

### Impact Explanation
This lets an attacker who controls a cloned/fetched repository (e.g., via a submodule pointing to an attacker-controlled or intentionally-failing authenticated remote) cause Desktop to present a branch/commit checkout as fully successful while the working tree is actually incomplete or in a stale/mismatched state (old submodule commit, missing submodule content, or partially applied filters). A user who then stages and commits changes on top of this silently-incomplete tree can push a corrupted commit (e.g., missing submodule updates, or content diverging from what they believe they checked out) — matching the "silent corruption of what the user commits or pushes" impact category.

### Likelihood Explanation
Reaching this path only requires the victim to open/checkout a branch/commit in a repository containing a submodule whose remote requires (and then fails) authentication — a state fully controllable by whoever controls the `.gitmodules` file (i.e., anyone with a fork/PR/clone that gets checked out in Desktop). No admin rights, local access, or pre-existing malware are needed; it's reachable purely through normal "check out this branch/PR" UI flows.

### Recommendation
After each `git()` call whose `expectedErrors` includes `AuthenticationErrors`, `checkoutBranch`, `checkoutCommit`, and `updateSubmodulesAfterOperation` should inspect the returned `gitError` and, if set, propagate a distinguishable "checkout partially failed" result/error to the caller instead of silently continuing and returning `true`. The UI layer should then surface this as a failed/partial checkout rather than a success.

### Proof of Concept
1. Create a repository with a `.gitmodules` entry pointing a submodule at a URL requiring credentials that will fail (e.g., a private HTTPS URL with no valid credential helper entry, or a URL that returns `401`).
2. Have the victim clone/fetch this repository in GitHub Desktop and check out the branch containing this `.gitmodules` state via `checkoutBranch`.
3. Observe: `git submodule update --init --recursive` fails with `HTTPSAuthenticationFailed` (in `AuthenticationErrors`), `git()` returns normally with `gitError` set, `updateSubmodulesAfterOperation` returns without throwing, and `checkoutBranch` returns `true` — Desktop reports the checkout as successful even though the submodule was never updated.
4. The user, seeing no error, commits/pushes on top of this state, resulting in a commit that silently omits or diverges from the expected submodule content. [7](#0-6)

### Citations

**File:** app/src/lib/git/core.ts (L322-353)
```typescript
          const exitCode = result.exitCode

          let gitError: DugiteError | null = null
          const acceptableExitCode = opts.successExitCodes
            ? opts.successExitCodes.has(exitCode)
            : false
          if (!acceptableExitCode) {
            gitError = parseError(coerceToString(result.stderr))
            if (gitError === null) {
              gitError = parseError(coerceToString(result.stdout))
            }
          }

          const gitErrorDescription =
            gitError !== null
              ? getDescriptionForError(gitError, coerceToString(result.stderr))
              : null
          const gitResult = {
            ...result,
            gitError,
            gitErrorDescription,
            path,
          }

          let acceptableError = true
          if (gitError !== null && opts.expectedErrors) {
            acceptableError = opts.expectedErrors.has(gitError)
          }

          if ((gitError !== null && acceptableError) || acceptableExitCode) {
            return gitResult
          }
```

**File:** app/src/lib/git/checkout.ts (L46-51)
```typescript
  const opts: IGitStringExecutionOptions = {
    env: await envForRemoteOperation(
      getFallbackUrlForProxyResolve(repository, currentRemote)
    ),
    expectedErrors: AuthenticationErrors,
  }
```

**File:** app/src/lib/git/checkout.ts (L121-146)
```typescript
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

  // we return `true` here so `GitStore.performFailableGitOperation`
  // will return _something_ differentiable from `undefined` if this succeeds
  return true
}
```

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
