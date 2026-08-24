## Title
Argument/option injection into `git log` via unvalidated `commit_sha` from Alive checks-failed events — (File: `app/src/lib/stores/notifications-store.ts`)

### Summary
`handleChecksFailedEvent` takes `event.commit_sha` from a server-pushed Alive event and passes it, unmodified and unvalidated, into `getCommit()` → `getCommits()`, where it is inserted directly into the `git log` argv as the revision-range argument, before git's `--` separator.

### Finding Description
`handleChecksFailedEvent` reads `commitSHA = event.commit_sha` and calls `getCommit(repository, commitSHA)` with no format check (no 40-char hex validation, no `-` prefix check): [1](#0-0) 

`IDesktopChecksFailedAliveEvent.commit_sha` is typed as a plain `string` and is populated straight from the Alive websocket payload with only a `type`/shape discriminator check (`data.type === 'pr-checks-failed'`) — there is no regex or schema validation of `commit_sha` itself: [2](#0-1) [3](#0-2) 

`getCommit` forwards the string as `revisionRange` to `getCommits`: [4](#0-3) 

`getCommits` builds the argv array and pushes `revisionRange` immediately after `'log'`, **before** the `--` pathspec separator that would otherwise stop option parsing: [5](#0-4) 

Because `git()` invokes `dugite`'s `exec`, which spawns the git binary directly (no shell), classic shell command injection is not possible, but git itself still parses each argv token, so any element beginning with `-` is treated as an **option to `git log`** rather than a revision. Git's `log` command supports options such as `--output=<file>` (writes the log output to an attacker-chosen file path) and other options that can affect file system state or process behavior. Since `commit_sha` is never validated as a 40-character hex SHA (or rejected if it starts with `-`), a malicious Alive event can supply a value like `--output=/path/to/target` and have it land directly in the `git log` argv, unmodified.

### Impact Explanation
This is a git-argument-injection primitive: an attacker who controls the Alive event stream (the "checks failed" notification, which originates from GitHub's Alive service tied to a repository/PR the attacker can influence, e.g. via a malicious/compromised CI check or a crafted PR against a repo the victim has open in Desktop) can inject arbitrary `git log` options. Depending on which options are reachable through this argv position, this can result in local file writes (`--output=<path>`) under the invoking process's permissions, which is an out-of-repo file-write primitive — one of the explicitly listed valid impacts (file write outside the repo). It could also potentially be leveraged to affect other `git log` behaviors depending on installed git version and enabled options.

### Likelihood Explanation
The trigger path requires: (1) the victim has the repository open with GitHub notifications/Alive enabled and signed-in with an account, (2) the victim has a tracked pull request whose head SHA/author email the attacker can align with (there is an author-email cross-check against the account's own emails at `notifications-store.ts:350`, and a PR-must-be-in-cache check), which constrains but does not eliminate exploitability since the attacker only needs to get their crafted `commit_sha` accepted as the event payload for a PR the victim already has cached from a `git push` via Desktop. This makes exploitation non-trivial but plausible without any local access, matching the "attacker controls a GitHub API/Alive object" threat model in scope.

### Recommendation
Validate `event.commit_sha` (and any other value used as a git revision) against a strict 40-character (or 7–40) hexadecimal SHA pattern (`/^[0-9a-f]{7,40}$/i`) before passing it to `getCommit`/`getCommits`, and reject/ignore the event otherwise. As defense in depth, `getCommits` should also refuse to accept a `revisionRange` argument that begins with `-`, or should insert it after a `--` boundary combined with `--end-of-options`, to prevent any caller-supplied revision string from being interpreted as a git option.

### Proof of Concept
1. Craft a synthetic `IDesktopChecksFailedAliveEvent` with `commit_sha: '--output=/tmp/pwned'` (or another `git log`-recognized option) for a pull request the victim has cached (e.g. one they pushed from Desktop).
2. Deliver it via the Alive websocket channel (or via `simulatePullRequestChecksFailed`/`simulateAliveEvent` for local testing) so it reaches `AliveStore.notify` → `onAliveEventReceived` → `NotificationsStore.handleAliveEvent` → `handleChecksFailedEvent`.
3. Observe `getCommit(repository, '--output=/tmp/pwned')` → `getCommits` pushes this string directly into the `git log` argv before `--`, at `app/src/lib/git/log.ts:144-146`, so `git()` is invoked as `git log --output=/tmp/pwned --date=raw ... -- ` — demonstrating the unmodified attacker string reaching the git invocation as an argument, not a revision. [6](#0-5) [7](#0-6)

### Citations

**File:** app/src/lib/stores/notifications-store.ts (L334-346)
```typescript
    const commitSHA = event.commit_sha

    if (this.skipCommitShas.has(commitSHA)) {
      return
    }

    const commit =
      this.cachedCommits.get(commitSHA) ??
      (await getCommit(repository, commitSHA))
    if (commit === null) {
      this.skipCommitShas.add(commitSHA)
      return
    }
```

**File:** app/src/lib/stores/alive-store.ts (L13-21)
```typescript
export interface IDesktopChecksFailedAliveEvent {
  readonly type: 'pr-checks-failed'
  readonly timestamp: number
  readonly owner: string
  readonly repo: string
  readonly pull_request_number: number
  readonly check_suite_id: number
  readonly commit_sha: string
}
```

**File:** app/src/lib/stores/alive-store.ts (L254-267)
```typescript
  private notify = (subscribers: Iterable<AliveStore>, event: AliveEvent) => {
    if (event.type !== 'message') {
      return
    }

    const data = event.data as any as DesktopAliveEvent
    if (
      data.type === 'pr-checks-failed' ||
      data.type === 'pr-review-submit' ||
      data.type === 'pr-comment'
    ) {
      this.emitter.emit(this.ALIVE_EVENT_RECEIVED_EVENT, data)
    }
  }
```

**File:** app/src/lib/git/log.ts (L142-164)
```typescript
  const args = ['log']

  if (revisionRange !== undefined) {
    args.push(revisionRange)
  }

  args.push('--date=raw')

  if (limit !== undefined) {
    args.push(`--max-count=${limit}`)
  }

  if (skip !== undefined) {
    args.push(`--skip=${skip}`)
  }

  args.push(
    ...formatArgs,
    '--no-show-signature',
    '--no-color',
    ...additionalArgs,
    '--'
  )
```

**File:** app/src/lib/git/log.ts (L336-347)
```typescript
/** Get the commit for the given ref. */
export async function getCommit(
  repository: Repository,
  ref: string
): Promise<Commit | null> {
  const commits = await getCommits(repository, ref, 1)
  if (commits.length < 1) {
    return null
  }

  return commits[0]
}
```
