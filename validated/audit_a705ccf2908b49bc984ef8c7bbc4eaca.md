Based on the investigation, the strongest analog to the Blast_Adapter bug class (attacker-influenced routing that bypasses the expected validated path, letting an unexpected/unsanitized value flow into a sensitive operation) is the `filepath` query parameter in `parseAppURL`, used by the `x-github-client://` / `github-mac://` / `github-windows://` deep-link handlers.

### Title
Deep-link `filepath` parameter is not sanitized against path traversal - (File: `app/src/lib/parse-app-url.ts`)

### Summary
`parseAppURL` validates the `branch` and `pr` query parameters of an `openRepo` deep link with dedicated regex/character checks, but the `filepath` parameter is extracted and passed through with no validation whatsoever before it is forwarded to the dispatcher's `open-repository-from-url` handling.

### Finding Description
In `parseAppURL`, when the action is `openrepo`, the handler reads `pr`, `branch`, and `filepath` from the query string: [1](#0-0) 
`pr` is checked against `/^\d+$/` and `branch` is checked with `testForInvalidChars` (imported from `sanitize-ref-name.ts`), but `filepath` has no equivalent check — it is returned unmodified as part of the `IOpenRepositoryFromURLAction` object: [1](#0-0) 
The corresponding unit tests confirm this asymmetry: invalid `branch` and `pr` values are rejected, while `filepath` is only ever exercised with benign relative paths (e.g. `Octokit.Reactive/Octokit.Reactive.csproj`) and no traversal payload is tested: [2](#0-1) 
This action is dispatched from the OS-level protocol handler entry point (`open-url` on macOS, `--protocol-launcher` argument on Windows), meaning the URL is fully attacker-controlled if the user is lured into clicking a `github-mac://openRepo/...?filepath=...` or `x-github-client://openRepo/...` link: [3](#0-2) [4](#0-3) 
The `filepath` value subsequently flows into `dispatcher.ts`, which contains six references to `filepath` tied to the `open-repository-from-url` action handling.

### Impact Explanation
If the value that reaches the file-system-facing consumer in `dispatcher.ts` is joined with the repository path (e.g., to reveal/open the file after cloning) without normalization/containment checks, a value like `../../../../Users/victim/.ssh/id_rsa` or `..\\..\\AppData\\...` could cause Desktop to open or reveal a file located outside the cloned repository, satisfying the "read outside the repo via a link the user clicks" impact class. I was not able to fully trace and confirm the exact sink expression in `dispatcher.ts` (the file path join / `shell.showItemInFolder` or editor-open call) within the available tool budget, so the severity of the eventual sink (mere UI reveal vs. arbitrary read) is unconfirmed and should be verified directly against that file.

### Likelihood Explanation
The attacker primitive matches the accepted class exactly: a crafted deep link that the user clicks, requiring no local access, admin rights, or pre-existing malware. The missing validation is asymmetric and clearly demonstrable — `branch` and `pr` are sanitized in the same function while `filepath` is not, indicating this is an overlooked case rather than an intentional design decision.

### Recommendation
Apply the same character/path validation used for `branch` (via `testForInvalidChars`/`sanitize-ref-name.ts`) to `filepath`, and additionally reject any value containing `..` path segments or resolving outside the target repository root before it is used to construct a file-system path in `dispatcher.ts`.

### Proof of Concept
1. Register/observe that Desktop handles `github-mac://` (macOS) or `x-github-client://` protocol URLs via `app.on('open-url', ...)` / `--protocol-launcher`. [5](#0-4) 
2. Host a link: `github-mac://openRepo/https://github.com/attacker/repo?branch=main&filepath=..%2F..%2F..%2F..%2F.ssh%2Fid_rsa`.
3. `parseAppURL` accepts this because `filepath` bypasses all validation, unlike `branch`: [1](#0-0) 
4. The `open-repository-from-url` action is forwarded to the dispatcher for handling (confirmed via references in `dispatcher.ts`); the exact file-system operation performed with `filepath` needs to be verified directly in that file to confirm the final read/reveal impact.

**Note on confidence**: This analog is grounded in confirmed, unvalidated attacker-controlled input reaching a privileged-action dispatcher via an OS-level protocol handler. However, I could not verify within the available tool calls the precise line(s) in `app/src/ui/dispatcher/dispatcher.ts` where `filepath` is consumed to confirm whether existing guards (e.g., path normalization elsewhere) mitigate traversal. This should be verified with a full read of `dispatcher.ts`'s `open-repository-from-url` handling before treating this as fully confirmed.

### Citations

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

**File:** app/src/main-process/main.ts (L204-210)
```typescript
app.on('will-finish-launching', () => {
  // macOS only
  app.on('open-url', (event, url) => {
    event.preventDefault()
    handleAppURL(url)
  })
})
```

**File:** app/src/main-process/main.ts (L248-280)
```typescript
  if (__WIN32__ && args['protocol-launcher'] === true) {
    // On Windows we'll end up getting called with something like
    // `--protocol-launcher --allow-file-access-from-files x-github-client://..`
    // which minimist naturally interprets as
    // `--allow-file-access-from-files=x:/github-client`. This is due to
    // Chromium's hot take on parsing command line arguments, see:
    // https://github.com/electron/electron/issues/20322#issuecomment-534137321
    // So while we could add '--allow-file...' as a boolean we can't know for
    // sure that Chromium won't add more switches later on which is why we have
    // to resort to looking through all arguments looking for something that
    // appears to be an app url.
    const prefixes = Array.from(possibleProtocols, p => `${p}://`)
    const matchingUrl = argv.find(arg => {
      if (prefixes.some(p => arg.startsWith(p))) {
        try {
          new URL(arg)
          return true
        } catch (e) {
          log.error(`Unable to parse argument as URL: ${arg}`)
        }
      }
      return false
    })

    if (matchingUrl) {
      handleAppURL(matchingUrl)
    } else {
      log.error(`Encountered --protocol-launcher without app url`)
    }
    // If --protocol-launcher is present we always want to bail and not
    // risk a smuggled cli switch
    return
  }
```
