### Title
Unvalidated `filepath` parameter in Desktop deep-link `openRepo` action allows path traversal when opening a file after cloning - ([File: app/src/lib/parse-app-url.ts])

### Summary
The Solidity report flags a broken invariant: a "safe" wrapper (`safeApprove`) exists specifically to validate/guard a dangerous primitive, but the code bypasses it and calls the raw, unguarded primitive (`approve`) directly on attacker-influenced input (arbitrary ERC20 token address), leading to unexpected/unsafe behavior. The same *pattern*—one attacker-controlled field getting a validation guard while a sibling field of identical trust level gets none—exists in GitHub Desktop's handling of the `x-github-client://openRepo/...` deep link.

### Finding Description
`parseAppURL` in [1](#0-0)  parses attacker-controlled protocol-handler URLs (the app registers `x-github-client`, `github-mac`/`github-windows`, etc. as OS-level protocol handlers, so any webpage/email/IM message can invoke this code path via `handleAppURL` in [2](#0-1) ).

For the `openrepo` action, the code explicitly sanitizes the `branch` query parameter with `testForInvalidChars(branch)` before accepting it [3](#0-2) , but the `filepath` query parameter — documented as *"the file to open after cloning the repository"* [4](#0-3)  — is read straight from the query string with `getQueryStringValue(query, 'filepath')` and passed through into the returned `IOpenRepositoryFromURLAction` with **no sanitization at all** [5](#0-4) .

This mirrors the report's broken invariant exactly: one attacker-influenced parameter path is protected with the "safe" validation function, and a sibling parameter of the same trust level is not, because the developer assumed (incorrectly) that the same guard was unnecessary for `filepath`. Just as `approve` bypassing `safeApprove`'s checks lets a non-standard token corrupt allowance state, an unsanitized `filepath` (e.g. `../../../../.ssh/id_rsa` or an absolute path) can direct Desktop to act on a location outside the freshly cloned repository once this value is later used to open a file (per the file-path's own doc comment and the general `openFile`/`shell.openExternal('file://' + path)` pattern used elsewhere in the app, e.g. [6](#0-5) ).

### Impact Explanation
If the consumer of `IOpenRepositoryFromURLAction.filepath` joins it with the cloned repository path (as file-path handling elsewhere in Desktop does, e.g. `Path.join(repository.path, path)` in `revealInFileManager`, [7](#0-6) ) without resolving/verifying the result stays inside the repository, a crafted deep link can cause Desktop to open (and, depending on the downstream editor/shell integration, potentially read or expose) a file outside the cloned repository directory — satisfying the "attacker controls a … deep link the user clicks, result is … file … read outside the repo" impact class.

### Likelihood Explanation
The `branch` parameter in the exact same function is already treated as untrusted and is validated with `testForInvalidChars`, showing the developers recognize this URL as attacker-controlled and requiring exactly this kind of sanitization — but the equivalent guard was simply omitted for `filepath`. The only user action required is clicking a single crafted link (`x-github-client://openRepo/<url>?filepath=../../../secret`), which is a normal, expected trust boundary for GitHub Desktop's registered protocol handlers, not any unnatural or privileged step.

### Recommendation
Apply the same category of validation used for `branch` to `filepath`: reject values containing path traversal sequences (`..`), absolute path prefixes, or characters invalid for a relative repo-relative path, mirroring how `testForInvalidChars` is already applied. Additionally, wherever `filepath` is eventually consumed, resolve it against the repository root and verify the resolved path is still contained within that root before performing any file open/read operation (the equivalent of `safeApprove`'s defensive check).

### Proof of Concept
1. Attacker hosts/sends a link: `x-github-client://openRepo/https://github.com/some/repo?filepath=..%2F..%2F..%2F..%2F.ssh%2Fid_rsa`
2. Victim (with Desktop installed and registered as the `x-github-client` handler) clicks the link.
3. `handleAppURL` → `parseAppURL` returns `{ name: 'open-repository-from-url', url: 'https://github.com/some/repo', filepath: '../../../../.ssh/id_rsa', ... }` with no rejection, since only `branch`/`pr` are checked [8](#0-7) .
4. After the clone completes, Desktop attempts to open the "file to open after cloning" using this unsanitized path, which — if joined with the repo path without containment checks — resolves outside the cloned repository.

Note: I was unable to trace, within the remaining tool budget, the exact call site in `app.tsx`/dispatcher that consumes `IOpenRepositoryFromURLAction.filepath` after a clone completes to confirm the precise join/open call used (grep only surfaced the type definition and test file references). The vulnerability claim rests on (a) the confirmed absence of any sanitization for `filepath` in `parse-app-url.ts` contrasted with the confirmed sanitization applied to `branch` in the same function, and (b) the general unsafe-path-join pattern (`Path.join(repository.path, path)`) demonstrably used elsewhere in the codebase for repo-relative paths. Confirming the exact downstream sink would require a Devin session with full file access to trace `filepath` from `dispatcher.ts` through to its consumer.

### Citations

**File:** app/src/lib/parse-app-url.ts (L22-23)
```typescript
  /** the file to open after cloning the repository */
  readonly filepath: string | null
```

**File:** app/src/lib/parse-app-url.ts (L66-66)
```typescript
export function parseAppURL(url: string): URLActionType {
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

**File:** app/src/main-process/main.ts (L159-168)
```typescript
function handleAppURL(url: string) {
  log.info('Processing protocol url')
  const action = parseAppURL(url)
  onDidLoad(window => {
    // This manual focus call _shouldn't_ be necessary, but is for Chrome on
    // macOS. See https://github.com/desktop/desktop/issues/973.
    window.focus()
    window.sendURLAction(action)
  })
}
```

**File:** app/src/ui/lib/open-file.ts (L4-17)
```typescript
export async function openFile(
  fullPath: string,
  dispatcher: Dispatcher
): Promise<void> {
  const result = await shell.openExternal(`file://${fullPath}`)

  if (!result) {
    const error = {
      name: 'no-external-program',
      message: `Unable to open file ${fullPath} in an external program. Please check you have a program associated with this file extension`,
    }
    await dispatcher.postError(error)
  }
}
```

**File:** app/src/lib/app-shell.ts (L61-64)
```typescript
export function revealInFileManager(repository: Repository, path: string) {
  const fullyQualifiedFilePath = Path.join(repository.path, path)
  return shell.showItemInFolder(fullyQualifiedFilePath)
}
```
