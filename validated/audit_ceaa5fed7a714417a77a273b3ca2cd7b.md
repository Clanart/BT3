## Finding: Git clone protection (`GIT_CLONE_PROTECTION_ACTIVE`) is explicitly disabled for every recursive clone of an untrusted URL

### Title
GitHub Desktop unconditionally disables Git's clone-time submodule protection during `--recursive` clone of attacker-controlled URLs - (File: `app/src/lib/git/clone.ts`)

### Summary
The bug-class in the seed report (missing guard/modifier that should gate an action before it is allowed to run) maps to `app/src/lib/git/clone.ts`, where the environment variable that Git itself uses to protect against malicious repository content during `--recursive` clones is deliberately set to `'false'` for every clone, regardless of how untrusted the source URL is.

### Finding Description
`clone()` builds the execution environment for every clone with: [1](#0-0) 

```
const env = {
  ...(await envForRemoteOperation(url)),
  GIT_CLONE_PROTECTION_ACTIVE: 'false',
}
...
const args = [
  '-c',
  `init.defaultBranch=${defaultBranch}`,
  'clone',
  '--recursive',
]
```

`--recursive` is passed unconditionally, meaning submodules referenced by the cloned (attacker-controlled) repository are also cloned in the same operation. `GIT_CLONE_PROTECTION_ACTIVE` is the guard Git uses at clone time (introduced upstream to mitigate crafted-repository clone attacks where a malicious top-level repo, combined with maliciously-named/pathed submodules, can cause Git to write files outside the intended working tree or into a nested `.git`/hooks location on the initial recursive clone). By hard-coding this to `'false'`, Desktop opts every user-initiated clone of any remote URL (`clone-url` CLI action, `open-repository-from-url` deep link, manual "Clone repository" URL entry, etc.) out of that protection — the broken invariant here is "clone-time protections must remain active when cloning an attacker-supplied URL," and no other check in `clone.ts` or its callers re-enables or conditions this on trust level of the source.

The url itself is attacker/user-supplied and flows straight into this function: it originates from deep links (`app/src/lib/parse-app-url.ts`, `open-repository-from-url` action dispatched via `dispatcher.dispatchURLAction` → `openRepositoryFromUrl` / `openOrCloneRepository`) or from CLI `--cli-clone` arguments handled in `app/src/main-process/main.ts`, i.e. exactly the "attacker controls a cloned/fetched repository" primitive called out in the Valid Impact criteria. [2](#0-1) [3](#0-2) 

Nothing downstream re-validates or restores the protection — `updateSubmodulesAfterOperation` (used post-checkout/pull) has its own opt-in `allowFileProtocol` flag defaulted to `false`, but that is a separate, later step and does not compensate for the protection already disabled during the initial `clone --recursive` call itself. [4](#0-3) 

### Impact Explanation
If Git's clone-time protection exists specifically to stop a crafted repository from writing/executing content outside the intended clone destination during the initial recursive submodule population, disabling it here removes that safety net for the exact scenario it's meant to cover: a user cloning or opening a link to a repository they do not control. Depending on the local Git version this can translate into files being written outside the expected repository path or content executing as part of the clone, i.e. code execution / file write outside the repo — squarely in the "Valid Impact" bucket (attacker-controlled cloned/fetched repository leading to code execution or file write outside repo).

### Likelihood Explanation
Every clone performed by Desktop — whether from the UI's "Clone repository" dialog, a `x-github-client://openRepo/...` deep link, or `github-desktop --cli-clone <url>` — goes through this single `clone()` function and therefore always runs with protection disabled and `--recursive` enabled. No user interaction beyond the normal "clone this URL" action (or clicking a deep link) is required, and the attacker fully controls the repository content (including its submodule definitions), which is the only prerequisite.

### Recommendation
- Do not unconditionally set `GIT_CLONE_PROTECTION_ACTIVE: 'false'`; only disable it (if at all) for specific, already-trusted/internal use cases, and otherwise let Git's own protection remain active for user-supplied URLs.
- Audit why this was introduced (likely to work around a legitimate false-positive) and instead address that with a narrower fix (e.g., only for local/trusted paths) rather than a blanket bypass.
- Add a regression test asserting that cloning an untrusted URL retains Git's default clone protection.

### Proof of Concept
1. Register/host a malicious repository containing a submodule configuration crafted to trigger the class of clone-time write/execution issue that `GIT_CLONE_PROTECTION_ACTIVE` is designed to stop (e.g., a submodule path/name designed to escape the intended nested `.git` directory on the victim's filesystem).
2. Send the victim a `x-github-client://openRepo/<attacker-repo-url>` deep link or have them run `github-desktop --cli-clone <attacker-repo-url>`.
3. Desktop calls `clone()` in `app/src/lib/git/clone.ts`, which executes `git -c init.defaultBranch=... clone --recursive -- <url> <path>` with `GIT_CLONE_PROTECTION_ACTIVE=false` in the environment, removing Git's own defense during the recursive submodule population.
4. Depending on the installed Git version, this allows the crafted submodule to write or execute content outside the intended clone destination that Git's protection would otherwise have blocked.

### Citations

**File:** app/src/lib/git/clone.ts (L81-93)
```typescript
  const env = {
    ...(await envForRemoteOperation(url)),
    GIT_CLONE_PROTECTION_ACTIVE: 'false',
  }

  const defaultBranch = options.defaultBranch ?? (await getDefaultBranch())

  const args = [
    '-c',
    `init.defaultBranch=${defaultBranch}`,
    'clone',
    '--recursive',
  ]
```

**File:** app/src/main-process/main.ts (L282-291)
```typescript
  if (typeof args['cli-open'] === 'string') {
    handleCLIAction({ kind: 'open-repository', path: args['cli-open'] })
  } else if (typeof args['cli-clone'] === 'string') {
    handleCLIAction({
      kind: 'clone-url',
      url: args['cli-clone'],
      branch:
        typeof args['cli-branch'] === 'string' ? args['cli-branch'] : undefined,
    })
  }
```

**File:** app/src/ui/dispatcher/dispatcher.ts (L1940-1951)
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
```

**File:** app/src/lib/git/submodule.ts (L29-51)
```typescript
export async function updateSubmodulesAfterOperation<T extends Progress>(
  repository: Repository,
  remote: IRemote | null,
  progressCallback: ((progress: T) => void) | undefined,
  progressKind: T['kind'],
  title: string,
  targetOrRemote: string,
  allowFileProtocol: boolean
): Promise<void> {
  const opts: IGitStringExecutionOptions = {
    env: await envForRemoteOperation(
      getFallbackUrlForProxyResolve(repository, remote)
    ),
    expectedErrors: AuthenticationErrors,
  }

  const args = [
    ...(allowFileProtocol ? ['-c', 'protocol.file.allow=always'] : []),
    'submodule',
    'update',
    '--init',
    '--recursive',
  ]
```
