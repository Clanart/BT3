Based on the investigation, the strongest analog to the "missing authentication on privileged operations" bug class is a **git submodule protocol-restriction bypass** in the local git wrapper code, rather than an IPC/auth issue (Desktop's IPC channel already validates sender identity via `isTrustedIPCSender`, and the deep-link/`open-repository-from-url` handler already validates paths with `isAbsolute()`/`resolveWithin()` checks, so those paths are hardened).

### Title
Submodule update can re-enable `protocol.file.allow=always`, permitting a malicious repository to read files from outside the repo via `file://` submodule URLs - (File: `app/src/lib/git/submodule.ts`)

### Summary
`updateSubmodulesAfterOperation` accepts an `allowFileProtocol` flag that, when true, passes `-c protocol.file.allow=always` to `git submodule update --init --recursive`. [1](#0-0) 
Modern git disables the `file://` transport for submodules by default specifically to stop a malicious repository's `.gitmodules` from pointing a submodule at an arbitrary local path (e.g. another cloned repository, a config directory, etc.) and having that content copied/checked out into the victim's working tree during clone/checkout/pull. Re-enabling this protection with `protocol.file.allow=always` reopens that primitive for any repository that ends up going through this code path with `allowFileProtocol=true`.

### Finding Description
The broken invariant is: *submodule URLs are attacker-controlled content coming from a cloned/fetched repository's `.gitmodules` file, and git's own protocol allow-list is the guard that is supposed to stop those URLs from referencing local filesystem paths.* By explicitly overriding `protocol.file.allow` back to `always`, this guard is defeated for any code path that supplies `allowFileProtocol=true`, regardless of whether the parent repository/remote is trusted. Because `path` values in `.gitmodules` are also attacker-controlled, a crafted repository can define a submodule such as:
```
[submodule "x"]
  path = leak
  url = file:///Users/victim/.ssh
```
When Desktop runs `git submodule update --init --recursive` with `protocol.file.allow=always` in effect, git will happily "clone" the local directory `~/.ssh` into `leak/`, copying its contents into the user's working tree.

### Impact Explanation
If the victim opens the resulting working tree in Desktop, the exfiltrated files become visible in the Changes list and can be staged/committed/pushed by the user without them realizing the files originated outside the repository - this is exactly the "silent corruption of what the user commits/pushes" and "file read outside the repo" impact categories called out as valid. Depending on which local path is targeted (SSH keys, git credentials, other repos' `.git` directories), this can lead to credential exfiltration once the user pushes the resulting commit to a remote the attacker can see (e.g., a PR back to the attacker's fork).

### Likelihood Explanation
Exploitability entirely depends on whether any caller of `updateSubmodulesAfterOperation` (clone, pull, checkout flows) passes `allowFileProtocol: true` for repositories/submodules that are not already fully trusted/local. I was not able to trace all call sites of this function within the available search budget, so I cannot confirm the exact conditions under which `true` is passed versus `false`. This is the key piece of evidence still needed to determine real-world reachability.

### Recommendation
Audit every call site of `updateSubmodulesAfterOperation` and ensure `allowFileProtocol` is only ever `true` for operations on repositories/submodules whose origin is fully trusted (e.g., not attacker-supplied clone/fetch targets). Where the flag is derived from user settings or repository content, ensure it cannot be silently forced on by a cloned repository's own `.gitmodules`/config.

### Proof of Concept
1. Attacker publishes a public repository containing a `.gitmodules` entry: `url = file:///<path to sensitive local directory>`.
2. Victim uses a Desktop flow that reaches `updateSubmodulesAfterOperation(..., allowFileProtocol: true)`.
3. Git config `protocol.file.allow=always` overrides the default deny, and `git submodule update --init --recursive` copies the local directory's contents into the victim's working tree as a "submodule".
4. Victim's Desktop UI shows new files under Changes; if the victim commits/pushes, the previously-local file contents are exfiltrated to the attacker-controlled remote.

**Caveat:** This analysis is limited by not having confirmed which call sites pass `allowFileProtocol: true` and under what trust conditions; a Devin session with full repo access (searching all callers of `updateSubmodulesAfterOperation`) is recommended to confirm actual reachability before treating this as a confirmed exploitable finding.

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
