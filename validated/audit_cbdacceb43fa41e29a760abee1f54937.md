Based on the evidence gathered, I found a plausible analog but could not fully verify the exact trust conditions under which `allowFileProtocol` is invoked as `true` in `app-store.ts` (search matched 8 occurrences there, but I ran out of iterations to read the surrounding logic). I'll present the strongest supported finding while being explicit about that gap.

### Title
Unvalidated `allowFileProtocol` flag re-enables `file://` submodule URLs for attacker-controlled repositories - (File: app/src/lib/git/submodule.ts)

### Summary
`updateSubmodulesAfterOperation` in [1](#0-0)  conditionally injects `-c protocol.file.allow=always` before running `git submodule update --init --recursive`. This config override disables Git's own hardening (the `protocol.file.allow=user` default introduced after CVE-2022-39253) that normally blocks submodules from being fetched over `file://` URLs. `checkoutBranch` and `checkoutCommit` in [2](#0-1)  and [3](#0-2)  both accept an `allowFileProtocol: boolean = false` parameter that is passed straight through to the submodule update call with no validation tying it to whether the repository/remote being operated on is actually a trusted local path.

### Finding Description
Git added `protocol.file.allow=user` as a default specifically to stop a cloned repository's `.gitmodules` from pointing a submodule at an arbitrary local `file://` path, which could otherwise be used to read/copy files from elsewhere on disk into the working tree during `submodule update --init --recursive`. Desktop's own test fixtures acknowledge this directly: [4](#0-3)  comments "Git 2.38 ... changed the default here to 'user'" and works around it with `protocol.file.allow=always`.

The same override is wired into production code paths (`checkoutBranch`, `checkoutCommit`, and by extension `pull`/`fetch`-triggered submodule updates) via a plain boolean flag, rather than being scoped to a specific, verified-safe local clone operation. Because the flag flows from UI call sites into `updateSubmodulesAfterOperation` without any check on the submodule URLs recorded in `.gitmodules`, an attacker who controls a cloned/fetched repository's `.gitmodules` content can add a submodule entry with a `file://` (or bare local path) URL. If any code path calls `checkoutBranch`/`checkoutCommit` with `allowFileProtocol = true` for that repository, Desktop will happily run `submodule update --init --recursive` with `protocol.file.allow=always`, following the attacker-chosen local path and copying its contents into the submodule's working directory inside the repo — a file read that a malicious repo maintainer controls, occurring silently as part of an ordinary checkout/pull.

### Impact Explanation
If reachable with `allowFileProtocol=true` for a repository whose `.gitmodules` is attacker-supplied (e.g., a forked/cloned repo, or a repo pulled from an untrusted remote), this bypasses Git's own file-protocol submodule protection and can result in local files being read into the working directory (potential path/data exposure), which could then be committed/pushed by the unsuspecting user — directly matching the "file read outside the repo" / "silent corruption of what the user commits" impact categories.

### Likelihood Explanation
I could not confirm within the available tool budget whether any call site in [5](#0-4)  passes `allowFileProtocol = true` unconditionally versus only for verified local-clone scenarios (e.g., the `setupRepositoryWithUninitializedSubmodule` test helper suggests the flag is meant for legitimate local test/dev flows). This is the key open question that determines real-world exploitability: if the flag is only ever set `true` for repositories the user explicitly created/trusts locally, the attacker-controlled path does not apply and this finding would not qualify. Given this uncertainty, likelihood is assessed as **unconfirmed/moderate** — the mechanism (unguarded protocol-allow override tied only to a boolean, not to actual submodule URL/remote trust) is present and unsafe by construction, but I cannot certify from the indexed code alone that a fully unprivileged attacker-controlled repository can reach the `true` branch.

### Recommendation
- Do not gate `protocol.file.allow=always` on a simple boolean threaded from UI call sites; instead validate/allow-list submodule URLs (reject `file://`/bare local paths) before ever overriding Git's protocol protection.
- If `allowFileProtocol=true` is only intended for internal/local-clone workflows, enforce that invariant in code (e.g., assert the repository/remote is same-origin/local before setting the flag) rather than trusting callers.
- Audit every call site in `app-store.ts`/`dispatcher.ts` that passes `allowFileProtocol` to confirm none of them apply it to repositories fetched from arbitrary/untrusted remotes.

### Proof of Concept
Not fully constructible from indexed code alone, since the deciding factor (which call sites pass `true`) was not verified. Conceptually: an attacker crafts a repository with `.gitmodules` containing `url = file:///etc` (or a sensitive local path) for a submodule, gets a victim to clone/fetch and checkout/pull it through a code path that sets `allowFileProtocol=true`, and `git -c protocol.file.allow=always submodule update --init --recursive` copies the target path's contents into the repository's working tree.

**Note on limitations:** Due to index size limits, I could not read the full logic in `app-store.ts` (8 matches for `checkoutBranch(`/`checkoutCommit(`/`allowFileProtocol`) that determines under what conditions `allowFileProtocol` is set to `true`. To confirm or refute this finding with certainty, a Devin session with full repository access should inspect those call sites directly.

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

**File:** app/src/lib/git/checkout.ts (L163-207)
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

  // we return `true` here so `GitStore.performFailableGitOperation`
  // will return _something_ differentiable from `undefined` if this succeeds
  return true
}
```

**File:** app/test/unit/git/rev-parse-test.ts (L65-70)
```typescript
      await git(
        [
          // Git 2.38 (backported into 2.35.5) changed the default here to 'user'
          ...['-c', 'protocol.file.allow=always'],
          ...['submodule', 'add', '../repo2'],
        ],
```

**File:** app/src/lib/stores/app-store.ts (L1-1)
```typescript
import * as Path from 'path'
```
