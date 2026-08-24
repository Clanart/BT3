### Title
Deep-link `open-repository-from-url` action forwards unvalidated `filepath`/`url` fields from attacker-controlled links - (File: `app/src/lib/parse-app-url.ts`)

### Summary
`parseAppURL` builds an `IOpenRepositoryFromURLAction` from a `x-github-client://openRepo/...` deep link. The `pr` and `branch` query parameters are strictly validated, but the `url` (repository location) and `filepath` (file to open after cloning) fields are taken verbatim from the untrusted URL and returned without any sanitization or path-scope check.

### Finding Description
In `parseAppURL`, for the `openrepo` action, `pr` is validated with `/^\d+$/`, and `branch` is checked with `testForInvalidChars` and a `pr/\d+` pattern when a PR is present: [1](#0-0) 

However `filepath` is only read via `getQueryStringValue` and placed into the resulting action object with no validation whatsoever, and `url` (`parsedPath`, derived directly from the deep-link path) is likewise unchecked for scheme/host or traversal sequences: [2](#0-1) 

This action is dispatched straight to the application's `openRepositoryFromUrl` handler: [3](#0-2) 

This is the same class of bug as the Halborn report: a field that ultimately controls a sensitive operation (there, which vault receives fees; here, which repository is cloned and which file inside it is opened) is accepted from an external/attacker-influenced source without validating its shape, scope, or association with the expected resource, while sibling fields in the very same instruction/action are validated. In the Desktop case, the attacker primitive is a link/deep-link the user clicks (an "Open in Desktop" style URL), which is exactly one of the permitted attacker primitives for this analog exercise.

### Impact Explanation
If the downstream consumer of `filepath` (in `openRepositoryFromUrl`/`AppStore`) joins it to the freshly cloned repository path without confirming it resolves inside that repository, a malicious deep link (e.g. containing `../../` segments or an absolute path) could cause Desktop to open or read a file outside the intended repository directory once the target repository has been cloned — a file-read-outside-repo primitive triggered purely by the user clicking a link, matching the "unprompted click on a link/deep link" attacker model. I was unable to confirm within the available tool budget whether the eventual sink normalizes/clamps the path before use, so this impact should be verified against the `filepath` consumer in `dispatcher.ts`/`AppStore` before being treated as confirmed exploitable.

### Likelihood Explanation
Deep links of this form are a documented, user-facing feature ("Open in Desktop"), so the attack surface is reachable by any web page or email that can register/trigger the `x-github-client://openRepo` protocol handler — no local access, admin rights, or prior compromise is required, satisfying the valid-impact criteria for this analog.

### Recommendation
- Validate `filepath` the same way `branch` is validated (reject invalid/traversal characters, e.g. via `testForInvalidChars` or a dedicated path-safety check) before including it in the parsed action.
- Before using `filepath` downstream, resolve it against the cloned repository root and verify the resolved path stays within that root (canonicalize and prefix-check), rejecting `..`, absolute paths, or symlink escapes.
- Apply equivalent scheme/format validation to `url` before it is used to drive a clone operation.

### Proof of Concept
1. Attacker crafts a link: `x-github-client://openRepo/some-org/some-repo?filepath=../../../../.ssh/id_rsa`.
2. Victim clicks the link; GitHub Desktop parses it via `parseAppURL`, which validates `pr`/`branch` but passes `filepath` through unchanged.
3. `dispatchURLAction` routes the resulting `open-repository-from-url` action to `openRepositoryFromUrl` in `dispatcher.ts`.
4. If the downstream handler joins `filepath` to the repository path without a containment check, the application opens/reads a file outside the cloned repository (exact consumer behavior not yet verified in this session — recommend confirming in `AppStore`/`dispatcher.ts` `openRepositoryFromUrl`).

### Citations

**File:** app/src/lib/parse-app-url.ts (L98-117)
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

```

**File:** app/src/lib/parse-app-url.ts (L118-124)
```typescript
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
