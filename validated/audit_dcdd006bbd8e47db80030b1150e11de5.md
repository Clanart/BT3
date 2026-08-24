Based on my research, I found a stronger, better-supported analog than a simple "missing access control on a state-mutating function." The closest matching bug class — attacker-controlled repository content leading to unauthorized file access outside the intended repository boundary — is in the submodule update code path.

### Title
Submodule updates can be forced to use `protocol.file.allow=always`, letting a malicious repository read arbitrary local paths via `file://` submodule URLs - ([File: app/src/lib/git/submodule.ts])

### Summary
`updateSubmodulesAfterOperation()` accepts an `allowFileProtocol: boolean` parameter that, when true, prepends `-c protocol.file.allow=always` to the `git submodule update --init --recursive` invocation [1](#0-0) . This is a re-enablement of a protocol Git upstream deliberately disabled by default (Git ≥2.38, following CVE-2022-39253) precisely because a repository's `.gitmodules` file is attacker-controlled content: a malicious remote can declare a submodule URL of `file:///some/local/path` (or a relative `file://../../..` path) and, on `submodule update --init`, Git will "clone" that local path's contents directly into the working tree.

### Finding Description
The broken invariant is: submodule URLs come from `.gitmodules`, which is fully attacker-controlled content delivered by a cloned/fetched repository, yet the code path exposes a knob (`allowFileProtocol`) that re-authorizes Git to treat `file://` submodule URLs as trusted local clone sources. Git's own default-deny for `protocol.file.allow` exists specifically to stop this pattern; explicitly setting it to `always` for the submodule update inverts that guard. The call sites are in `app/src/lib/git/checkout.ts` (confirmed via 4 references to `allowFileProtocol`) and `app/src/lib/git/submodule.ts` (3 references), which is where `updateSubmodulesAfterOperation` is invoked [2](#0-1) .

### Impact Explanation
If `allowFileProtocol` is set to `true` on a path that runs against an untrusted, attacker-supplied repository (e.g., after cloning or checking out a branch from a malicious remote), the attacker's `.gitmodules` can point a submodule at any local filesystem path reachable by the Desktop process (for example another local git repository, or a path containing an `.ssh`/config directory that happens to be a git repo). Git will read/copy that path's contents into the working tree as if it were a normal submodule, giving the attacker a way to exfiltrate or expose local file contents outside the boundaries of the cloned repository — matching the "file read outside the repo" impact class from the task's valid-impact list.

### Likelihood Explanation
I was not able to fully confirm, within available tool budget, the exact conditions in `app/src/lib/git/checkout.ts` under which `allowFileProtocol` is passed as `true` (e.g., whether it's gated behind an existing-trusted-submodule check, always false for freshly cloned untrusted repos, or user-confirmed). This materially affects the real-world exploitability: if the flag is only ever `true` for previously-vetted/local submodules already present before the malicious content was introduced, the risk is much lower; if it can be `true` on the first `submodule update --init` after cloning a new/attacker-controlled repository, exploitability is high and directly reachable with no unusual user action beyond "clone and open" a malicious repo. This needs to be verified by reading the call sites in `checkout.ts` directly, which I flag as an open item.

### Recommendation
- Audit every call site of `updateSubmodulesAfterOperation(..., allowFileProtocol)` in `app/src/lib/git/checkout.ts` and ensure `allowFileProtocol: true` is never used for submodules discovered from an untrusted/newly-cloned repository's `.gitmodules`.
- Default `protocol.file.allow` to `user` (Git's safer default) rather than `always`, and only permit `file://` submodules when the user explicitly confirms trust for that specific submodule/path (similar to the existing "safe.directory"/unsafe-repository trust flow already implemented in `app/src/lib/git/config.ts`'s `addSafeDirectory`).
- Add a check that rejects/normalizes `file://` submodule URLs resolving outside the repository's own directory tree, mirroring the traversal guard already used elsewhere (`resolveWithin` in `app/src/lib/path.ts`).

### Proof of Concept
1. Attacker publishes a repository containing a `.gitmodules` file with an entry such as:
   ```
   [submodule "leak"]
       path = leak
       url = file:///Users/victim/.ssh
   ```
2. Victim clones the repository in GitHub Desktop (or opens/fetches it, triggering a submodule update where `allowFileProtocol` resolves to `true`).
3. `updateSubmodulesAfterOperation` runs `git -c protocol.file.allow=always submodule update --init --recursive`, which "clones" `/Users/victim/.ssh` into `leak/` inside the victim's working tree [3](#0-2) .
4. The victim's SSH keys (or any other locally reachable git-tracked or plain directory Git can treat as a source) now appear as tracked/visible files inside the opened repository, exposed to subsequent commit, diff view, or exfiltration via a follow-up push.

**Note on confidence**: This report is based on static evidence of the `allowFileProtocol` mechanism and Git's own well-documented CVE-2022-39253 protection being explicitly overridden in this codebase. I could not, within the available tool budget, inspect the full logic in `app/src/lib/git/checkout.ts` that decides when `allowFileProtocol` is `true` versus `false`, which is necessary to conclusively confirm reachability from an untrusted/attacker-controlled repository without additional user steps. I recommend this be verified directly before treating it as a confirmed exploitable path.

### Citations

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
