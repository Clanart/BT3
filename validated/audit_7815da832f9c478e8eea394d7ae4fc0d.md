Based on my research, I found a concrete asymmetry in deep-link/URL-action parsing that maps to the same class of bug as the report: **a value that reaches a sensitive operation without the same validation applied to its siblings**, in `strategy/programs/vaultka` that was `position_amount`/`is_in_withdraw_request` not being checked; here it is the `filepath` parameter of the `openRepo` deep-link action not being validated the way `branch` and `pr` are.

### Title
Unvalidated `filepath` parameter in `openRepo` deep-link action bypasses the same input sanitization applied to `branch`/`pr` - (File: `app/src/lib/parse-app-url.ts`)

### Summary
`parseAppURL` handles the `x-github-client://openRepo/...` deep link (and the `github-mac://openRepo/...` equivalent), which is fully attacker-controlled content since it can be delivered via a crafted link a user clicks (e.g. embedded in a web page, email, or malicious website). [1](#0-0)  The function validates `pr` with a strict digit regex and `branch` with `testForInvalidChars`, rejecting the whole action if either fails, but it forwards `filepath` straight from the query string into `IOpenRepositoryFromURLAction` with no equivalent check. [2](#0-1) 

### Finding Description
The invariant that should hold is: "every attacker-supplied field consumed from a deep link must be sanitized before use," and it demonstrably holds for `pr` (`/^\d+$/`) and `branch` (`testForInvalidChars`), but not for `filepath`. [3](#0-2)  This is the exact analog of the reported bug class: one code path enforces a guard (`request_deposit`/`is_in_withdraw_request` checks in the original report) while a sibling path that shares the same downstream consumer skips it, letting a corrupted/unchecked value flow forward. The `open-repository-from-url` action is dispatched to `Dispatcher.dispatchURLAction` and ultimately to the repository-open flow, where `filepath` is presumably used to select a file to display/open after the repository is opened; the `dispatcher.ts` file has additional references to `filepath` that I could not fully trace to its final sink within the remaining tool budget. [4](#0-3) 

### Impact Explanation
If `filepath` is joined onto the repository's working directory path without normalization/containment checks (I was not able to confirm the exact join/consumption code before running out of iterations), a value like `../../../../.ssh/id_rsa` or an absolute path could cause Desktop to open or reveal a file outside the cloned repository when the user clicks a link, which matches the required impact class ("file read outside the repo," attacker controls a link/deep link).

### Likelihood Explanation
The `openRepo` action is reachable purely by getting a user to click a crafted protocol-handler link, matching the "unprivileged … link or deep link the user clicks" attacker model in scope, and the validation gap is explicit and visible in the parsing function relative to `branch`/`pr`. [2](#0-1)  However, I could not confirm within this session whether the downstream file-open code performs its own path containment check (e.g. `Path.resolve` + prefix check) before using `filepath`, which is required to determine whether this gap is actually exploitable or is a defense-in-depth-only issue.

### Recommendation
Apply the same `testForInvalidChars`-style validation (or an explicit path-traversal check disallowing `..`, absolute paths, and drive letters) to `filepath` in `parseAppURL` before constructing `IOpenRepositoryFromURLAction`, and confirm/enforce that any downstream consumer resolves the path and verifies it stays within the repository root before opening it.

### Proof of Concept
Craft and have a user click:
```
x-github-client://openRepo/https://github.com/octocat/Hello-World?branch=main&filepath=..%2F..%2F..%2F..%2F.ssh%2Fid_rsa
```
`parseAppURL` will accept this because `branch=main` passes validation and `filepath` is never checked, producing `{ name: 'open-repository-from-url', url: '...', branch: 'main', filepath: '../../../../.ssh/id_rsa' }`. [5](#0-4)  Whether this results in an actual out-of-repo file read depends on code I could not fully trace in `app/src/ui/dispatcher/dispatcher.ts`; a Devin session with full file access would be needed to confirm the final sink and whether it sanitizes the resolved path.

**Caveat:** Due to running out of investigation iterations, I could not verify the exact downstream code that consumes `filepath` (in `dispatcher.ts`) to confirm whether it actually results in a path-traversal read/write, versus being safely handled elsewhere. This finding should be treated as a validation-gap observation requiring further confirmation, not a fully proven exploit chain.

### Citations

**File:** app/src/lib/parse-app-url.ts (L66-97)
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

  // Trim the trailing / from the URL
  const parsedPath = pathName.substring(1)

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

**File:** app/src/ui/dispatcher/dispatcher.ts (L2100-2120)
```typescript
  public async dispatchURLAction(action: URLActionType): Promise<void> {
    switch (action.name) {
      case 'oauth':
        await this.appStore._resolveOAuthRequest(action)

        if (__DARWIN__) {
          // workaround for user reports that the application doesn't receive focus
          // after completing the OAuth signin in the browser
          const isFocused = await isWindowFocused()
          if (!isFocused) {
            log.info(
              `refocusing the main window after the OAuth flow is completed`
            )
            window.focus()
          }
        }
        break

      case 'open-repository-from-url':
        this.openRepositoryFromUrl(action)
        break
```
