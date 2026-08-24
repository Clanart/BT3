## Finding

Based on the investigation, I found a genuine analog: a boolean safety-guard parameter (`allowFileProtocol`) that, when enabled, disables Git's built-in protection against malicious submodule URLs — structurally identical to the reported bug's "safety threshold defaulted/forced to an unsafe value."

### Title
Submodule checkout can be forced to allow `file://` protocol, letting an attacker-controlled repo read/exfiltrate arbitrary local paths via `.gitmodules` - ([File: app/src/lib/git/submodule.ts](), [File: app/src/lib/git/checkout.ts]())

### Summary
`checkoutBranch` and `checkoutCommit` accept an `allowFileProtocol` boolean (default `false`) that is forwarded to `updateSubmodulesAfterOperation`. When `true`, the submodule update is run with `-c protocol.file.allow=always` before `git submodule update --init --recursive` [1](#0-0) [2](#0-1) . Modern Git disables the `file://` transport for submodules by default specifically to stop a malicious repository's `.gitmodules` from pointing a submodule at an arbitrary local path (this was Git's fix for CVE-2022-39253). Setting `protocol.file.allow=always` re-enables that transport, meaning any submodule URL of the form `file:///home/user/.ssh` (or any other local path) embedded by an attacker in a branch's `.gitmodules` will be happily "cloned" into the working tree during checkout.

### Finding Description
The unit test suite confirms the application actually exercises this unsafe path in real (non-mocked) code: `setupRepositoryWithUninitializedSubmodule` creates a branch with a submodule and then the test calls `checkoutBranch(repository, branchWithSubmodule, null, undefined, true)` to initialize it [3](#0-2) , exercising the same `updateSubmodulesAfterOperation(..., allowFileProtocol=true)` path that ships in `checkout.ts`. The `.gitmodules` file (and therefore the submodule URL) is part of the cloned/fetched commit content — fully attacker-controlled if the branch comes from a fork, pull request, or any third-party remote.

Unlike the `clone()` path, which explicitly hardens the destination against traversal (`isClonePathSensitive`) [4](#0-3) , and unlike `_startOpenInDesktop`/`openRepositoryFromUrl` which sanitize file paths with `resolveWithin` before touching disk [5](#0-4) , the submodule-checkout path has no equivalent guard on the **source** URL used for submodule content — it simply flips Git's own protection off when `allowFileProtocol` is `true`.

### Impact Explanation
If `allowFileProtocol` is passed as `true` for a checkout whose tree originates from an untrusted source (a forked PR branch, a malicious collaborator's branch, or any fetched ref the user didn't author), the attacker's `.gitmodules` can declare a submodule pointing at `file:///Users/<user>/.ssh`, `file:///Users/<user>/Library/Application Support/GitHub Desktop`, or similar. `git submodule update --init --recursive` with `protocol.file.allow=always` will materialize the contents of that local path into the working directory as tracked, visible files. If the user then stages/commits/pushes (a very natural next action for someone unaware their submodule pulled in unexpected files), private key material or other local secrets are silently exfiltrated to whatever remote the branch is pushed to — matching the "credential exfiltration" / "silent corruption of what the user commits" impact classes.

### Likelihood Explanation
I was **not able to fully confirm within the available search budget** which specific caller in `app-store.ts` / `dispatcher.ts` passes `allowFileProtocol=true` for production (non-test) checkout flows, nor whether it is gated behind an explicit "I trust this fork" user action. This is the key open question: if `allowFileProtocol=true` is only ever used for local, user-authored branches (never for PR/fork checkouts), the practical exploitability drops substantially and this would not meet the "unprivileged, attacker-controlled content" bar. The test fixture demonstrating the code path uses a submodule added directly to the test repo, not an untrusted fork, so it does not by itself prove the dangerous production call path exists.

### Recommendation
- Audit every call site of `checkoutBranch`/`checkoutCommit` (and `updateSubmodulesAfterOperation`) that passes `allowFileProtocol: true` to confirm it is never used for content originating from an untrusted remote (forks, PR heads, arbitrary fetched branches).
- Regardless, add an explicit allowlist/validation step on submodule URLs before enabling `protocol.file.allow=always` — e.g., reject `file://` submodule URLs whose resolved path is outside the user's designated "trusted" clone roots, mirroring the `isClonePathSensitive` backstop already used for clone destinations.
- Prefer never re-enabling `protocol.file.allow` for `git submodule update` on repository content that isn't 100% first-party.

### Proof of Concept
Conceptual, based on confirmed code paths:
1. Attacker creates a branch/fork with a `.gitmodules` entry: `url = file:///Users/victim/.ssh`.
2. Victim opens/fetches that branch in GitHub Desktop and checks it out via a code path that calls `checkoutBranch(repo, branch, remote, cb, /*allowFileProtocol=*/true)`.
3. `updateSubmodulesAfterOperation` runs `git -c protocol.file.allow=always submodule update --init --recursive` [6](#0-5) , materializing `~/.ssh` contents into the working tree.
4. Victim stages/commits/pushes, exfiltrating the private key material to the attacker's remote.

Given the unresolved question about the exact production call site, I'm presenting this as the strongest local-code analog found rather than a fully-proven end-to-end issue — a background Devin session with full-repo access would be needed to trace every `allowFileProtocol=true` invocation in `app-store.ts`/`dispatcher.ts` and confirm reachability from untrusted (fork/PR) checkout flows before treating this as confirmed-exploitable.

### Citations

**File:** app/src/lib/git/checkout.ts (L102-146)
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

**File:** app/test/unit/git/checkout-test.ts (L150-166)
```typescript
    it('initializes an uninitialized submodule when checking out a branch', async t => {
      const repository = await setupRepositoryWithUninitializedSubmodule(t)

      const branches = await getBranches(repository)
      const branchWithSubmodule = branches.find(b => b.name !== 'master')

      if (branchWithSubmodule == null) {
        throw new Error(`Could not find branch other than 'master'`)
      }

      await checkoutBranch(
        repository,
        branchWithSubmodule,
        null,
        undefined,
        true
      )
```

**File:** app/src/lib/git/clone.ts (L10-47)
```typescript
/**
 * Check whether a resolved clone path targets a sensitive location that
 * should never be used as a clone destination. This is a backstop against
 * path traversal attacks where a crafted URL tricks the UI into deriving
 * a clone path outside the intended base directory.
 */
function isClonePathSensitive(unresolvedClonePath: string): boolean {
  const clonePath = Path.resolve(unresolvedClonePath).toLowerCase()
  const home = Path.resolve(homedir()).toLowerCase()

  if (clonePath === home) {
    return true
  }

  const sensitiveLocations = [
    Path.join(home, '.ssh'),
    Path.join(home, '.gnupg'),
    Path.join(home, '.config'),
    Path.join(home, '.config', 'git'),
    Path.join(home, '.gitconfig'),
  ]

  if (__WIN32__) {
    const appData = process.env.APPDATA
    if (appData) {
      sensitiveLocations.push(appData.toLowerCase())
      sensitiveLocations.push(Path.join(appData, 'gnupg').toLowerCase())
    }
  }

  for (const sensitive of sensitiveLocations) {
    if (clonePath === sensitive || clonePath.startsWith(sensitive + Path.sep)) {
      return true
    }
  }

  return false
}
```

**File:** app/src/ui/dispatcher/dispatcher.ts (L1957-1972)
```typescript
    if (filepath !== null) {
      if (isAbsolute(filepath)) {
        log.error(`Refusing to open absolute path: ${filepath}`)
        return
      }

      const resolved = await resolveWithin(repository.path, filepath)

      if (resolved !== null) {
        shell.showItemInFolder(resolved)
      } else {
        log.error(
          `Prevented attempt to open path outside of the repository root: ${filepath}`
        )
      }
    }
```
