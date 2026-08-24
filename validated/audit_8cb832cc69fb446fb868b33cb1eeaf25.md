### Title
Unsanitized `filepath` parameter in `x-github-client://openRepo` deep link enables path traversal outside the cloned repository - (File: `app/src/lib/parse-app-url.ts`)

### Summary
The reported Astaria bug is about a permissionless, attacker-controlled parameter (`liquidationInitialAsk`) that is accepted and acted upon without re-validating it against the invariant the user originally relied on, right before a critical, irreversible action (liquidation). The closest verified analog in GitHub Desktop is a parameter-validation asymmetry in the deep-link handler: `parseAppURL` validates the `branch` query parameter of an `openRepo` action but performs **no validation at all** on the `filepath` query parameter, even though both originate from the same untrusted, attacker-controlled deep link that a user is enticed to click (e.g. via a crafted "Open in Desktop" button on a malicious/forked repo or website).

### Finding Description
`parseAppURL` in `app/src/lib/parse-app-url.ts:66-128` parses `x-github-client://openRepo/<url>?branch=...&pr=...&filepath=...` deep links, which Desktop registers itself to handle as the default OS protocol handler (`app/src/main-process/main.ts:88-117`, `238-360`).

For the `openrepo` action:
- `pr` is validated with a numeric regex.
- `branch` is validated with `testForInvalidChars` (imported from `sanitize-ref-name`) and rejected if the branch value looks unsafe: [1](#0-0) 
- `filepath`, however, is read straight from the query string and returned unmodified with **no character or path validation whatsoever**: [2](#0-1) 

This is confirmed by the corresponding action shape, which documents `filepath` as "the file to open after cloning the repository": [3](#0-2) 

The broken invariant is the same shape as the Astaria bug: a value that is supposed to be constrained to a "safe" domain (a path *inside* the freshly cloned repository) is instead accepted unchecked from a fully attacker-controlled source (the deep link), while a sibling parameter (`branch`) in the exact same code path *is* defended with an explicit sanitizer. That asymmetry is strong evidence the omission for `filepath` is a gap rather than an intentional design decision.

### Impact Explanation
If the consumer of `IOpenRepositoryFromURLAction.filepath` (reached via `Dispatcher.dispatchURLAction` → `openRepositoryFromUrl`, `app/src/ui/dispatcher/dispatcher.ts:2118-2120`) joins `filepath` to the local clone directory and opens/reads it without normalizing and re-confining the result to the repository root, a value like `../../../../.ssh/id_rsa` or an absolute path could cause Desktop to open a file **outside the cloned repository** in the user's editor — a "file read outside the repo" outcome, which is explicitly in-scope per the impact criteria (link/deep link the user clicks → file read outside the repo). This can expose the contents of arbitrary local files to the attacker via screen-share/support flows, or at minimum silently substitutes the file the victim believes they are opening (from the repo they intended to open) with a file of the attacker's choosing on the victim's machine.

### Likelihood Explanation
The `x-github-client://` (and legacy `github-mac://` / `github-windows://`) protocol is registered as the OS default handler for Desktop (`app/src/main-process/main.ts`), meaning any web page, email, or chat message containing a crafted `openRepo` link can trigger this code path the moment the user clicks it — no local access, no prior compromise, and no unnatural steps beyond a single click, which satisfies the "link a user clicks" attacker primitive called out as valid. The existing sanitization of `branch` in the same function shows the surrounding code is aware that these parameters are attacker-controlled, making the missing check on `filepath` a concrete, reachable gap rather than a defense-in-depth nicety.

### Recommendation
- Apply the same (or stronger) validation used for `branch` to `filepath`: reject values containing path traversal sequences (`..`), absolute path prefixes, drive letters, or NUL/control characters.
- Wherever `filepath` is eventually consumed, resolve it against the cloned repository root with `path.resolve`/`path.normalize` and verify the resulting path is still a descendant of the repository directory before opening or reading it; reject otherwise.
- Add a regression test analogous to the existing `parse-app-url-test.ts` branch/pr cases, asserting that `openRepo` URLs with traversal sequences in `filepath` are rejected or sanitized.

### Proof of Concept
1. Attacker crafts a link: `x-github-client://openRepo/https://github.com/some/repo?branch=main&filepath=..%2F..%2F..%2F..%2F.ssh%2Fid_rsa`
2. Victim (with GitHub Desktop installed and registered as the protocol handler) clicks the link from a webpage/email/chat.
3. `parseAppURL` accepts the URL, returning `{ name: 'open-repository-from-url', url, branch: 'main', pr: null, filepath: '../../../../.ssh/id_rsa' }` unchanged (verified: `filepath` has no validation call, contrasted with `branch`'s `testForInvalidChars` check). [2](#0-1) 
4. `Dispatcher.dispatchURLAction` routes this to `openRepositoryFromUrl`. [4](#0-3) 

Note: I was not able to locate/verify the exact downstream implementation of `openRepositoryFromUrl` (where `filepath` is joined to the local clone path and opened) within the indexed code, so the final confirmation that the sink lacks path re-confinement is inferred from the parsing-layer asymmetry rather than directly observed in the consuming function. Given index size limits, some file contents may not be available — a full Devin session with complete repository access would be needed to confirm the exact sink behavior for `filepath` and finalize severity.

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
