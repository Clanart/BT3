## Analysis

The external report's core issue is: **a security-relevant string value from an untrusted source is accepted with insufficiently strict/inconsistent validation**, so the code behaves as if a different, unchecked value was validated ("proof" is checked but "proofWithSignature" behavior is inferred from unrelated data, not from an explicit check).

I found a structurally identical pattern in GitHub Desktop's custom-URL-scheme handler: [1](#0-0)  parses the `openrepo` deep-link action. It explicitly validates `pr` with a regex and validates `branch` with `testForInvalidChars` [2](#0-1) , but the `filepath` query parameter is read straight from the URL with `getQueryStringValue` and returned completely unvalidated: [3](#0-2) . There is no equivalent of `testForInvalidChars` (or any `..`/path-separator check) applied to `filepath`, even though `branch` — a conceptually similar "identifier" field — is explicitly hardened.

### Title
Deep-link `filepath` parameter in `x-github-client://openRepo` is not sanitized for path traversal, unlike the sibling `branch` parameter - (File: app/src/lib/parse-app-url.ts)

### Summary
`parseAppURL` enforces strict validation on `branch` (via `testForInvalidChars`) and `pr` (via a digit regex) for the `openrepo` deep-link action, but applies **no validation at all** to `filepath`, which is documented as "the file to open after cloning the repository." [4](#0-3)  This is the same class of bug as the in3-server report: one branch of a protocol handler is protected against malicious input while a semantically similar field is passed through unchecked because the code implicitly assumes it will "always be a filename."

### Finding Description
`handleAppURL` in the main process dispatches `x-github-client://openRepo/<url>?branch=...&pr=...&filepath=...` links to `parseAppURL`, which builds an `IOpenRepositoryFromURLAction` object containing the raw, unsanitized `filepath` string [5](#0-4) . Compare this to the `branch` field a few lines above, which is rejected outright if `testForInvalidChars(branch)` returns true [6](#0-5)  — a guard that exists specifically to stop traversal-style or shell-breaking characters from reaching downstream git/filesystem operations.

`filepath` receives no such treatment, so an attacker who crafts the deep link can set `filepath=../../../../some/sensitive/path` or a value containing OS path separators. This action is then consumed by `Dispatcher` (`app/src/ui/dispatcher/dispatcher.ts`, where `filepath` appears 6 times per `grep_search`) to open a file after the clone completes. I could not fully confirm within available search iterations exactly how the dispatcher joins `filepath` with the freshly cloned repository's root path before invoking the file-open API, so the precise downstream sink (`app/src/lib/app-shell.ts` / `app/src/ui/lib/open-file.ts`) needs to be verified directly against source to determine whether `Path.join`/`resolveWithin`-style containment (as used elsewhere, e.g. `resolveWithin` in `app/test/unit/path-test.ts`) is applied before the open call.

### Impact Explanation
If the join is a naive `Path.join(repoPath, filepath)` without the same traversal containment that `resolveWithin` provides elsewhere in the codebase, a user who clicks an attacker-supplied `x-github-client://openRepo/...&filepath=..%2F..%2F..%2Fetc%2Fpasswd`-style link would have Desktop open (or read) a file outside the cloned repository directory — matching the accepted impact category of "file... read outside the repo" triggered purely by "a link or deep link the user clicks."

### Likelihood Explanation
The attack requires only that a user click a specially crafted `x-github-client://` link (e.g., embedded in a webpage, README, or chat message) — no local access, no prior malware, and no credential leak. This is directly analogous to the in3-server report's premise that a client fully controls a request field the server trusts without cross-checking against the specification. Whether this is currently exploitable depends entirely on the unverified downstream sink; my search did not conclusively confirm the sink lacks the `resolveWithin` guard.

### Recommendation
Apply the same (or a path-traversal-specific) validation used for `branch` to `filepath` in `parse-app-url.ts` — reject any value containing `..`, absolute path roots, or path separators outside a single filename component, and/or require the downstream consumer in `dispatcher.ts` to resolve `filepath` against the repository root using the existing `resolveWithin` helper (already used in `app/test/unit/path-test.ts` for similar containment) before any file-open operation.

### Proof of Concept
Not independently confirmed end-to-end due to inability to inspect the exact `dispatcher.ts` sink in this session; a Devin session with full file access should verify:
1. `parseAppURL('x-github-client://openRepo/https://github.com/octocat/Hello-World?filepath=..%2F..%2F..%2F..%2Fetc%2Fpasswd')` returns an `open-repository-from-url` action with `filepath: '../../../../etc/passwd'` unmodified.
2. Trace `Dispatcher`'s handling of `IOpenRepositoryFromURLAction.filepath` in `app/src/ui/dispatcher/dispatcher.ts` to confirm whether it is joined to the repo path without `resolveWithin`/containment checks before being passed to the file-open API in `app/src/ui/lib/open-file.ts` or `app/src/lib/app-shell.ts`.

### Citations

**File:** app/src/lib/parse-app-url.ts (L10-24)
```typescript
export interface IOpenRepositoryFromURLAction {
  readonly name: 'open-repository-from-url'

  /** the remote repository location associated with the "Open in Desktop" action */
  readonly url: string

  /** the optional branch name which should be checked out. use the default branch otherwise. */
  readonly branch: string | null

  /** the pull request number, if pull request originates from a fork of the repository */
  readonly pr: string | null

  /** the file to open after cloning the repository */
  readonly filepath: string | null
}
```

**File:** app/src/lib/parse-app-url.ts (L98-125)
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
  }
```
