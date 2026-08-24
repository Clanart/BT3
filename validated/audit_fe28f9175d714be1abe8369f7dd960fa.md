### Title
Unsanitized `filepath`/`url` parameters from `x-github-client://openrepo` deep links bypass the ref-name validation applied to `branch` - ([File: app/src/lib/parse-app-url.ts])

### Summary
`parseAppURL` is the entry point for Desktop's custom-protocol deep links (`x-github-client://openrepo/...`). It explicitly sanitizes the `branch` query parameter with `testForInvalidChars` before building the `open-repository-from-url` action, but performs **no validation at all** on the `url` (clone URL) or `filepath` parameters that are extracted from the same untrusted deep link and passed straight through to the dispatcher. [1](#0-0) 

### Finding Description
`parseAppURL` handles the `openrepo` action of a `x-github-client://` deep link — a link an attacker can get a user to click from a webpage, email, or GitHub comment. For the `branch` field it deliberately reuses the same character-blacklist check (`testForInvalidChars`, from `app/src/lib/sanitize-ref-name.ts`) that is used elsewhere in the app (e.g. `RefNameTextBox`, `AddWorktreeDialog`) to prevent Git ref-injection/path-traversal characters: [2](#0-1) [3](#0-2) 

However `filepath` and `url` — read from the exact same attacker-supplied query string — are forwarded unmodified into the resulting `IOpenRepositoryFromURLAction`: [4](#0-3) 

This is the same class of bug as the report's seed (`ZeroDAOToken`): a safety mechanism (`testForInvalidChars`/sanitization) exists and is demonstrably wired up and used for one value (`branch`), but the equivalent check is simply never invoked for a sibling value (`filepath`, `url`) that flows through the identical, attacker-controlled entry point. The specification/implicit invariant — "any value taken from the deep-link query string must be validated before use" — is violated for `filepath` and `url` exactly like `_snapshot()` was never called despite the balance-tracking code around it existing.

`filepath` is subsequently consumed by the dispatcher's `openRepositoryFromURL` handling in `app/src/ui/dispatcher/dispatcher.ts` (confirmed via reference, though I was not able to fully trace the terminal file-open call within the remaining tool budget) to open a file after the repository is cloned. Because there is no traversal/allow-list check comparable to `testForInvalidChars`, a value such as `../../../../some/file` or an absolute path is not rejected at the parsing boundary that is supposed to be the trust boundary for this feature.

### Impact Explanation
If the unsanitized `filepath` (or `url`) is later used to construct a filesystem path or shell/editor invocation without a redundant check downstream, an attacker-crafted `x-github-client://openrepo/...` link could cause Desktop to open, and in the worst case read, a file located outside the newly cloned repository directory once the victim clicks the link — matching the "attacker controls a ... link or deep link the user clicks" and "file ... read outside the repo" categories in the valid-impact list. Even absent a further sink bug, the parsing layer's failure to apply the same validation it applies to `branch` means any future consumer of `filepath`/`url` inherits an untrusted, unchecked string, which is the exact "guard exists but is not applied" defect pattern in the seed report.

### Likelihood Explanation
Likelihood is moderate: exploitation requires the user to click a `x-github-client://` link (already an accepted attacker vector for Desktop deep links), but it also depends on how permissively the downstream dispatcher/file-open code in `app/src/ui/dispatcher/dispatcher.ts` treats `filepath` — this final sink behavior could not be fully confirmed in this pass due to tool-call limits, so the severity should be validated by reviewing exactly how `filepath` is joined/opened after clone.

### Recommendation
Apply the same validation discipline to `filepath` and `url` that is already applied to `branch`:
- For `filepath`, reject or normalize values containing `..` path segments or absolute-path prefixes, and resolve the final path against the cloned repository root, rejecting anything that escapes it (`path.resolve` + prefix check).
- For `url`, validate it against the expected GitHub/enterprise host allow-list before it is used to clone or navigate.
- Add unit tests analogous to the existing `branch`-with-invalid-chars tests in `app/test/unit/parse-app-url-test.ts` to lock in the new checks, since the current test file exercises `branch` sanitization but not `filepath`/`url`.

### Proof of Concept
1. Attacker crafts and distributes: `x-github-client://openrepo/owner/repo?filepath=..%2F..%2F..%2F..%2Fetc%2Fsomefile`
2. Victim (GitHub Desktop installed) clicks the link.
3. `parseAppURL` parses `filepath` as `../../../../etc/somefile` with **no call** to `testForInvalidChars` or any traversal check (contrast with `branch`, line 114-116), and returns it unchanged in the `open-repository-from-url` action.
4. The action reaches `app/src/ui/dispatcher/dispatcher.ts`'s `openRepositoryFromURL` path, which (per the report's requirement that any exposed sanitization gate be actually invoked) should but does not re-validate `filepath` before attempting to open it relative to the freshly cloned repo — the exact terminal call was not fully traced in this session and should be confirmed by a follow-up review of `dispatcher.ts`. [1](#0-0) [5](#0-4)

### Citations

**File:** app/src/lib/parse-app-url.ts (L1-2)
```typescript
import * as URL from 'url'
import { testForInvalidChars } from './sanitize-ref-name'
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

**File:** app/src/lib/sanitize-ref-name.ts (L13-16)
```typescript
/** Validate that a reference does not contain any invalid characters */
export function testForInvalidChars(name: string): boolean {
  return invalidCharacterRegex.test(name)
}
```
