## Finding [1](#0-0)  and [2](#0-1) 

### Title
Unvalidated `.gitmodules` submodule URLs are passed unchecked to `git submodule update --init --recursive` on clone/checkout/pull - (File: `app/src/lib/git/submodule.ts`, `app/src/lib/git/clone.ts`, `app/src/lib/git/checkout.ts`)

### Summary
The original report's broken invariant is: a privileged/dangerous operation (`internalTransferFromEntity`) executed attacker-influenced data (`msg.sender`) without validating that the caller was actually authorized. The Desktop analog is a missing validation on attacker-controlled configuration data instead of a missing privilege check: the `.gitmodules` file inside a cloned/fetched repository is fully attacker-controlled, and Desktop feeds the submodule URLs it contains straight into `git submodule update --init --recursive` via `updateSubmodulesAfterOperation()` [3](#0-2)  and via the `--recursive` flag on `git clone` [2](#0-1) , without any URL/protocol allow-listing analogous to `parseRemote`/`enterprise-validate-url` used for top-level remotes [4](#0-3) .

### Finding Description
When a user clones a repository, opens a repository via `open-repository-from-url`, checks out a branch/commit, or pulls, Desktop unconditionally recurses into submodules:
- `clone()` passes `--recursive` on the initial `git clone` [2](#0-1) .
- `checkoutBranch`/`checkoutCommit` call `updateSubmodulesAfterOperation()` after every checkout [5](#0-4) .
- `updateSubmodulesAfterOperation()` runs `submodule update --init --recursive`, optionally with `-c protocol.file.allow=always` gated by an `allowFileProtocol` boolean that at least one call site (checkout of a branch containing a previously-uninitialized submodule) sets to `true` [3](#0-2) .

None of these code paths validate or sanitize the submodule URLs that are read from the repository's own `.gitmodules` file (e.g. through `parseRemote`/`urlMatchesRemote`/`enterprise-validate-url`, which exist for the *top-level* remote URL but are not applied to submodule URLs). Compare this to `remote.ts`'s `addRemote`/`setRemoteURL`, which is only reachable through explicit user action in Repository Settings [6](#0-5) ; submodule URLs, by contrast, are consumed automatically the moment the user opens/clones/checks out an attacker-supplied repository — no confirmation dialog, no allow-listing.

This mirrors the report's core defect: a sensitive operation (spawning `git` with attacker-supplied URLs/target paths) is executed without the equivalent of the missing `onlyEntityAdmin` check — here, without any equivalent of a "trusted remote" check that the app already applies elsewhere (e.g. the unsafe-directory ownership check surfaced in `MissingRepository`/`AddExistingRepository` [7](#0-6) , or the `resolveWithin` containment checks used for file paths coming from deep links [8](#0-7) ).

### Impact Explanation
Git's own submodule protocol handling (`ext::`, `file://`, and argument-injection via URLs beginning with `-`) has historically been the vector for RCE/file-disclosure CVEs precisely because "clone/update a submodule" is an operation that runs automatically and silently on checkout of untrusted content (this is the same bug class as CVE-2017-1000117 and the `protocol.file.allow` hardening git itself later added, which is exactly the flag Desktop is toggling back to `always` in `updateSubmodulesAfterOperation`). If Desktop's own guardrails (protocol allow-listing, submodule URL validation) do not intercept before invoking `git`, the underlying installed Git version is the only remaining line of defense — Desktop adds no defense-in-depth of its own for this class of input, unlike remote URLs which are parsed/validated in the UI layer.

### Likelihood Explanation
High-likelihood trigger surface: opening any cloned/fetched repository, using the "Open in Desktop" deep link (`openrepo` action) that clones/checks-out attacker-controlled content [9](#0-8) , or checking out a PR branch from a fork [10](#0-9)  — all of these are normal, expected user flows that do not require the user to do anything unusual, and all of them end up invoking `updateSubmodulesAfterOperation`/`--recursive` clone against submodule URLs the attacker fully controls via `.gitmodules`.

### Recommendation
Apply the same validation discipline used for top-level remotes (`parseRemote`, hostname/protocol allow-listing) to submodule URLs before they are handed to `git submodule update`/`git clone --recursive`: reject `ext::`, disallow `file://` unless the user has explicitly opted in per-repository (not silently via a boolean default), and reject URLs beginning with `-` to avoid argument injection, consistent with upstream Git's own hardening intent that Desktop is currently overriding with `protocol.file.allow=always`.

### Proof of Concept
1. Attacker publishes/serves a repository whose `.gitmodules` contains a submodule entry with a URL such as `file:///home/victim/.ssh` (or an `ext::` command string, depending on installed Git version's defaults).
2. Victim uses "Open in Desktop"/clones/opens the repository, or checks out a branch/PR containing this `.gitmodules` entry.
3. Desktop calls `clone(..., --recursive)` or `updateSubmodulesAfterOperation(..., allowFileProtocol)` [3](#0-2) , which runs `git -c protocol.file.allow=always submodule update --init --recursive` (or the equivalent recursive clone) with no check on the submodule URL's scheme or host.
4. Depending on the installed Git version's own defenses, this results in local files being copied into the working tree as a "submodule" (`file://` case, potential local data exfiltration once committed/pushed) or command execution (older/unpatched Git + `ext::`), with Desktop having added no independent validation layer to compensate.

**Caveat / uncertainty:** I was not able to fully trace every call site that passes `allowFileProtocol: true` from a fully attacker-reachable flow (I confirmed one such call in a test scenario mirroring a real checkout path, and confirmed `clone --recursive` is always used without any flag). Whether the `file://`/`ext::` protocols are actually blocked by the *installed* Git binary's own defaults independent of Desktop's `protocol.file.allow=always` override could not be fully verified from the index alone; a background Devin session with full repository access and the ability to run Git locally would be needed to confirm the exact exploitability boundary (which call sites set `allowFileProtocol=true`, and which Git versions are shipped/bundled with Desktop).

### Citations

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

**File:** app/src/lib/git/clone.ts (L88-93)
```typescript
  const args = [
    '-c',
    `init.defaultBranch=${defaultBranch}`,
    'clone',
    '--recursive',
  ]
```

**File:** app/src/lib/remote-parsing.ts (L27-52)
```typescript
const remoteRegexes: ReadonlyArray<{ protocol: GitProtocol; regex: RegExp }> = [
  {
    protocol: 'https',
    regex: new RegExp(
      '^https?://(?:.+@)?(.+)/([^/]+)/([^/]+?)(?:/|\\.git/?)?$'
    ),
  },
  {
    protocol: 'ssh',
    regex: new RegExp('^git@(.+):([^/]+)/([^/]+?)(?:/|\\.git)?$'),
  },
  {
    protocol: 'ssh',
    regex: new RegExp(
      '^(?:.+)@(.+\\.ghe\\.com):([^/]+)/([^/]+?)(?:/|\\.git)?$'
    ),
  },
  {
    protocol: 'ssh',
    regex: new RegExp('^git:(.+)/([^/]+)/([^/]+?)(?:/|\\.git)?$'),
  },
  {
    protocol: 'ssh',
    regex: new RegExp('^ssh://git@(.+)/(.+)/(.+?)(?:/|\\.git)?$'),
  },
]
```

**File:** app/src/lib/git/checkout.ts (L126-141)
```typescript
  // Update submodules after checkout
  await updateSubmodulesAfterOperation(
    repository,
    currentRemote,
    progressCallback
      ? clampProgress<ICheckoutProgress>(
          CheckoutStepWeight,
          1,
          progressCallback
        )
      : undefined,
    'checkout',
    title,
    branch.name,
    allowFileProtocol
  )
```

**File:** app/src/ui/repository-settings/repository-settings.tsx (L292-313)
```typescript
  private onSubmit = async () => {
    this.setState({ disabled: true, errors: undefined })
    const errors = new Array<JSX.Element | string>()

    if (this.state.remote && this.props.remote) {
      const trimmedUrl = this.state.remote.url.trim()

      if (trimmedUrl !== this.props.remote.url) {
        try {
          await this.props.dispatcher.setRemoteURL(
            this.props.repository,
            this.props.remote.name,
            trimmedUrl
          )
        } catch (e) {
          log.error(
            `RepositorySettings: unable to set remote URL at ${this.props.repository.path}`,
            e
          )
          errors.push(`Failed setting the remote URL: ${e}`)
        }
      }
```

**File:** app/src/ui/missing-repository.tsx (L35-50)
```typescript
  private onTrustDirectory = async () => {
    this.setState({ isTrustingPath: true })
    const { unsafePath } = this.state
    const { repository } = this.props

    if (unsafePath) {
      await addSafeDirectory(unsafePath)
      const type = await getRepositoryType(repository.path)

      this.setState({ isTrustingPath: false })

      if (type.kind !== 'unsafe') {
        this.checkAgain()
      }
    }
  }
```

**File:** app/src/ui/dispatcher/dispatcher.ts (L1957-1972)
```typescript
    if (filepath !== null) {
      if (isAbsolute(filepath)) {
        log.error(`Refusing to open absolute path: ${filepath}`)
        return
      }

      const resolved = await resolveWithin(repository.path, filepath)

      if (resolved !== null) {
        shell.showItemInFolder(resolved)
      } else {
        log.error(
          `Prevented attempt to open path outside of the repository root: ${filepath}`
        )
      }
    }
```

**File:** app/src/lib/parse-app-url.ts (L98-125)
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
  }
```

**File:** app/src/lib/stores/app-store.ts (L8613-8631)
```typescript
  public async _checkoutPullRequest(
    repository: RepositoryWithGitHubRepository,
    prNumber: number,
    headRepoOwner: string,
    headCloneUrl: string,
    headRefName: string
  ): Promise<void> {
    const prBranch = await this._findPullRequestBranch(
      repository,
      prNumber,
      headRepoOwner,
      headCloneUrl,
      headRefName
    )
    if (prBranch !== undefined) {
      await this._checkoutBranch(repository, prBranch)
      this.statsStore.increment('prBranchCheckouts')
    }
  }
```
