Based on my investigation, I was unable to confirm a full end-to-end exploitable path with certainty — I found a concrete, provable input-validation gap in the deep-link handler, but I did not have remaining tool budget to trace the exact file-open sink in `dispatcher.ts` to confirm whether a path-traversal payload actually escapes the intended directory. I'll report what is verified and flag the unverified part explicitly.

### Title
Deep-link `openRepo` action does not sanitize the `filepath` parameter, unlike `branch`/`pr` - (File: `app/src/lib/parse-app-url.ts`)

### Summary
`parseAppURL` in [1](#0-0)  parses the `x-github-client://openRepo/...` deep link (the "Open in Desktop" protocol handler, reachable from any web page or attacker-controlled HTML the user clicks). For the `openrepo` action it validates the `pr` parameter with a numeric regex, validates `branch` with `testForInvalidChars`, but reads `filepath` from the query string with no validation whatsoever before constructing the `IOpenRepositoryFromURLAction`: [2](#0-1) 

This is the same trust-boundary problem shape as the Sablier finding: a value that is later used to select a target resource ("payer"/here, "which file to open on disk") is accepted directly from an untrusted, externally-controlled source (a clicked link) without being constrained the way sibling parameters in the same function are constrained.

### Finding Description
The `openRepo` deep-link action is fully attacker-controlled: any web page can embed a link such as `x-github-client://openRepo/https://github.com/owner/repo?branch=main&filepath=<attacker string>`, and simply getting the user to click it triggers `handleAppURL` → `parseAppURL` → `dispatchURLAction` → `openRepositoryFromUrl` (confirmed present in `app/src/ui/dispatcher/dispatcher.ts`, which references `filepath` six times). Unlike `branch` (checked against `testForInvalidChars`) and `pr` (checked against `/^\d+$/`), `filepath` is passed through unchecked as a raw string. If the downstream file-open logic in `dispatcher.ts`/`openRepositoryFromUrl` joins this value onto the freshly cloned repository's path without resolving and validating that the final path stays within the repository root (e.g. no check equivalent to `testForInvalidChars` or a realpath containment check), a payload such as `..%2F..%2F..%2F.ssh%2Fid_rsa` or an absolute path could cause Desktop to open a file outside the cloned repository directory.

### Impact Explanation
If the (unverified) sink does not perform containment/normalization, the impact matches "attacker controls a link/deep link the user clicks" → "file read... outside the repo": Desktop could open/reveal an arbitrary file on the user's disk (e.g., SSH keys, `.netrc`, other repository's source) chosen entirely by the attacker who crafted the link, with a single click and no other privileges required.

### Likelihood Explanation
Medium-low confidence rather than confirmed, because:
- I confirmed the missing sanitization at the parsing layer (`parse-app-url.ts`), which is a clear asymmetry versus the `branch` field handled two lines above it.
- I was **not able to verify**, within the remaining tool budget, the exact code in `dispatcher.ts`'s `openRepositoryFromUrl` that consumes `filepath` (i.e., whether it does `path.join` and opens directly, or whether it separately validates/normalizes the path before use). This is the missing piece needed to state definitively that traversal succeeds.

### Recommendation
Apply the same defensive pattern already used for `branch` in `parse-app-url.ts`: reject `filepath` values containing path separators leading outside the repo (`..`), absolute path prefixes, or invalid characters, and additionally verify in the consumer (`dispatcher.ts`) that the resolved absolute path is still contained within the cloned repository's root directory before opening it (e.g., using `path.resolve` + a prefix check with the repository path).

### Proof of Concept
1. Attacker hosts a link: `<a href="x-github-client://openRepo/https://github.com/owner/repo?filepath=../../../../.ssh/id_rsa">Open in Desktop</a>`.
2. Victim (with GitHub Desktop installed and registered as the protocol handler) clicks the link.
3. `parseAppURL` accepts `filepath` unmodified (no check exists, contrast with the `testForInvalidChars(branch)` check at [3](#0-2) ), and returns it as-is inside the `open-repository-from-url` action.
4. `dispatchURLAction` forwards the action to `openRepositoryFromUrl` in `app/src/ui/dispatcher/dispatcher.ts` — the exact join/open logic there needs to be inspected directly (I could not do so this session) to confirm whether the traversal payload reaches an unguarded file-open call.

Because step 4 is unverified, treat this as a **candidate finding requiring confirmation** of the `filepath` consumption logic in `dispatcher.ts` rather than a fully proven vulnerability. A Devin session with full repo access should read `openRepositoryFromUrl` in `app/src/ui/dispatcher/dispatcher.ts` end-to-end to confirm or rule out the traversal.

### Citations

**File:** app/src/lib/parse-app-url.ts (L66-94)
```typescript
export function parseAppURL(url: string): URLActionType {
  const parsedURL = URL.parse(url, true)
  const hostname = parsedURL.hostname
  const unknown: IUnknownAction = { name: 'unknown', url }
  if (!hostname) {
    return unknown
  }

  const query = parsedURL.query

  const actionName = hostname.toLowerCase()
  if (actionName === 'oauth') {
    const code = getQueryStringValue(query, 'code')
    const state = getQueryStringValue(query, 'state')
    if (code != null && state != null) {
      return { name: 'oauth', code, state }
    } else {
      return unknown
    }
  }

  // we require something resembling a URL first
  // - bail out if it's not defined
  // - bail out if you only have `/`
  const pathName = parsedURL.pathname
  if (!pathName || pathName.length <= 1) {
    return unknown
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
