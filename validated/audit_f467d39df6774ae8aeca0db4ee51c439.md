Based on my investigation, the strongest local analog to "anyone can call a privileged function" is the custom URL-protocol handler that GitHub Desktop registers with the OS. Unlike the vault's `notifyStrategyRemoved()`, which lacks a caller check, Desktop's IPC layer (`app/src/main-process/ipc-main.ts`) *does* enforce a "trusted sender" check for renderer-to-main messages via `isTrustedIPCSender` [1](#0-0) [2](#0-1) . However, the OS-level custom protocol handlers (`x-github-client://`, `x-github-desktop-auth://`, `github-mac://`, `github-windows://`) have no equivalent "who is allowed to call this" restriction — any external process, webpage, or email link on the system can invoke them, since they are registered globally with `app.setAsDefaultProtocolClient` [3](#0-2) [4](#0-3) .

### Title
Unrestricted OS protocol handler allows any external link to drive repository actions (open/clone/checkout) - (File: app/src/main-process/main.ts, app/src/lib/parse-app-url.ts, app/src/ui/dispatcher/dispatcher.ts)

### Summary
The `x-github-client://` (and legacy `github-mac://` / `github-windows://`) custom protocol is globally registered with the OS, so any application, webpage, or email a user interacts with can invoke it. When triggered, the raw URL is parsed with only light syntactic validation and dispatched straight into application logic that opens, clones, or manipulates repository state (including checking out a caller-specified branch/PR) — with no check on the identity or trustworthiness of whoever invoked the URL.

### Finding Description
`possibleProtocols` are registered as OS-wide protocol handlers [5](#0-4) . Incoming URLs (from the OS protocol launcher or command line) are handed to `handleAppURL`, which calls `parseAppURL` and forwards the resulting action straight to the renderer [6](#0-5) . `parseAppURL` validates only syntax — PR must be numeric, branch must match a ref pattern, filepath is checked later for traversal — but performs no check on *who* sent the URL nor whether the destination `url`/repository is one the user has any relationship with [7](#0-6) . The renderer's `dispatchURLAction` then routes `open-repository-from-url` actions to `openRepositoryFromUrl`, which — depending on the presence of `pr`/`branch` params supplied entirely by the untrusted caller — calls `openPullRequestFromUrl(url, pr)` or `openBranchNameFromUrl(url, branch)`, or otherwise clones/opens the repository [8](#0-7) . If a locally cloned repository already matches the attacker-supplied `url` (matched loosely by comparing `htmlURL`/parent `htmlURL`) [9](#0-8) , the app selects that existing repository and proceeds with the caller-specified PR/branch checkout rather than treating it as an entirely new/unverified destination.

I was not able to fully inspect the bodies of `openPullRequestFromUrl` and `openBranchNameFromUrl` within the available tool budget (only their call sites and signatures were confirmed) [10](#0-9) , so I cannot confirm with certainty whether they prompt the user before switching branches/checking out a PR ref in an already-open repository, or do so silently. That uncertainty limits how conclusively this maps to "silent corruption of what the user commits/pushes" versus "just opens a clone dialog requiring confirmation."

### Impact Explanation
If the branch/PR checkout path proceeds without an explicit confirmation step, a malicious webpage or email could craft an `x-github-client://openrepo/<victim-repo-url>?branch=<attacker-branch>` link. Clicking it (a normal, low-friction user action) could cause Desktop to silently switch a user's already-open working repository to an attacker-chosen branch or PR ref, potentially leading the user to build/run/commit on top of unreviewed, attacker-controlled code. This matches the "silent corruption of what the user commits or pushes" impact category, driven entirely by an unprivileged, unauthenticated attacker primitive (a clicked link).

### Likelihood Explanation
Likelihood is moderate: the protocol is a documented, always-on OS integration (used for the OAuth login flow and "Open in Desktop" buttons on GitHub.com), so it is reachable by any local process or web link without special permissions. However, exploitability hinges on details of `openPullRequestFromUrl`/`openBranchNameFromUrl` that I could not verify (e.g., whether the app prompts for confirmation before switching branches on an already-tracked repository).

### Recommendation
- Require explicit user confirmation before checking out a branch/PR supplied via an external protocol URL when acting on an already-open repository, rather than performing the checkout automatically.
- Constrain `doesRepositoryMatchUrl` matching and subsequent branch/PR actions so that attacker-supplied protocol parameters cannot silently alter the working state of a repository without a visible prompt naming the source (URL) and target ref.
- Add rate limiting / confirmation-dialog friction consistent with other high-impact actions triggered via IPC (as already done for `isTrustedIPCSender`) to the protocol-URL action path.

### Proof of Concept
1. Attacker crafts a link: `x-github-client://openRepo/https://github.com/victim-org/victim-repo?branch=malicious-branch`
2. Victim (with `victim-repo` already cloned in Desktop) clicks the link from any webpage/email.
3. OS invokes GitHub Desktop with the URL; `handleAppURL` → `parseAppURL` → `dispatchURLAction` → `openRepositoryFromUrl` → `openBranchNameFromUrl(url, 'malicious-branch')` is invoked [10](#0-9) .
4. If this path checks out `malicious-branch` without a confirming dialog, the user's working tree is silently switched to attacker-controlled content.

Given I could not confirm step 4's exact behavior, treat this as a lead requiring verification of `openBranchNameFromUrl`/`openPullRequestFromUrl` implementations before treating it as a confirmed vulnerability rather than a plausible analog.

### Citations

**File:** app/src/main-process/ipc-main.ts (L53-65)
```typescript
function safeListener<E extends IpcMainEvent | IpcMainInvokeEvent, R>(
  listener: (event: E, ...a: any) => R
) {
  return (event: E, ...args: any) => {
    if (!isTrustedIPCSender(event.sender)) {
      log.error(
        `IPC message received from invalid sender: ${event.senderFrame?.url}`
      )
      return
    }

    return listener(event, ...args)
  }
```

**File:** app/src/main-process/trusted-ipc-sender.ts (L1-16)
```typescript
import { WebContents } from 'electron'

// WebContents id of trusted senders of IPC messages. This is used to verify
// that only IPC messages sent from trusted senders are handled, as recommended
// by the Electron security documentation:
// https://github.com/electron/electron/blob/main/docs/tutorial/security.md#17-validate-the-sender-of-all-ipc-messages
const trustedSenders = new Set<number>()

/** Adds a WebContents instance to the set of trusted IPC senders. */
export const addTrustedIPCSender = (wc: WebContents) => {
  trustedSenders.add(wc.id)
  wc.on('destroyed', () => trustedSenders.delete(wc.id))
}

/** Returns true if the given WebContents is a trusted sender of IPC messages. */
export const isTrustedIPCSender = (wc: WebContents) => trustedSenders.has(wc.id)
```

**File:** app/src/main-process/main.ts (L102-116)
```typescript
/** Extra argument for the protocol launcher on Windows */
const protocolLauncherArg = '--protocol-launcher'

const possibleProtocols = new Set(['x-github-client'])
if (__DEV_SECRETS__) {
  possibleProtocols.add('x-github-desktop-dev-auth')
} else {
  possibleProtocols.add('x-github-desktop-auth')
}
// Also support Desktop Classic's protocols.
if (__DARWIN__) {
  possibleProtocols.add('github-mac')
} else if (__WIN32__) {
  possibleProtocols.add('github-windows')
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

**File:** app/src/main-process/main.ts (L309-317)
```typescript
function setAsDefaultProtocolClient(protocol: string) {
  if (__WIN32__) {
    app.setAsDefaultProtocolClient(protocol, process.execPath, [
      protocolLauncherArg,
    ])
  } else {
    app.setAsDefaultProtocolClient(protocol)
  }
}
```

**File:** app/src/lib/parse-app-url.ts (L66-127)
```typescript
export function parseAppURL(url: string): URLActionType {
  const parsedURL = URL.parse(url, true)
  const hostname = parsedURL.hostname
  const unknown: IUnknownAction = { name: 'unknown', url }
  if (!hostname) {
    return unknown
  }

  const query = parsedURL.query

  const actionName = hostname.toLowerCase()
  if (actionName === 'oauth') {
    const code = getQueryStringValue(query, 'code')
    const state = getQueryStringValue(query, 'state')
    if (code != null && state != null) {
      return { name: 'oauth', code, state }
    } else {
      return unknown
    }
  }

  // we require something resembling a URL first
  // - bail out if it's not defined
  // - bail out if you only have `/`
  const pathName = parsedURL.pathname
  if (!pathName || pathName.length <= 1) {
    return unknown
  }

  // Trim the trailing / from the URL
  const parsedPath = pathName.substring(1)

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
  }

  return unknown
```

**File:** app/src/ui/dispatcher/dispatcher.ts (L1920-1938)
```typescript
  private doesRepositoryMatchUrl(
    repo: Repository | CloningRepository,
    url: string
  ): repo is RepositoryWithGitHubRepository {
    if (repo instanceof Repository && isRepositoryWithGitHubRepository(repo)) {
      const originRepoUrl = repo.gitHubRepository.htmlURL
      const upstreamRepoUrl = repo.gitHubRepository.parent?.htmlURL ?? null

      if (originRepoUrl !== null && urlsMatch(originRepoUrl, url)) {
        return true
      }

      if (upstreamRepoUrl !== null && urlsMatch(upstreamRepoUrl, url)) {
        return true
      }
    }

    return false
  }
```

**File:** app/src/ui/dispatcher/dispatcher.ts (L1940-1955)
```typescript
  private async openRepositoryFromUrl(action: IOpenRepositoryFromURLAction) {
    const { url, pr, branch, filepath } = action

    let repository: Repository | null

    if (pr !== null) {
      repository = await this.openPullRequestFromUrl(url, pr)
    } else if (branch !== null) {
      repository = await this.openBranchNameFromUrl(url, branch)
    } else {
      repository = await this.openOrCloneRepository(url)
    }

    if (repository === null) {
      return
    }
```
