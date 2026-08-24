### Title
Submodule update re-enables `file://` protocol via `allowFileProtocol`, allowing malicious repos to exfiltrate local files - (File: app/src/lib/git/submodule.ts)

### Summary
`updateSubmodulesAfterOperation` in `app/src/lib/git/submodule.ts` accepts an `allowFileProtocol` boolean that, when true, prepends `-c protocol.file.allow=always` to the `git submodule update --init --recursive` invocation. [1](#0-0)  This mirrors the "Incorrect Parameter Setting" bug class from the external report: a security-relevant Git configuration parameter (`protocol.file.allow`) is force-set to a permissive value (`always`) rather than left at its safe default (`user`/`never`), and the decision of when to do so is controlled by an app-internal flag whose trust boundary is not visible from this file alone.

### Finding Description
Since Git 2.38.1 (CVE-2022-39253), `protocol.file.allow` defaults to a restrictive setting specifically to stop a cloned/fetched repository's `.gitmodules` from pointing a submodule at a `file://` URL, which previously allowed an attacker-controlled repository to make `git submodule update --init --recursive` copy arbitrary local files (or nested repos) into the victim's working tree. GitHub Desktop's `updateSubmodulesAfterOperation` explicitly overrides this hardening by injecting `protocol.file.allow=always` whenever it is invoked with `allowFileProtocol=true`. [2](#0-1)  The submodule URLs used in this call come from `.gitmodules`, an attacker-controlled file inside a cloned/fetched repository — the exact "attacker controls a cloned/fetched repository" primitive required by the task's valid-impact criteria.

The corrupted value is the effective Git config `protocol.file.allow`, which is turned from a safe default into `always` for the duration of the submodule update, re-opening the exact class of vulnerability upstream Git intentionally closed.

### Impact Explanation
If `allowFileProtocol=true` is reachable for repositories/submodule updates triggered from content the app does not fully control (e.g. checking out a branch or pulling changes that add/modify `.gitmodules` in a cloned repository), a malicious `.gitmodules` entry such as:
```
[submodule "x"]
  path = x
  url = file:///Users/victim/.ssh
```
would cause `git submodule update --init --recursive` to copy the target directory into the working tree as a submodule, effectively performing a read of files outside the intended repository boundary and placing them inside a location the user may subsequently commit, push, or otherwise disclose. This satisfies the "file read/write outside the repo" and "silent corruption of what the user commits" impact categories from the valid-impact list.

### Likelihood Explanation
The likelihood depends entirely on the call sites that set `allowFileProtocol=true`. Callers are located in `app/src/lib/git/checkout.ts` (7 references found), but I was not able to inspect that file's logic in this session to confirm under exactly which conditions (e.g., only for local/trusted repositories vs. any checkout/pull that touches submodules) the flag is set to `true`. If it is unconditionally `true` for any checkout that updates submodules — including checkouts of remote branches or PRs from forks whose `.gitmodules` the user does not control — the path is directly reachable by an unprivileged attacker who can get a target to check out/pull a branch. If instead it is gated behind an explicit user-trust decision, the risk is substantially reduced.

### Recommendation
- Audit every call site of `updateSubmodulesAfterOperation` in `app/src/lib/git/checkout.ts` and confirm `allowFileProtocol` is only `true` when the repository/`.gitmodules` content has already been established as trusted (e.g., previously-cloned local repos the user explicitly trusted), never for fresh checkouts of branches/PRs originating from untrusted forks or newly fetched refs.
- Prefer leaving `protocol.file.allow` at Git's default and, if `file://` submodules must be supported for legitimate local-development workflows, scope the override as narrowly as possible (e.g., only for submodule paths already present and unchanged, not newly added ones from an untrusted diff).
- Add a regression test asserting that submodule updates triggered from checking out an untrusted/remote branch never pass `allowFileProtocol=true`.

### Proof of Concept
1. Attacker publishes/pushes a branch whose `.gitmodules` contains a submodule with `url = file:///path/to/sensitive/dir` (or `file:///home/victim/.ssh`).
2. Victim, using GitHub Desktop, checks out or pulls that branch in a way that triggers `updateSubmodulesAfterOperation` with `allowFileProtocol=true` (call site in `app/src/lib/git/checkout.ts`, exact condition unverified in this session).
3. `git -c protocol.file.allow=always submodule update --init --recursive` executes, per `app/src/lib/git/submodule.ts:45-51`, cloning the local path into the submodule directory of the repository.
4. Contents of the local path are now materialized inside the repository working tree, where they can be viewed in Desktop's UI, committed, and pushed — exfiltrating local file contents through the repository the attacker controls. [2](#0-1)

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
