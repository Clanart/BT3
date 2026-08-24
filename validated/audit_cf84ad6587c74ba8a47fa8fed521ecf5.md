## Finding: Unsanitized `html_url` from GitHub Check Run API object flows into `shell.openExternal` without scheme validation

### Title
Unvalidated Check Run `html_url` (attacker-controlled via malicious GitHub App) can produce non-`https` URLs passed to `shell.openExternal` - (File: `app/src/lib/ci-checks/ci-checks.ts`)

### Summary
`apiCheckRunToRefCheck` copies `checkRun.html_url` verbatim into `IRefCheck.htmlUrl` with no scheme/format validation, and `getCheckRunStepURL` later string-concatenates it into `${htmlUrl}/#step:${n}:1`. This value is eventually rendered as the `uri` prop of `LinkButton`, whose click handler calls `shell.openExternal(uri)` unconditionally.

### Finding Description
`apiCheckRunToRefCheck` assigns `htmlUrl: checkRun.html_url` directly from the API payload with no check that the value is an `https://` URL: [1](#0-0) 

`getCheckRunStepURL` builds the final navigable string purely by concatenation, again without any scheme validation: [2](#0-1) 

The resulting string is consumed as a `uri` in the UI (e.g. `ci-check-run-popover.tsx`, `pull-request-checks-failed.tsx`), and rendering components such as `LinkButton` call `shell.openExternal(uri)` on click with no scheme check: [3](#0-2) 

`shell` here is the app's `IAppShell` wrapper around Electron's `shell.openExternal`, proxied to the main process: [4](#0-3) 

I was not able to fully inspect the main-process implementation behind `openExternal` in `app/src/main-process/main.ts` / `app/src/ui/main-process-proxy.ts` within the available context to confirm whether any scheme allow-listing (e.g., restricting to `http`/`https`) is applied there before calling Electron's `shell.openExternal`. This is a gap in my verification — if such a check exists in the main process, it would mitigate this issue; if not, the vulnerability is directly exploitable as described.

### Impact Explanation
If no scheme validation exists downstream, an attacker who controls a GitHub Check Run (e.g. via a GitHub App installed on their own repository, or a repo they control that the victim views checks for) can set `html_url` to a `file://`, or other custom-scheme URI. When a Desktop user clicks the corresponding check-run link in the UI, `shell.openExternal` would be invoked with that URI, causing the OS to open a locally-addressed resource chosen by the attacker (e.g. `file:///Users/test/secret.txt`, or a more dangerous local path/executable already present on the user's machine). This does not let the attacker read remote-inaccessible file contents themselves, but it can expose local files to the victim in Desktop's context, trigger unwanted local file/application execution depending on OS handler behavior, and constitutes an escape of the "always https" invariant.

### Likelihood Explanation
Requires the victim to open a PR/branch with a check run from a repository or GitHub App the attacker controls, and to click the specific check-run/step link — a plausible, low-friction interaction Desktop is designed to encourage (clicking check statuses is a normal part of the review workflow). No admin rights, malware, or credential leakage needed, matching the in-scope threat model of "attacker controls ... a GitHub API object ... and the result is ... renderer-sandbox or IPC escape" style issues, though actual impact severity is capped by the level of OS-level URL-open sandboxing.

### Recommendation
Validate that `html_url` values returned from the API are well-formed `https:` (or `http:`) URLs before storing them into `IRefCheck.htmlUrl`, e.g. in `apiCheckRunToRefCheck` and `apiStatusToRefCheck`, and re-validate the final constructed string in `getCheckRunStepURL` before returning it. Additionally, as defense in depth, `shell.openExternal` (or its main-process implementation) should enforce an `http(s)`-only allow-list regardless of caller, since it is a generic external-link opener reachable from many attacker-influenced strings across the codebase.

### Proof of Concept
```ts
import { apiCheckRunToRefCheck, getCheckRunStepURL } from 'app/src/lib/ci-checks/ci-checks'

const maliciousCheckRun /* IAPIRefCheckRun */ = {
  id: 1,
  name: 'evil-check',
  status: 'completed',
  conclusion: 'success',
  app: { name: 'Malicious App' },
  check_suite: { id: 1 },
  html_url: 'file:///Users/test/secret.txt',
  started_at: '2024-01-01T00:00:00Z',
  completed_at: '2024-01-01T00:01:00Z',
} as any

const refCheck = apiCheckRunToRefCheck(maliciousCheckRun)
// refCheck.htmlUrl === 'file:///Users/test/secret.txt'

const stepURL = getCheckRunStepURL(
  refCheck,
  { number: 1 } as any,
  { htmlURL: 'https://github.com/owner/repo' } as any,
  1
)
// stepURL === 'file:///Users/test/secret.txt/#step:1:1'
// This string, when clicked via LinkButton, is passed straight to shell.openExternal
``` [5](#0-4) [6](#0-5)

### Citations

**File:** app/src/lib/ci-checks/ci-checks.ts (L169-184)
```typescript
export function apiCheckRunToRefCheck(checkRun: IAPIRefCheckRun): IRefCheck {
  return {
    id: checkRun.id,
    name: checkRun.name,
    description: getCheckRunShortDescription(
      checkRun.status,
      checkRun.conclusion,
      getCheckDurationInMilliseconds(checkRun)
    ),
    status: checkRun.status,
    conclusion: checkRun.conclusion,
    appName: checkRun.app.name,
    checkSuiteId: checkRun.check_suite.id,
    htmlUrl: checkRun.html_url,
  }
}
```

**File:** app/src/lib/ci-checks/ci-checks.ts (L570-588)
```typescript
export function getCheckRunStepURL(
  checkRun: IRefCheck,
  step: IAPIWorkflowJobStep,
  repository: GitHubRepository,
  pullRequestNumber: number
): string | null {
  if (checkRun.htmlUrl === null && repository.htmlURL === null) {
    // A check run may not have a url depending on how it is setup.
    // However, the repository should have one; Thus, we shouldn't hit this
    return null
  }

  const url =
    checkRun.htmlUrl !== null
      ? `${checkRun.htmlUrl}/#step:${step.number}:1`
      : `${repository.htmlURL}/pull/${pullRequestNumber}`

  return url
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
