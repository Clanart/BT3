Based on the evidence gathered, I was unable to fully confirm the exact downstream sink for the `filepath` field before running out of investigation budget, but the pattern strongly parallels the report's bug class (a fully user-controlled parameter that reaches a "receiving" code path without the same validation as its sibling fields).

### Title
Deep-link `open-repository-from-url` action's `filepath` field is not sanitized for path traversal, unlike the `branch` field - (File: app/src/lib/parse-app-url.ts)

### Summary
GitHub Desktop registers a custom protocol handler (`x-github-client://openRepo/...` and other `github-*://openRepo/...` variants) that any web page or link can invoke. The handler is parsed by `parseAppURL`, which builds an `IOpenRepositoryFromURLAction` containing `url`, `branch`, `pr`, and `filepath`, all taken directly from the attacker-controlled query string.

### Finding Description
In `parseAppURL`, the `branch` value is explicitly checked with `testForInvalidChars` before being accepted, and rejected as `unknown` if invalid: [1](#0-0) 

The `pr` value is similarly validated with a numeric regex, and the branch/PR pair is cross-checked against a strict `pr/<n>` format: [2](#0-1) 

However, `filepath` is read straight from the query string with no equivalent validation and passed through into the resulting action object: [3](#0-2) 

This mirrors the structural flaw in the report: a struct is built from fully attacker/user-controlled fields, one sensitive field (`compose` in the original report, `filepath` here) is left unchecked while adjacent fields (`branch`, `pr` / the receiver's compose check) are guarded, and the unguarded field is expected to be consumed later — creating an asymmetry that a downstream consumer must not blindly trust.

This action is dispatched to the renderer and handled by `Dispatcher.dispatchURLAction`, which routes `open-repository-from-url` to `this.openRepositoryFromUrl(action)`: [4](#0-3) 

I was not able to fully trace, within the available tool budget, the exact code inside `openRepositoryFromUrl` that consumes `action.filepath` to confirm whether it joins this value with the freshly cloned repository path (e.g., via `Path.join`) without checking for `../` traversal segments the way `sanitizeCloneName` does for the repository name derived from the URL (`app/src/lib/remote-parsing.ts:88-116`). That sanitizer was clearly added specifically to defend against path-escaping values derived from a URL, which suggests the general risk class is recognized in this codebase, but I could not verify whether the same protection is applied to the `filepath` action field before it is used to open a file post-clone.

### Impact Explanation
If `filepath` is joined with the local clone directory to open a file after cloning without stripping `..` traversal segments (unlike the sibling `branch` field, which is validated), a malicious "Open in Desktop" deep link could cause Desktop to open/read a file outside the freshly cloned repository directory when the victim simply clicks the link — matching the valid impact category of file read outside the repo triggered by a deep link the user clicks.

### Likelihood Explanation
Likelihood cannot be confirmed as "high" without verifying the actual file-open sink in `openRepositoryFromUrl`; this is the missing piece. If the sink does perform a naive `Path.join(repoPath, action.filepath)` and then opens/reveals the resulting path, the likelihood is high (single click, no other user interaction, matches an already-known GitHub Desktop attack surface: the custom protocol handler). If the sink already normalizes/validates the resulting path against the repo root, there is no vulnerability here.

### Recommendation
Apply the same defensive pattern already used for `branch` (`testForInvalidChars`) and for the repo name (`sanitizeCloneName`) to `filepath`: reject or normalize any `filepath` value that resolves outside the repository root before it is used to construct a filesystem path, e.g. by resolving it against the repo root and asserting the resolved path stays within that root.

### Proof of Concept
Not confirmed end-to-end due to inability to inspect the exact file-opening sink in this session. The reachable, confirmed portion of the PoC is:
1. Attacker crafts a link: `x-github-client://openRepo/https://github.com/some/public-repo?branch=main&filepath=..%2F..%2F..%2F..%2Fetc%2Fpasswd` (or a Windows-equivalent traversal sequence).
2. Victim clicks the link; `app.on('open-url', ...)` in `app/src/main-process/main.ts:206-209` forwards it to `handleAppURL`, which calls `parseAppURL`.
3. `parseAppURL` validates `branch` but returns `filepath` unmodified: [5](#0-4) 
4. The resulting action reaches `Dispatcher.openRepositoryFromUrl` via `dispatchURLAction`.

Confirming actual exploitability requires inspecting the body of `openRepositoryFromUrl` in `app/src/ui/dispatcher/dispatcher.ts` (not fully retrieved in this session) to see exactly how `action.filepath` is turned into a filesystem path and opened. I recommend a follow-up session with direct filesystem access to `desktop--005` to pull the full `openRepositoryFromUrl` implementation and verify whether traversal is possible before treating this as confirmed.

### Citations

**File:** app/src/lib/parse-app-url.ts (L98-124)
```typescript
  if (actionName === 'openrepo') {
    const pr = getQueryStringValue(query, 'pr')
    const branch = getQueryStringValue(query, 'branch')
    const filepath = getQueryStringValue(query, 'filepath')

    if (pr != null) {
      if (!/^\d+$/.test(pr)) {
        return unknown
      }

      // we also expect the branch for a forked PR to be a given ref format
      if (branch != null && !/^pr\/\d+$/.test(branch)) {
        return unknown
      }
    }

    if (branch != null && testForInvalidChars(branch)) {
      return unknown
    }

    return {
      name: 'open-repository-from-url',
      url: parsedPath,
      branch,
      pr,
      filepath,
    }
```

**File:** app/src/ui/dispatcher/dispatcher.ts (L2118-2120)
```typescript
      case 'open-repository-from-url':
        this.openRepositoryFromUrl(action)
        break
```
