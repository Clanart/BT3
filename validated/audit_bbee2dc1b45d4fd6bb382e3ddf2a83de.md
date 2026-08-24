Based on my research, I found a stronger, better-evidenced analog than initially expected, though I want to flag upfront that I could not fully trace every call site of `allowFileProtocol` in `app/src/ui/dispatcher/dispatcher.ts` and `app-store.ts` within available tool calls (they appeared in grep results but I ran out of iterations to read their full bodies). The finding below is based on the confirmed code in `checkout.ts` and `submodule.ts`.

### Title
Desktop force-enables `protocol.file.allow=always` for submodule updates, reviving the `file://` submodule local-file-disclosure vector (CVE-2022-39253 class) - (File: `app/src/lib/git/submodule.ts`)

### Summary
`updateSubmodulesAfterOperation` in `app/src/lib/git/submodule.ts` accepts an `allowFileProtocol` boolean and, when true, prepends `-c protocol.file.allow=always` to the `git submodule update --init --recursive` invocation [1](#0-0) . This flag is threaded in from `checkoutBranch`/`checkoutCommit` in `app/src/lib/git/checkout.ts`, which pass it straight through to the post-checkout submodule update after checking out a branch or commit [2](#0-1) [3](#0-2) .

Git 2.38 deliberately changed the default of `protocol.file.allow` to `user` specifically to stop *recursive*/automatic operations (like `submodule update --init --recursive` triggered by a `checkout` or `clone --recursive`) from silently cloning arbitrary local paths referenced via `file://` submodule URLs — this was the fix for the CVE-class local-file-disclosure bug where a malicious `.gitmodules` could point a submodule at a sensitive local directory and have Git copy its contents into the working tree. By forcing `protocol.file.allow=always`, Desktop's own code re-opens exactly that hole for any checkout path where `allowFileProtocol` is `true`.

### Finding Description
The broken invariant is: *"a value meant to gate a dangerous git config override is unconditionally forwarded down a call chain that ultimately reaches the git subprocess, overriding an upstream security default set specifically to block this attack."* This mirrors the reported bug class (a critical parameter/flag not being correctly gated as it's forwarded through nested calls, changing the security-relevant behavior of the underlying operation) — except here the flag ends up being forwarded when it should be withheld, rather than withheld when it should be forwarded.

Concretely:
- A malicious/compromised remote (or a collaborator's branch/PR fetched into the repo) can add or modify `.gitmodules` to point a submodule URL at `file:///path/to/sensitive/dir` (e.g., another local git repo, or a path under the user's home directory that happens to be a git repository).
- When Desktop checks out that branch/commit with `allowFileProtocol: true`, `checkoutBranch`/`checkoutCommit` call `updateSubmodulesAfterOperation`, which runs `git -c protocol.file.allow=always submodule update --init --recursive` [4](#0-3) .
- This bypasses Git's built-in `protocol.file.allow=user` safeguard, letting the submodule step clone the referenced local path directly into the working tree.
- The user can then unknowingly commit and push disclosed local content, or simply have it visible in the working directory / Explorer/Finder, achieving read of local file-system content outside the intended repo.

### Impact Explanation
If exploitable through a normal Desktop flow (checking out an untrusted branch/commit, e.g., from a fork or PR, with `allowFileProtocol=true`), this allows an attacker who only controls a fetched/cloned repository's tree content (via `.gitmodules`) to cause Desktop to read/copy local filesystem content into the user's working directory — matching the report's "attacker controls a cloned/fetched repository" impact class, potentially leading to silent corruption of what the user later commits/pushes (data exfiltration via push).

### Likelihood Explanation
Likelihood hinges entirely on when `allowFileProtocol` is `true` at the call sites in `dispatcher.ts`/`app-store.ts` — I was unable to fully verify these within the available tool budget (only found 6 matches in `dispatcher.ts` and 8 in `app-store.ts` via `grep_search`, but did not read their bodies). If `allowFileProtocol` is only ever `true` for repositories the app already fully trusts (e.g., ones the user explicitly added and where all submodule URLs were already resolved at add-time), the practical exploitability is much lower. This is the key open question a Devin session with full file access should confirm before treating this as a shippable finding.

### Recommendation
- Do not unconditionally force `protocol.file.allow=always` for the recursive submodule update following checkout; instead, rely on Git's default `user` policy or explicitly validate/allowlist submodule URLs (reject `file://`, `ext::`, and other local-execution schemes) before checkout of untrusted refs.
- Audit every caller of `checkoutBranch`/`checkoutCommit`/`updateSubmodulesAfterOperation` to confirm `allowFileProtocol` is only set `true` for repositories/branches the user has already vetted, not for freshly fetched remote branches or PR checkouts.

### Proof of Concept
1. Attacker prepares a repository/branch containing a `.gitmodules` file with a submodule entry: `url = file:///Users/victim/some-other-local-repo` (or any absolute path known/guessed to exist and be a git repo on the victim's machine).
2. Victim opens/fetches this branch or PR in GitHub Desktop and checks it out via a flow where `allowFileProtocol` is passed as `true` to `checkoutBranch`/`checkoutCommit`.
3. `updateSubmodulesAfterOperation` runs `git -c protocol.file.allow=always submodule update --init --recursive`, cloning the local path's content into the submodule directory inside the victim's working tree, bypassing Git's `protocol.file.allow=user` default protection.
4. Contents from the local path are now present in the repo's working directory and may be inadvertently committed/pushed by the victim. [5](#0-4) [6](#0-5)

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
