## Finding Assessment: Valid

### Title
Unvalidated GitHub API `html_url` reaches `shell.openExternal` via `createCommitURL` - (File: `app/src/lib/commit-url.ts`)

### Summary
`createCommitURL` builds the "View on GitHub" URL by string-interpolating `gitHubRepository.htmlURL` with no scheme or host validation, and that value is ultimately handed to `shell.openExternal`. Because `htmlURL` is persisted verbatim from the `html_url` field of a GitHub/GHES API response, a malicious or compromised GHES server (or a MITM/proxy able to tamper with API responses) controls the string that ends up being opened by the OS shell.

### Finding Description
`createCommitURL` reads `gitHubRepository.htmlURL` as `baseURL` and returns `${baseURL}/commit/${SHA}` (or with a `#diff-` suffix) without ever checking that `baseURL` starts with `http://`/`https://` or matches the expected endpoint host: [1](#0-0) 

`htmlURL` itself is populated directly from the API's `html_url` field with no sanitization when a repository is upserted: [2](#0-1) 

and `IAPIRepository.html_url` is a plain untyped string coming straight off the wire from the configured API endpoint (dotcom or a GitHub Enterprise Server / proxy): [3](#0-2) 

The resulting `commitURL` is passed straight to `dispatcher.openInBrowser`, which is used identically in multiple call sites (`app.tsx`'s `onViewCommitOnGitHub`, `pull-request-files-changed.tsx`'s `onViewOnGitHub`): [4](#0-3) [5](#0-4) 

Following the chain toward the sink, the renderer's `openExternal` is a thin IPC proxy that forwards the string to the main process with no validation performed in the renderer: [6](#0-5) [7](#0-6) 

I was not able to fully inspect the main-process IPC handler registration for the `'open-external'` channel (in `app/src/main-process/main.ts` / `ipc-main.ts`) within the tool budget available to confirm or rule out a scheme allowlist at that final hop; this remains unverified. However, none of the intermediate layers (`commit-url.ts`, `repositories-store.ts`, `app-shell.ts`, `main-process-proxy.ts`) perform any scheme check, so if the main-process handler simply calls Electron's `shell.openExternal(path)` unmodified, the invariant "only `http(s)` targets are opened externally" is broken at the `createCommitURL` layer regardless.

### Impact Explanation
If `htmlURL` is attacker-influenced (e.g., `file:///etc/passwd`, a custom registered protocol handler, or a Windows UNC-style path), `shell.openExternal` will hand that string to the OS shell. Depending on OS/registered handlers this can result in local file exposure (Explorer/Finder opening a directory) or, more seriously, invocation of a locally-registered custom URI scheme handler that a vulnerable installed application handles unsafely (a well-documented `shell.openExternal` risk class in Electron apps).

### Likelihood Explanation
This requires the attacker to control the API response for `html_url` on the endpoint the user's account is registered against — realistic for a malicious/compromised GitHub Enterprise Server the user has added an account for, or a network position able to tamper with that specific endpoint's API responses (both are within the stated valid-impact scope of "attacker controls...a GitHub API object" / "a git remote/proxy response"). It further requires the user to click "View on GitHub"/"View on GitHub Enterprise" for a commit — a normal, expected user action, not an unnatural step.

### Recommendation
Validate `baseURL`'s scheme (must be `http:`/`https:`) and ideally that its origin matches the account's configured endpoint before constructing the URL in `createCommitURL`, and/or enforce an `http(s)`-only allowlist at the final `shell.openExternal` call site in the main process as defense in depth.

### Proof of Concept
```ts
// app/src/lib/commit-url.ts
const fixture: GitHubRepository = {
  ...otherRequiredFields,
  htmlURL: 'file:///etc/passwd',
}

const url = createCommitURL(fixture, 'deadbeef')
// url === 'file:///etc/passwd/commit/deadbeef' — scheme is passed through unchanged
```
This value flows unmodified to `dispatcher.openInBrowser` → `shell.openExternal` in both call sites shown above. [8](#0-7)

### Citations

**File:** app/src/lib/commit-url.ts (L1-26)
```typescript
import * as crypto from 'crypto'
import { GitHubRepository } from '../models/github-repository'

/** Method to create the url for viewing a commit on dotcom */
export function createCommitURL(
  gitHubRepository: GitHubRepository,
  SHA: string,
  filePath?: string
): string | null {
  const baseURL = gitHubRepository.htmlURL

  if (baseURL === null) {
    return null
  }

  if (filePath === undefined) {
    return `${baseURL}/commit/${SHA}`
  }

  const fileHash = crypto.createHash('sha256').update(filePath).digest('hex')
  const fileSuffix = '#diff-' + fileHash

  return `${baseURL}/commit/${SHA}${fileSuffix}`
}


```

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

**File:** app/src/lib/api.ts (L149-161)
```typescript
export interface IAPIRepository {
  readonly clone_url: string
  readonly ssh_url: string
  readonly html_url: string
  readonly name: string
  readonly owner: IAPIIdentity
  readonly private: boolean
  readonly fork: boolean
  readonly default_branch: string
  readonly pushed_at: string
  readonly has_issues: boolean
  readonly archived: boolean
}
```

**File:** app/src/ui/app.tsx (L4082-4093)
```typescript
    const commitURL = createCommitURL(
      repository.gitHubRepository,
      SHA,
      filePath
    )

    if (commitURL === null) {
      return
    }

    this.props.dispatcher.openInBrowser(commitURL)
  }
```

**File:** app/src/ui/open-pull-request/pull-request-files-changed.tsx (L141-152)
```typescript
    const commitURL = createCommitURL(
      gitHubRepository,
      nonLocalCommitSHA,
      file.path
    )

    if (commitURL === null) {
      return
    }

    dispatcher.openInBrowser(commitURL)
  }
```

**File:** app/src/ui/main-process-proxy.ts (L146-146)
```typescript
export const openExternal = invokeProxy('open-external', 1)
```

**File:** app/src/lib/app-shell.ts (L1-15)
```typescript
import { shell as electronShell } from 'electron'
import * as Path from 'path'

import { Repository } from '../models/repository'
import {
  showItemInFolder,
  showFolderContents,
  openExternal,
  moveItemToTrash,
} from '../ui/main-process-proxy'

export interface IAppShell {
  readonly moveItemToTrash: (path: string) => Promise<void>
  readonly beep: () => void
  readonly openExternal: (path: string) => Promise<boolean>
```
