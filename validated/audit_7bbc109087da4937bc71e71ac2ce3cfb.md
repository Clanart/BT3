Based on the investigation, I found a concrete asymmetry in validation of the `x-github-client://openRepo/...` deep-link handler that is directly analogous to the report's "unvalidated/uncontrolled queued action processed without proper access or input control" pattern — here the untrusted `filepath` query parameter from an attacker-controlled deep link is *not* validated the way sibling parameters (`branch`, `pr`) are.

### Title
Deep-link `openRepo` action allows unsanitized `filepath` parameter to bypass validation applied to other fields - (File: app/src/lib/parse-app-url.ts)

### Summary
The `x-github-client://openRepo/...` protocol handler parses attacker-controlled URL parameters (`branch`, `pr`, `filepath`) in `parseAppURL` [1](#0-0) . `branch` is checked with `testForInvalidChars` and `pr` is checked against a strict `^\d+$` regex, but `filepath` is passed through with no validation whatsoever before being attached to the `IOpenRepositoryFromURLAction` payload [2](#0-1) .

### Finding Description
`handleAppURL` in the main process forwards any externally supplied protocol URL directly into `parseAppURL`, and the resulting action is sent over IPC to the renderer via `window.sendURLAction(action)` [3](#0-2) . The renderer dispatches it through `dispatcher.dispatchURLAction`, which for `open-repository-from-url` calls `this.openRepositoryFromUrl(action)` [4](#0-3) . This flow is triggered purely by the user clicking a link (e.g., a malicious "Open in Desktop" button on a webpage or a crafted `x-github-client://` link) — no local access or prior compromise is required.

Unlike `branch` (validated with `testForInvalidChars`, rejecting characters used in ref-injection/traversal) and `pr` (validated as a plain integer), the `filepath` value flows into the action object completely unchecked [5](#0-4) . The test suite confirms this: valid `filepath` values are accepted with arbitrary path separators, but there is no test (and no code) that rejects `../` sequences, absolute paths, or drive letters [6](#0-5) .

### Impact Explanation
If the downstream consumer of `filepath` (the file-open step of the "Open in Desktop" flow) joins this value onto the repository's working directory without re-validating for traversal sequences or absolute-path escapes, an attacker who controls the deep link content (e.g. embedding it in a GitHub-rendered page, a forked repo's README, or a standalone webpage) can cause Desktop to open/read a file outside the cloned repository's directory boundary — matching the report's "attacker controls a link/deep link the user clicks" and "file read outside the repo" impact classes.

### Likelihood Explanation
The `openRepo` action is a first-class, publicly documented Desktop protocol handler intended to be triggered from web pages ("Open in Desktop" buttons), so the attack surface is directly reachable by any external site or link the user clicks, with no privilege or local access required. The inconsistency (strict validation on `branch`/`pr`, none on `filepath`) strongly suggests this parameter was overlooked when the sanitization was added to the other fields.

### Recommendation
Apply the same (or a path-traversal-specific) validation to `filepath` in `parseAppURL` that is already applied to `branch`, e.g., reject values containing `..`, path separators leading outside the expected relative structure, or absolute path indicators, before constructing `IOpenRepositoryFromURLAction`. Additionally, whichever code ultimately resolves `filepath` against the repository path should use a hardened join (e.g., verifying the resolved path stays within the repository root) as defense in depth.

### Proof of Concept
1. Host a page (or GitHub markdown) with a link:
   `x-github-client://openRepo/https://github.com/octokit/octokit.net?branch=master&filepath=..%2F..%2F..%2F..%2F..%2Fsome%2Fsensitive%2Ffile`
2. User clicks the link; Desktop's protocol handler parses it via `parseAppURL`, which accepts the `filepath` value unmodified since only `branch`/`pr` are validated [7](#0-6) .
3. `dispatchURLAction` → `openRepositoryFromUrl` proceeds using this attacker-controlled `filepath`.

**Note on verification limits:** I was not able to fully trace, within the available tool budget, the exact downstream code that consumes `action.filepath` after `openRepositoryFromUrl` (i.e., the precise file-open/join call). The vulnerability described here is confirmed at the parsing/validation layer (`parse-app-url.ts`), but confirming the exact sink and whether any later path-containment check exists would require further inspection — recommend a full Devin session with repository access to trace `openRepositoryFromUrl`'s complete implementation and confirm exploitability end-to-end.

### Citations

**File:** app/src/lib/parse-app-url.ts (L21-24)
```typescript

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

**File:** app/src/ui/dispatcher/dispatcher.ts (L2118-2120)
```typescript
      case 'open-repository-from-url':
        this.openRepositoryFromUrl(action)
        break
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
