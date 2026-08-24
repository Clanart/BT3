### Title
Malicious `.gitmodules` `file://` submodule URLs are re-enabled during checkout, allowing arbitrary local file read/exfiltration outside the repo - ([File: app/src/lib/git/submodule.ts])

### Summary
`updateSubmodulesAfterOperation` in `app/src/lib/git/submodule.ts` accepts an `allowFileProtocol` flag that, when `true`, adds `-c protocol.file.allow=always` to the `git submodule update --init --recursive` invocation [1](#0-0) . This flag is threaded through `checkoutBranch` and `checkoutCommit` in `app/src/lib/git/checkout.ts`, which default it to `false` but let callers opt it back to `true` [2](#0-1) [3](#0-2) . Modern Git disables the `file://` transport for submodules by default (`protocol.file.allow=user`, i.e. blocked for automated/recursive clones) specifically to stop a malicious repository from declaring a submodule pointing at an arbitrary local path (e.g. `file:///home/user/.ssh`) and having Git silently copy that content into the working tree during `submodule update --init --recursive`. Desktop deliberately overrides that protection with `protocol.file.allow=always` in some checkout paths.

### Finding Description
The broken invariant is: *a repository/branch/commit fetched from an untrusted remote should never be able to make Desktop read files from outside the repository's own boundary during a routine checkout.* Git's own `protocol.file.allow=user` default is exactly the guard meant to enforce this, following the CVE-class of submodule `file://` local-file-disclosure bugs. Desktop's `checkoutBranch`/`checkoutCommit` accept a caller-supplied `allowFileProtocol` boolean that flows straight into `updateSubmodulesAfterOperation`, and when `true` it explicitly reinstates the unsafe `always` policy for that submodule update, overriding Git's own hardened default [4](#0-3) . Once this flag is set, if the target commit's `.gitmodules` contains a submodule URL such as `file:///etc/passwd` or a UNC/relative path pointing at sensitive user files, `git submodule update --init --recursive` will clone that local path directly into the submodule directory inside the working tree — content the attacker never had legitimate access to, now materialized as tracked files that Desktop will show as new/changed files ready to be committed and pushed.

### Impact Explanation
This matches the "silent corruption of what the user commits or pushes" and "file read... outside the repo" impact categories: an attacker who only controls a repository/branch/commit that the victim checks out (e.g. a malicious PR branch, a compromised fork, or a crafted `.gitmodules` in a forked repo added as a remote) can cause local files to be copied into the tracked working directory without the user's knowledge, where they may then be committed and pushed to a remote the attacker can read (exfiltration), or simply corrupt the state of what the user believes they are committing.

### Likelihood Explanation
Exploitability depends entirely on which UI flows pass `allowFileProtocol: true` into `checkoutBranch`/`checkoutCommit`; the default is `false`, so this is not exploitable through every checkout. Because full inspection of every call site was not completed (I could not exhaustively trace all callers such as `app/src/ui/checkout/confirm-checkout-commit.tsx` and `app/src/ui/stash-changes/stash-and-switch-branch-dialog.tsx` to determine under exactly what user action and repository state `allowFileProtocol` becomes `true`), the precise trigger conditions and how attacker-reachable they are from a purely "check out a branch from an untrusted source" action remain unverified. This is a real, code-visible weakening of Git's own file-protocol hardening, but confirming the end-to-end exploit path requires tracing those call sites further, which the current index/tool budget did not allow.

### Recommendation
Never allow `protocol.file.allow=always` to be applied to submodule operations triggered by content that originates from a remote/branch the user does not control the full provenance of. At minimum, restrict `allowFileProtocol=true` to explicitly local, user-initiated, non-remote scenarios, and default all submodule operations following a fetch/checkout of remote content to Git's `protocol.file.allow=user`/`deny` semantics. Ideally, warn the user and require explicit confirmation before initializing any submodule whose URL uses `file://`, `ext::`, or other non-`http(s)/ssh` schemes.

### Proof of Concept
1. Attacker creates a public repository (or a PR branch on a fork) whose `.gitmodules` contains:
   ```
   [submodule "leak"]
     path = leak
     url = file:///Users/victim/.ssh
   ```
2. Victim adds the attacker's fork as a remote in Desktop, fetches it, and checks out the malicious branch/commit through a Desktop flow that calls `checkoutBranch`/`checkoutCommit` with `allowFileProtocol: true` [5](#0-4) .
3. `updateSubmodulesAfterOperation` runs `git -c protocol.file.allow=always submodule update --init --recursive`, which clones the victim's local `~/.ssh` directory into the working tree's `leak/` folder [6](#0-5) .
4. The victim's private key material now appears as untracked/new files in Desktop's Changes view, ready to be committed and pushed, or otherwise disclosed.

### Citations

**File:** app/src/lib/git/submodule.ts (L36-54)
```typescript
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
