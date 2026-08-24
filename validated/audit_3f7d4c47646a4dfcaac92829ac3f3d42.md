## Title
Path Traversal via Unvalidated `filepath` Deep-Link Parameter — (File: `app/src/lib/parse-app-url.ts`)

## Summary
The `filepath` query parameter parsed from `x-github-client://openrepo` deep links passes through `parseAppURL` completely unvalidated, unlike the `branch` and `pr` parameters, which are checked with `testForInvalidChars` / a numeric regex.

## Finding Description
In `parseAppURL`, when `actionName === 'openrepo'`, three query parameters are extracted via `getQueryStringValue`: `pr`, `branch`, and `filepath`. [1](#0-0) 

`pr` is validated against `/^\d+$/`, and `branch` is validated with `testForInvalidChars(branch)` (rejecting the action entirely if invalid). `filepath`, however, is taken directly from `getQueryStringValue(query, 'filepath')` with no sanitization, no traversal check (`../`), and no check for an absolute path — it is returned unmodified as `filepath` in the `IOpenRepositoryFromURLAction` object. [2](#0-1) [3](#0-2) 

This confirms the specific claim: a deep link such as `x-github-client://openrepo/owner/repo?filepath=..%2F..%2F..%2F.ssh%2Fid_rsa` will decode to `filepath: '../../../.ssh/id_rsa'` and be returned as-is in the parsed action object — no traversal or absolute-path guard exists at this layer.

## Impact Explanation
I was unable to fully trace, within the available tool budget, exactly how `Dispatcher` in `app/src/ui/dispatcher/dispatcher.ts` consumes this `filepath` value after cloning (e.g., whether it is joined with the repository root path using `path.join`/`path.resolve` and then passed to `shell.openPath`/`openFile` in `app/src/lib/app-shell.ts` or `app/src/ui/lib/open-file.ts`, and whether any normalization or containment check happens downstream). Grep confirmed `dispatcher.ts` references `filepath` multiple times and the codebase has file-opening sinks (`shell.openPath`, `app-shell.ts`, `open-file.ts`), but I could not confirm the exact join/validation logic at the sink in this session. Because of this, I cannot definitively confirm that the traversal-crafted `filepath` results in a file being opened outside the cloned repo — only that the parsing layer itself performs no validation, which is a necessary precondition for the reported bug but not sufficient proof of the full end-to-end impact without inspecting the sink.

## Likelihood Explanation
This is triggered purely via an unprivileged, attacker-crafted deep link (`x-github-client://openrepo/...?filepath=...`) that a user clicks — no local access or credentials required, matching the in-scope "clicked links/deep links" category.

## Recommendation
Add the same style of validation used for `branch` to `filepath` in `parse-app-url.ts` — reject or sanitize values containing `..` path segments or resolving to an absolute path outside the intended clone directory, and re-verify at the consumption site in `dispatcher.ts` (e.g., resolve `filepath` against the cloned repo root with `path.resolve` and confirm the result still starts with that root) before it's handed to any file-open API.

## Proof of Concept
```ts
import { parseAppURL } from '../../src/lib/parse-app-url'

const url =
  'x-github-client://openrepo/owner/repo?filepath=..%2F..%2F..%2F.ssh%2Fid_rsa'
const action = parseAppURL(url)
// action.filepath === '../../../.ssh/id_rsa'  (unmodified, no traversal check)
console.log(action)
```
This demonstrates the parser-level defect confirmed in `parse-app-url.ts`. Full confirmation of file-open impact requires inspecting the downstream consumption in `Dispatcher.openRepositoryFromUrl` and the actual file-open sink, which was not completed in this review.

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

**File:** app/src/lib/parse-app-url.ts (L50-64)
```typescript
function getQueryStringValue(
  query: ParsedUrlQueryWithUndefined,
  key: string
): string | null {
  const value = query[key]
  if (value == null) {
    return null
  }

  if (Array.isArray(value)) {
    return value[0]
  }

  return value
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
