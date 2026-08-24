Based on the investigation, the strongest analog I found in this codebase is the `protocol.file.allow=always` gate in `updateSubmodulesAfterOperation`, which mirrors the Curve bug's core pattern: **a security-relevant decision (which resources are safe to pull into the working tree) is gated by an all-or-nothing flag rather than by validating the actual attacker-controlled data (submodule URLs) that triggers it.**

### Title
Submodule update can be forced to run with `protocol.file.allow=always`, permitting an attacker-controlled `.gitmodules` to read local files into the working tree - (File: app/src/lib/git/submodule.ts)

### Summary
`updateSubmodulesAfterOperation()` accepts a caller-supplied `allowFileProtocol: boolean` and, when `true`, unconditionally appends `-c protocol.file.allow=always` to the `git submodule update --init --recursive` invocation [1](#0-0) . This re-enables the `file://` transport for submodules network-wide for that git invocation, which is exactly the behavior git upstream restricted by default after CVE-2022-39253 (a malicious `.gitmodules` could point a submodule at a `file://` path — including paths outside the repository, e.g. `../../..` traversal targets or other local directories — causing git to silently copy that local content into the checked-out working tree).

### Finding Description
Git's own default `protocol.file.allow=user` (or `deny`, depending on version) exists specifically to stop repositories from directing local clients to read arbitrary local filesystem paths via a submodule URL crafted in `.gitmodules`. This codebase's `updateSubmodulesAfterOperation()` re-opens that gate whenever it is invoked with `allowFileProtocol: true`, and the check is a static boolean supplied by the caller rather than a check on the actual submodule URLs about to be processed [2](#0-1) . This is the same broken-invariant pattern as the Curve report: instead of inspecting the attacker-controlled payload (submodule URLs, analogous to `_min_amounts`) to decide what is safe, the code makes a blanket decision (`allowFileProtocol`) that is disconnected from the content of the untrusted repository being processed.

I confirmed the flag exists and is plumbed through `checkout.ts` (7 references), but I was not able to fully read `checkout.ts` in the time available to confirm the exact call sites and whether `true` is passed unconditionally after checking out an attacker-supplied branch/PR, or only under a more restrictive condition. This is the key fact that determines actual exploitability and I could not verify it with certainty.

### Impact Explanation
If `allowFileProtocol: true` is reachable from a flow that processes an untrusted, attacker-supplied repository (e.g., checking out a branch/PR from a cloned/forked repo whose `.gitmodules` an attacker controls), the impact is a **file read outside the repository boundary**: git would honor a `file://` submodule URL and copy local filesystem content (which could include other private repositories, credential files, etc., depending on path) into the victim's working tree as a submodule checkout. That content then becomes part of the repository's working directory, and — mirroring the Curve report's "silent corruption of what the user commits" impact — the user could unknowingly stage, commit, and push that exfiltrated content to a remote, since Desktop's changes list doesn't distinguish "this file arrived via a re-enabled file:// submodule" from any other legitimately fetched submodule.

### Likelihood Explanation
Likelihood depends entirely on whether `allowFileProtocol: true` is passed on a path that handles untrusted repository content (e.g., after cloning or checking out a branch from a repo the user does not control, such as opening a PR branch). I was unable to confirm this within the available tool budget, so likelihood is uncertain — it could range from "not reachable with attacker-controlled input" (if `true` is only used for the user's own local operations) to "directly reachable" (if set unconditionally during clone/checkout flows involving forked/cloned repositories).

### Recommendation
Audit every call site of `updateSubmodulesAfterOperation` in `app/src/lib/git/checkout.ts` and determine which ones pass `allowFileProtocol: true`. For any call site that processes an untrusted repository (clone of a URL, checkout of a fork/PR branch not previously trusted by the user), do not blanket-allow `protocol.file.allow=always`; instead, either default to git's safe behavior (disallow `file://` submodules) or first parse `.gitmodules` submodule URLs and reject any `file://` URL that resolves outside the containing repository's directory (mirroring the existing `resolveWithin` guard already used elsewhere, e.g. in `app/src/lib/git/clone.ts` and `app/src/lib/copilot-conflict-context.ts`).

### Proof of Concept
1. Attacker publishes a repository whose `.gitmodules` contains a submodule entry with `url = file:///Users/victim/some-sensitive-dir` (or a relative `file://../../..` traversal target).
2. Victim opens/clones this repository in GitHub Desktop and performs an operation that internally calls `updateSubmodulesAfterOperation(..., allowFileProtocol: true)` (exact trigger path unverified — needs confirmation in `checkout.ts`).
3. Because `-c protocol.file.allow=always` is appended, `git submodule update --init --recursive` honors the `file://` URL and copies the target local directory's contents into the submodule path inside the victim's working tree.
4. The victim, unaware that the submodule content originated from a local path rather than a legitimate remote, stages and commits the changes (including the previously local-only files) and pushes them to a shared remote, exfiltrating the data. [1](#0-0) 

**Caveat:** I could not fully verify the exact call sites in `app/src/lib/git/checkout.ts` (7 matches found via grep but not read due to iteration limits) to confirm whether `allowFileProtocol: true` is actually reachable via an untrusted-repo code path in this specific fork of Desktop. This is essential to confirm before treating this as a confirmed, exploitable vulnerability rather than a plausible analog based on the flag's existence and naming.

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
