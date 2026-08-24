Based on my investigation, I found one area worth flagging, though it requires further verification with a background agent for exact call-site behavior (I was not able to exhaustively trace all call sites of `allowFileProtocol` before running out of iterations).

### Title
Submodule update re-enables `protocol.file.allow=always` for attacker-controlled `.gitmodules`, permitting local file exfiltration via cloned repository - (File: `app/src/lib/git/submodule.ts`)

### Summary
`updateSubmodulesAfterOperation` in `app/src/lib/git/submodule.ts` builds the `git submodule update --init --recursive` command and, when its `allowFileProtocol` parameter is `true`, prepends `-c protocol.file.allow=always` to the git invocation [1](#0-0) . Modern Git defaults `protocol.file.allow` to `user`/restricted specifically to prevent a cloned repository's `.gitmodules` file from silently pulling in submodules via the `file://` scheme, which can be abused to read or copy arbitrary local files into the checkout. Overriding this to `always` re-opens that class of bug for any repository this code path treats as eligible for file-protocol submodules.

### Finding Description
The `.gitmodules` file and submodule URLs inside a repository are fully attacker-controlled content — they arrive from whatever remote/repository the user clones or fetches, exactly matching the report's "attacker controls a cloned/fetched repository" primitive. Git added `protocol.file.allow` (defaulting away from `always`) as a direct mitigation for malicious `file://` submodule URLs (the same bug class as CVE-2017-1000117 for `ext::`/`file://` submodule handlers), because a submodule URL of `file:///home/user/.ssh` (or similar) lets a hostile repository copy sensitive local directories into the working tree during `submodule update --init --recursive`. By re-enabling `protocol.file.allow=always` whenever `allowFileProtocol` is `true`, this code disables that guard rail for the affected invocation, and the surrounding pipeline (`envForRemoteOperation`, `executionOptionsWithProgress`) does not perform any independent validation of the submodule URL scheme before invoking git [2](#0-1) .

### Impact Explanation
If `allowFileProtocol` is set to `true` for submodule updates performed as part of an unprivileged clone/fetch/checkout of an attacker-supplied repository, the attacker can point a submodule at a `file://` path on the victim's machine. Git will copy the contents of that local path into the resulting checkout, which the app then treats as ordinary repository content — this is a "read outside the repo" primitive (arbitrary local directory contents pulled into the workspace) and, depending on subsequent user action (e.g., pushing the resulting commit), can turn into credential/token exfiltration.

### Likelihood Explanation
I could not fully confirm from the index which call sites pass `allowFileProtocol: true` and under what conditions (e.g., only for local/relative submodule URLs the user explicitly requested vs. any freshly cloned attacker repository) — this needs to be verified by a background agent with full repo access, since the index did not surface all callers of `updateSubmodulesAfterOperation`. If the flag is only ever true for submodules whose parent remote is itself `file://`/local (a legitimate desktop workflow), the risk is much lower; if it's enabled broadly for any freshly cloned repository regardless of submodule origin, the risk is high and directly reachable by cloning an untrusted repo.

### Recommendation
Verify every call site of `updateSubmodulesAfterOperation` with `allowFileProtocol: true` and restrict it strictly to cases where the top-level remote itself is a local/file path initiated directly by the user (not attacker-supplied submodule URLs inside a remote HTTPS/SSH clone). Prefer Git's default `protocol.file.allow=user` (or omit the override entirely) for any submodule update triggered by cloning/fetching a remote, untrusted repository.

### Proof of Concept
Not independently reproduced — this requires a background agent to (1) enumerate callers of `updateSubmodulesAfterOperation`, (2) construct a test repository whose `.gitmodules` submodule URL uses `file:///<sensitive-local-path>`, and (3) confirm whether Desktop's clone/checkout flow reaches this function with `allowFileProtocol: true` for that scenario, then observe whether local file contents get copied into the resulting checkout. [3](#0-2)

### Citations

**File:** app/src/lib/git/submodule.ts (L29-56)
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
  }
```
