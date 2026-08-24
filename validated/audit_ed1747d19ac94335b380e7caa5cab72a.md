Based on the code I traced, this is a valid finding, though I could not fully confirm whether `main-process/main.ts`'s IPC handler for `openExternal` performs its own scheme validation (index coverage did not let me read its full body) — this remains an area of uncertainty. I'll present the finding with that caveat.

### Title
Unvalidated `html_url` from GitHub API repository objects stored verbatim and reachable by `shell.openExternal` via `LinkButton` - (File: app/src/lib/stores/repositories-store.ts)

### Summary
`_upsertGitHubRepository` persists `gitHubRepository.html_url` directly into `IDatabaseGitHubRepository.htmlURL` with no scheme/protocol validation. This value later flows into `GitHubRepository.htmlURL` and is rendered by UI components as a `uri` prop passed to `LinkButton`, whose click handler forwards the raw string unconditionally to `shell.openExternal`.

### Finding Description
In `_upsertGitHubRepository`, the API-provided `html_url` is copied straight into the database record with no allow-listing of `http`/`https` schemes: [1](#0-0) 

`LinkButton`'s click handler takes its `uri` prop and passes it unfiltered to `shell.openExternal(uri)`: [2](#0-1) 

The `shell` used by `LinkButton` is `app-shell.ts`'s `shell.openExternal`, which is a thin re-export of an IPC-proxied `openExternal` function with no scheme check visible at this layer: [3](#0-2) 

Because `_upsertGitHubRepository` never validates that `html_url` begins with `https://` (or `http://`), a GitHub API response (or a malicious/compromised GHES server, which is in-scope as "a GitHub API object") containing `html_url: "x-github-client://..."`, `javascript:...`, or `file:...` would be stored as-is and returned as `GitHubRepository.htmlURL`, becoming available to any UI code path that renders that field as a clickable link (e.g., repository settings/about links using `LinkButton`).

### Impact Explanation
If a UI surface renders `GitHubRepository.htmlURL` through `LinkButton` (or any other component that forwards it to `shell.openExternal`) without its own scheme check, clicking the link would ask the OS shell to handle an arbitrary URI. For custom schemes this can invoke arbitrary OS-registered protocol handlers, a known class of Electron `shell.openExternal` risk (potential command execution depending on installed handlers/OS). `javascript:`/`file:` will generally be safely no-op'd by `shell.openExternal`, but arbitrary custom deep-link schemes registered by other installed applications represent the concrete escalation vector.

### Likelihood Explanation
Moderate-to-low. This requires either a malicious/compromised GitHub Enterprise Server endpoint or a MITM'd/malformed dotcom API response supplying a crafted `html_url`, and it further requires the user to click the rendered link in a UI surface that uses `LinkButton` with that field. I was not able to confirm within the available index whether the IPC-side `openExternal` handler in `app/src/main-process/main.ts` (referenced via `app/src/ui/main-process-proxy.ts`) enforces its own `http(s)`-only allow-list before calling Electron's `shell.openExternal`; if such a check exists there, the practical exploitability of this particular gap is neutralized at that later layer, though the missing validation at persistence time (`repositories-store.ts:659`) remains a defense-in-depth gap.

### Recommendation
Validate `html_url` (and `clone_url`) against an `https://`/`http://` scheme allow-list in `_upsertGitHubRepository` before persisting to `IDatabaseGitHubRepository`, rejecting or nulling out non-conforming values. Additionally, add a scheme check in `LinkButton.onClick` (or centrally in `app-shell.ts`'s `openExternal` wrapper) so any current or future untrusted-string-to-URI path is defended even if upstream validation is missed.

### Proof of Concept
```ts
// stub IAPIFullRepository with a malicious html_url
const maliciousApiRepo: IAPIFullRepository = {
  ...baseApiRepo,
  html_url: 'x-github-client://open?url=...',
}

const ghRepo = await repositoriesStore._upsertGitHubRepository(endpoint, maliciousApiRepo)
assert(ghRepo.htmlURL === 'x-github-client://open?url=...') // stored unchanged, no scheme filtering
```
This `htmlURL` value is then usable anywhere the app renders `<LinkButton uri={repository.htmlURL}>`, which on click calls `shell.openExternal(uri)` with the unvalidated string: [4](#0-3)

### Citations

**File:** app/src/lib/stores/repositories-store.ts (L654-666)
```typescript
    const updatedGitHubRepo: IDatabaseGitHubRepository = {
      ...(existingRepo?.id !== undefined && { id: existingRepo.id }),
      ownerID: owner.id,
      name: gitHubRepository.name,
      private: gitHubRepository.private,
      htmlURL: gitHubRepository.html_url,
      cloneURL: gitHubRepository.clone_url,
      parentID,
      lastPruneDate: existingRepo?.lastPruneDate ?? null,
      issuesEnabled: gitHubRepository.has_issues,
      isArchived: gitHubRepository.archived,
      permissions,
    }
```

**File:** app/src/ui/lib/link-button.tsx (L76-92)
```typescript
  private onClick = (event: React.MouseEvent<HTMLAnchorElement>) => {
    event.preventDefault()

    if (this.props.disabled) {
      return
    }

    const uri = this.props.uri
    if (uri) {
      shell.openExternal(uri)
    }

    const onClick = this.props.onClick
    if (onClick) {
      onClick()
    }
  }
```

**File:** app/src/lib/app-shell.ts (L43-53)
```typescript
export const shell: IAppShell = {
  // Since Electron 13, shell.trashItem doesn't work from the renderer process
  // on Windows. Therefore, we must invoke it from the main process. See
  // https://github.com/electron/electron/issues/29598
  moveItemToTrash,
  beep: electronShell.beep,
  openExternal,
  showItemInFolder,
  showFolderContents,
  openPath: electronShell.openPath,
}
```
