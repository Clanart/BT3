### Title
Unvalidated `filepath` parameter in `x-github-client://openRepo` deep links enables path traversal outside the cloned repository - (File: app/src/lib/parse-app-url.ts)

### Summary
`parseAppURL` validates the `branch` and `pr` query parameters of an `openRepo` deep link but performs **no validation whatsoever** on the `filepath` parameter before returning it as part of `IOpenRepositoryFromURLAction`, which is later consumed by the dispatcher to open a file after cloning/opening the referenced repository.

### Finding Description
`parseAppURL` in `app/src/lib/parse-app-url.ts` handles the `openRepo` deep-link action. For the `pr` field it enforces a strict digit-only regex, and for `branch` it calls `testForInvalidChars` to reject unsafe ref names: [1](#0-0) 

However, immediately after, `filepath` is read straight off the query string via `getQueryStringValue` and passed through untouched into the returned `IOpenRepositoryFromURLAction`, with no sanitization, no traversal check (e.g. `../`), and no restriction ensuring the value resolves to a path inside the cloned repository: [2](#0-1) 

This is the same bug class as the report's core finding: a value that flows from an untrusted/attacker-controlled input into a sensitive downstream operation is validated inconsistently — some sibling parameters (`branch`, `pr`) are checked, but a semantically similar one (`filepath`) is not, allowing the "invariant" (that the deep link can only reference files inside the just-cloned repository) to be silently broken. The `filepath` field is subsequently consumed by `app/src/ui/dispatcher/dispatcher.ts` (multiple references found there) to open a file once the "Open in Desktop" flow completes; because the value is unsanitized at the parsing boundary, a value like `../../../../Users/victim/.ssh/id_rsa` or an absolute path could reach the file-open sink unmodified.

I was not able to fully confirm, within the available tool budget, exactly how `dispatcher.ts` joins/opens `filepath` (e.g., whether it's concatenated with the repository root via a path-join that a `../` sequence could escape, or opened via `shell.openItem`/`shell.openPath`/`fs.readFile`). This is the key remaining unknown — the vulnerability's ultimate severity (arbitrary file read/execution vs. cosmetic path confusion) depends on that sink's implementation, which should be reviewed directly in `app/src/ui/dispatcher/dispatcher.ts`.

### Impact Explanation
If the downstream consumer in `dispatcher.ts` joins `filepath` with the repository path without normalizing/containing it (e.g. `path.join(repoPath, filepath)` without a post-join containment check), a malicious `x-github-client://openRepo/...?filepath=..%2F..%2F..%2Fsecrets.txt` link clicked by the victim (e.g. embedded in a webpage, README, or issue) could cause Desktop to open/read a file outside the cloned repository, potentially exfiltrating or exposing sensitive local files. This matches the "attacker controls...a link or deep link the user clicks" and "file write or read outside the repo" categories called out as valid impact.

### Likelihood Explanation
Likelihood is moderate: it requires the victim to click a crafted deep link (a single click, no other unnatural steps), which is consistent with Desktop's documented protocol-handler flow for "Open in Desktop" buttons on github.com and other sites. Unlike `branch`/`pr`, no validation gate exists for `filepath`, so exploitation doesn't require bypassing any check — only crafting the query string.

### Recommendation
Add validation to `filepath` in `parse-app-url.ts` analogous to the existing `branch`/`pr` checks: reject values containing `..` path segments, reject absolute paths, and/or resolve the path against the intended repository root and verify containment before returning it in `IOpenRepositoryFromURLAction`. Additionally, audit the consumer in `app/src/ui/dispatcher/dispatcher.ts` to ensure it re-validates/normalizes `filepath` and confirms the resolved path stays within the repository directory before performing any file operation.

### Proof of Concept
1. Attacker hosts a page (or GitHub-rendered content) with a link:
   `x-github-client://openRepo/https://github.com/some/repo?branch=main&filepath=..%2F..%2F..%2F..%2F.ssh%2Fid_rsa`
2. Victim (with GitHub Desktop registered as the protocol handler) clicks the link.
3. `parseAppURL` accepts the URL because `branch=main` passes `testForInvalidChars`, and `filepath` is returned unchecked: [2](#0-1) 
4. Desktop clones/opens the target repository and then attempts to open the attacker-supplied `filepath`, which — if the sink in `dispatcher.ts` does not enforce containment — resolves outside the cloned repository.

**Verification gap**: exact behavior of the `filepath` consumer in `app/src/ui/dispatcher/dispatcher.ts` (path-join logic, containment checks, or absence thereof) could not be fully retrieved in this session; this should be reviewed directly to confirm exploitability and finalize impact severity.

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

**File:** app/src/lib/parse-app-url.ts (L118-125)
```typescript
    return {
      name: 'open-repository-from-url',
      url: parsedPath,
      branch,
      pr,
      filepath,
    }
  }
```
