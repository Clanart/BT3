### Title
Submodule updates can re-enable the disabled `file://` transport via an unvalidated `allowFileProtocol` flag - ([File: app/src/lib/git/submodule.ts])

### Summary
`updateSubmodulesAfterOperation()` accepts a boolean `allowFileProtocol` parameter that, when `true`, unconditionally injects `-c protocol.file.allow=always` into the `git submodule update --init --recursive` invocation, re-enabling the `file://` transport that modern Git disables by default for non-interactive/scripted invocations (`protocol.file.allow=user`). This mirrors the reported bug class: a config/flag value is applied to the whole operation without any validation of the specific submodule URLs it will affect, and the affected content (`.gitmodules`) is attacker-controlled in a cloned/fetched repository. [1](#0-0) 

### Finding Description
`.gitmodules` is ordinary tracked content, fully controlled by whoever authored the commits in a cloned/fetched repository (a fork, PR branch, or any remote a user adds). When Desktop performs a submodule update with `allowFileProtocol=true`, it passes a blanket `-c protocol.file.allow=always` flag for the *entire* `git submodule update --init --recursive` command rather than validating or scoping which submodule URLs are permitted to use `file://`. [2](#0-1) 

Git ships `protocol.file.allow=user` by default specifically to stop untrusted repositories from declaring submodules that point at local paths (`file:///...`) — a mitigation introduced after CVE-2017-1000117/CVE-2018-11235-class submodule URL attacks. By flipping this back to `always` for the whole operation, Desktop removes that guard for any submodule entry the malicious `.gitmodules` defines, without inspecting or restricting the actual URL values being processed.

### Impact Explanation
An attacker who controls a cloned/fetched repository (fork, PR head, or malicious remote) can commit a `.gitmodules` file with a submodule URL such as `file:///path/to/sensitive/location` on the victim's machine. If Desktop invokes submodule update with `allowFileProtocol=true` while operating on that content, git will happily "clone" from the local filesystem path into the submodule directory inside the user's working tree — copying local file/directory contents into the repo. This is a read of data outside the repository's intended boundary, and because the copied content lands inside the working tree it can subsequently be staged, committed, and pushed by the user, silently exfiltrating local files to a remote the attacker controls.

### Likelihood Explanation
Exploitability depends entirely on whether any caller passes `allowFileProtocol=true` for submodule updates that operate on untrusted content (e.g., checking out a branch/PR from a fork). The flag is consumed from `app/src/lib/git/checkout.ts` (multiple call sites), but I was not able to fully trace, within the remaining tool budget, whether any of those call sites set `allowFileProtocol=true` for attacker-influenced checkouts versus only for locally-created/trusted repositories. This is the key open question needed to confirm end-to-end exploitability — the flag's existence and its unscoped, unvalidated application is confirmed in code, but the exact trigger condition in `checkout.ts` needs verification.

### Recommendation
Do not apply `protocol.file.allow=always` as a blanket operation-wide override. Instead, resolve and inspect each submodule URL declared in `.gitmodules` before initializing/updating it, and only relax `protocol.file.allow` for URLs that are verified to be safe/expected (e.g., matching an explicit allow-list or same-host policy), never for content originating from an untrusted clone/fetch/fork. At minimum, gate `allowFileProtocol=true` behind an explicit, user-confirmed trust decision tied to the specific repository, similar to the existing "unsafe directory" trust prompt already used elsewhere in the app. [3](#0-2) 

### Proof of Concept
1. Attacker publishes a fork/branch whose `.gitmodules` contains:
   ```
   [submodule "x"]
     path = x
     url = file:///Users/victim/.ssh
   ```
2. Victim uses GitHub Desktop to check out this branch/PR (e.g., via "Check out this PR" or opening a PR from Desktop).
3. If the checkout path calls `updateSubmodulesAfterOperation(..., allowFileProtocol: true)`, Desktop runs:
   ```
   git -c protocol.file.allow=always submodule update --init --recursive
   ``` [4](#0-3) 
4. Git clones the local `file:///Users/victim/.ssh` path into the working tree submodule directory `x/`, copying private key material into the repository's working directory, where it can be viewed, committed, or accidentally pushed.

**Note:** Full confirmation requires tracing every `checkout.ts` call site that sets `allowFileProtocol=true` to confirm it can be reached with attacker-controlled `.gitmodules` content (vs. only trusted/local scenarios); this could not be completed within the available tool budget.

### Citations

**File:** app/src/lib/git/submodule.ts (L27-54)
```typescript
 * @param allowFileProtocol - Whether to allow file:// protocol for submodules
 */
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

**File:** app/src/ui/add-repository/add-existing-repository.tsx (L129-167)
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
```
