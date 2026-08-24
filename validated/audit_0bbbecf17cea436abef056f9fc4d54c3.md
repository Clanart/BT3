Based on `app/src/lib/parse-app-url.ts`, the `openrepo` deep-link handler validates `branch` with `testForInvalidChars` but does not apply any equivalent validation to `filepath` before including it in the returned `IOpenRepositoryFromURLAction`.

### Title
Missing validation of `filepath` in `openrepo` deep link allows path-traversal/absolute-path values to reach `IOpenRepositoryFromURLAction` - (File: `app/src/lib/parse-app-url.ts`)

### Summary
In `parseAppURL`, when handling the `openrepo` action, `branch` is checked against `testForInvalidChars` and the action is discarded (`return unknown`) if the check fails, but `filepath` is extracted from the query string and passed straight through into the returned action object with no equivalent character/path validation.

### Finding Description
`parseAppURL` reads `pr`, `branch`, and `filepath` from the query string of an `x-github-client://openrepo/...` URL. [1](#0-0) 
`pr` is validated as digits-only, and `branch` is validated with `testForInvalidChars` (imported from `./sanitize-ref-name`), causing the whole action to be rejected (`unknown`) on failure. `filepath`, however, is read via `getQueryStringValue(query, 'filepath')` and returned unmodified in the `IOpenRepositoryFromURLAction.filepath` field with no character filtering, no `..`/absolute-path rejection, and no length limit: [2](#0-1) 
The existing test suite for this parser confirms `filepath` values are accepted verbatim (e.g., `Octokit.Reactive/Octokit.Reactive.csproj`), and no test exercises rejection of `../`-containing or absolute `filepath` values, consistent with there being no such check in the implementation. [3](#0-2) 

I attempted to trace the downstream consumer of `IOpenRepositoryFromURLAction.filepath` (referenced in `app/src/ui/dispatcher/dispatcher.ts`, which has matches for `filepath`) to confirm exactly how this value is later joined with the repository path and opened/revealed, but I was not able to retrieve the relevant lines of `dispatcher.ts` in this session due to a tool limitation. This means I can confirm the **parsing-layer gap** (the asymmetry between `branch` and `filepath` validation) with certainty, but I cannot confirm from code alone whether the downstream consumer performs its own containment check (e.g., verifying the resolved path stays inside the repository root) before opening/revealing the file. If such a check exists downstream, it would mitigate this specific gap; if it does not, the parsing-layer gap directly enables the described path-traversal.

### Impact Explanation
If the downstream consumer of `filepath` (in `dispatcher.ts` / the "open file after clone" flow) does not independently re-validate the path before joining it with the repository directory and opening/revealing it, a value like `..%2F..%2F..%2Fetc%2Fpasswd` or an absolute path would let a clicked deep link cause Desktop to open/reveal a file outside the cloned repository, matching the "file-read-outside-repo" impact category in scope.

### Likelihood Explanation
Exploitability only requires the victim to click an attacker-supplied `x-github-client://openrepo/...` link, which is the standard trigger vector for this class of finding. The parser itself performs no rejection for such values, so the likelihood of the malicious `filepath` reaching the action object is high; the residual uncertainty is solely about the unverified downstream handling in `dispatcher.ts`.

### Recommendation
Apply the same (or a path-specific) validation to `filepath` as is applied to `branch`, e.g., reject values containing `..` path segments, reject absolute paths, and reject any characters caught by `testForInvalidChars` or a stricter path-safety check, before constructing `IOpenRepositoryFromURLAction`. Additionally, wherever `filepath` is consumed (in `dispatcher.ts`), resolve it against the repository root and verify the resolved absolute path still starts with the repository root path before performing any file open/reveal operation, as defense in depth.

### Proof of Concept
```ts
import { parseAppURL, IOpenRepositoryFromURLAction } from '../../src/lib/parse-app-url'

const result = parseAppURL(
  'x-github-client://openrepo/https://github.com/octokit/octokit.net?branch=master&filepath=..%2F..%2F..%2Fetc%2Fpasswd'
)

// Currently NOT rejected, unlike an invalid branch would be:
console.log(result.name) // 'open-repository-from-url' (expected 'unknown' if filepath were validated like branch)
console.log((result as IOpenRepositoryFromURLAction).filepath) // '../../../etc/passwd'
```

**Caveat**: this PoC demonstrates the confirmed parsing-layer gap only. Full end-to-end exploitation (actual file read/reveal outside the repo) depends on how `dispatcher.ts` consumes `filepath`, which I was unable to fully verify in this session — the user should confirm this by inspecting `app/src/ui/dispatcher/dispatcher.ts` where `filepath` is used, to see whether the resolved path is re-validated against the repository root before use.

### Citations

**File:** app/src/lib/parse-app-url.ts (L99-116)
```typescript
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

**File:** app/test/unit/parse-app-url-test.ts (L80-93)
```typescript
    it('adds file path if found', () => {
      const result = parseAppURL(
        'github-mac://openRepo/https://github.com/octokit/octokit.net?branch=master&filepath=Octokit.Reactive%2FOctokit.Reactive.csproj'
      )
      assert.equal(result.name, 'open-repository-from-url')

      const openRepo = result as IOpenRepositoryFromURLAction
      assert.equal(openRepo.url, 'https://github.com/octokit/octokit.net')
      assert.equal(openRepo.branch, 'master')
      assert.equal(
        openRepo.filepath,
        'Octokit.Reactive/Octokit.Reactive.csproj'
      )
    })
```
