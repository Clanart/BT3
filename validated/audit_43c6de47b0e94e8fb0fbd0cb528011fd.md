### Title
`updateSubmodulesAfterOperation` allows `protocol.file.allow=always` to be attacker-gated via a caller-controlled `allowFileProtocol` flag, enabling local file read via a malicious `.gitmodules` `file://` URL - (File: `app/src/lib/git/submodule.ts`)

### Summary
`updateSubmodulesAfterOperation` in `app/src/lib/git/submodule.ts` accepts an `allowFileProtocol` boolean and, when `true`, prepends `-c protocol.file.allow=always` to the `git submodule update --init --recursive` invocation. [1](#0-0) 
This flag is threaded through from `checkoutBranch`/`checkoutCommit` in `app/src/lib/git/checkout.ts`, both of which default the parameter to `false` but expose it as a caller-supplied argument that upstream callers (e.g. branch/commit checkout after cloning or pulling an untrusted repository) can set to `true`. [2](#0-1) [3](#0-2) 

### Finding Description
Git upstream disabled `file://` submodule URLs by default (`protocol.file.allow=user`, effectively blocked for automatic recursive operations) specifically to close a class of vulnerabilities (tracked historically as CVE-2022-39253) where a malicious repository's `.gitmodules` file points a submodule at a `file://` path on the victim's local filesystem (e.g. `file:///home/victim/.ssh` or a sibling directory). When such a submodule is "cloned," git will copy the target directory's contents into the submodule's working tree inside the repository, effectively exfiltrating local file contents into tracked, committable files.

Desktop's `updateSubmodulesAfterOperation` explicitly overrides this protection by conditionally injecting `-c protocol.file.allow=always` before `submodule update --init --recursive`: [4](#0-3) 
This is invoked after every branch/commit checkout via `checkoutBranch`/`checkoutCommit`. [5](#0-4) 

The corrupted invariant is: "submodule URLs from an untrusted/attacker-supplied repository must never be resolved with `file://` semantics without explicit, informed user consent." Because `allowFileProtocol` is a plain boolean parameter threaded from checkout call sites rather than a value derived from a trust/consent decision recorded per-repository, any code path that calls `checkoutBranch`/`checkoutCommit` with `allowFileProtocol=true` (e.g., to support legitimate local-submodule development workflows) will apply that same permissive setting uniformly, including for freshly cloned or fetched repositories whose `.gitmodules` content is entirely attacker-controlled. Git's own default hardening (`protocol.file.allow=user`) is the guard this bypasses, and Desktop's override removes it unconditionally whenever the flag is set to `true`, with no check against `.gitmodules` submodule URL schemes or repository provenance/trust state (contrast with the existing "unsafe repository ownership" trust gate used elsewhere in the app for a related but different threat, `app/src/ui/add-repository/add-existing-repository.tsx:129-174`, and `app/src/ui/missing-repository.tsx:62-135`, which shows Desktop already models this class of "untrusted repo triggers automatic execution/read" risk but does not appear to gate the submodule file-protocol case the same way). [6](#0-5) 

### Impact Explanation
If a caller path sets `allowFileProtocol=true` for a checkout following a clone/fetch of a repository the user does not control (e.g., a repo cloned or opened from a URL, or a PR/branch fetched from a fork), a `.gitmodules` entry using `url = file:///<sensitive-local-path>` would cause `git submodule update --init --recursive` to copy the contents of that local path into the submodule's working directory inside the cloned repository. Those copied files then become part of the user's working tree and, if staged/committed and pushed, are exfiltrated to whatever remote the attacker controls. This matches the report's required impact class: "attacker controls a cloned/fetched repository ... resulting in ... file write/read outside the repo ... or silent corruption of what the user commits or pushes."

### Likelihood Explanation
Exploitability depends on whether any reachable call site actually passes `allowFileProtocol=true` for a checkout that follows cloning/fetching attacker-supplied content without a corresponding trust/consent check — this reachable path was not confirmed within the indexed code (the parameter defaults to `false`, and I could not locate, within the available index, the specific call site(s) that set it to `true`). This is the key uncertainty: without confirming a concrete caller that flips the flag for untrusted content, this should be treated as a latent design risk rather than a fully proven end-to-end exploit chain in this codebase snapshot.

### Recommendation
- Never allow `protocol.file.allow=always` to be enabled implicitly for submodule operations following a clone/fetch of a repository whose trust has not been explicitly established (reuse the existing "unsafe repository" trust model already present for directory ownership).
- Before calling `updateSubmodulesAfterOperation` with `allowFileProtocol=true`, inspect `.gitmodules` for `file://` URLs and require explicit user confirmation, or resolve `file://` submodule URLs only when they point inside the already-trusted repository ancestry.
- Prefer leaving `protocol.file.allow` at git's default (`user`) for any submodule update triggered by checkout of remote-originated refs, and only relax it for verified local development scenarios.

### Proof of Concept
Conceptual PoC (end-to-end reachability from an untrusted clone was not confirmed in this pass; the mechanism below is confirmed at the `updateSubmodulesAfterOperation` layer):
1. Attacker crafts a repository with a `.gitmodules` file:
   ```
   [submodule "leak"]
     path = leak
     url = file:///Users/victim/.ssh
   ```
2. Victim clones/opens this repository in GitHub Desktop and checks out a branch/commit, and the checkout call path sets `allowFileProtocol=true`.
3. `checkoutBranch`/`checkoutCommit` calls `updateSubmodulesAfterOperation` with `allowFileProtocol=true`, which adds `-c protocol.file.allow=always` and runs `submodule update --init --recursive`. [7](#0-6) 
4. Git resolves the `file://` submodule URL and copies `/Users/victim/.ssh` into the working tree at `leak/`, making the victim's private key files part of the tracked working directory, ready to be staged and pushed to the attacker's remote.

### Citations

**File:** app/src/lib/git/submodule.ts (L29-54)
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

**File:** app/src/ui/add-repository/add-existing-repository.tsx (L129-174)
```typescript
  private buildRepositoryUnsafeError() {
    const { repositoryUnsafePath, path } = this.state
    if (
      !this.state.path.length ||
      !this.state.showNonGitRepositoryWarning ||
      !this.state.isRepositoryUnsafe ||
      repositoryUnsafePath === undefined
    ) {
      return null
    }

    // Git for Windows will replace backslashes with slashes in the error
    // message so we'll do the same to not show "the repo at path c:/repo"
    // when the entered path is `c:\repo`.
    const convertedPath = __WIN32__ ? path.replaceAll('\\', '/') : path

    const displayedMessage = (
      <>
        <p>
          The Git repository
          {repositoryUnsafePath !== convertedPath && (
            <>
              {' at '}
              <Ref>{repositoryUnsafePath}</Ref>
            </>
          )}{' '}
          appears to be owned by another user on your machine. Adding untrusted
          repositories may automatically execute files in the repository.
        </p>
        <p>
          If you trust the owner of the directory you can
          <LinkButton onClick={this.onTrustDirectory}>
            {' '}
            add an exception for this directory
          </LinkButton>{' '}
          in order to continue.
        </p>
      </>
    )

    const screenReaderMessage = `The Git repository appears to be owned by another user on your machine.
      Adding untrusted repositories may automatically execute files in the repository.
      If you trust the owner of the directory you can add an exception for this directory in order to continue.`

    return { screenReaderMessage, displayedMessage }
  }
```
