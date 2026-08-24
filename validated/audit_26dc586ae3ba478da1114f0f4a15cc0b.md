## Finding: Desktop re-enables the `file://` submodule protocol disabled by upstream Git (CVE-2022-39253) via `protocol.file.allow=always`

The reported crate malicious-build-script report is a supply-chain/exfiltration bug class: untrusted content causes the tool to read/exfiltrate data it shouldn't touch. The closest verified Desktop analog is in the submodule-update path, where Desktop explicitly overrides a Git security fix that was introduced specifically to stop repositories from exfiltrating local filesystem content via submodules.

### Finding Description
Git 2.38.1 disabled the `file://` transport for submodules by default (fix for CVE-2022-39253) because a malicious repository could declare a submodule whose URL is a local `file://` path, causing `git submodule update` to silently pull in the contents of another local git repository (or arbitrary reachable local `.git` directories) into the victim's working tree — leaking data the victim never intended to share.

Desktop's `updateSubmodulesAfterOperation` re-enables this disabled protocol on demand: [1](#0-0) 

The `allowFileProtocol` flag is threaded through `checkoutBranch` / `checkoutCommit`, both of which run submodule update immediately after checking out attacker-influenced branch/commit content: [2](#0-1) [3](#0-2) 

When `allowFileProtocol` is `true`, the `git submodule update --init --recursive` invocation is prefixed with `-c protocol.file.allow=always`, which unconditionally permits `file://` (and bare local-path) submodule URLs found in the (attacker-controlled) `.gitmodules` file of a cloned/fetched repository.

### Impact Explanation
If a crafted `.gitmodules` entry points a submodule at a local path that happens to be a valid git repository on the victim's machine (e.g. another checked-out project, a sibling working copy, or a repo path guessable/discoverable on disk), `git submodule update --init` with `protocol.file.allow=always` will clone/checkout that repository's contents into the victim's working tree. This is a "read outside the repo" primitive: content from a completely unrelated local repository can be pulled into the current repository's working directory. If the user subsequently stages and commits (a common workflow when Desktop reports "new files" after a checkout/pull), that foreign content is pushed to whatever remote the current repository points at — satisfying the "silent corruption of what the user commits or pushes" impact category. This exactly reverses the mitigation Git upstream shipped for CVE-2022-39253.

### Likelihood Explanation
Exploitability depends on Desktop actually invoking checkout/submodule-update with `allowFileProtocol = true` for repository content that isn't already fully trusted by the user (the parameter defaults to `false` in both `checkoutBranch` and `checkoutCommit`). I was not able to fully confirm, within the available search budget, the exact condition(s) under which `app-store.ts` passes `allowFileProtocol = true` into these functions (8 call-site references were located in `app/src/lib/stores/app-store.ts` but their surrounding logic was not retrieved). This is the key open question: if Desktop only sets this flag for submodules that were already present/initialized locally by the user (re-syncing a submodule the user previously and knowingly cloned via a local path), the practical attacker-reachability is much lower than if it is set broadly for any checkout/pull/clone flow touching untrusted remote content.

### Recommendation
- Confirm and restrict all call sites that set `allowFileProtocol = true` to only apply when the submodule URL was already resolved/trusted in a prior, user-approved operation — never based on `.gitmodules` content freshly introduced by a fetch/checkout of untrusted remote refs.
- Where `file://` submodules must be supported (e.g., for local development scaffolding), scope the allowance with `protocol.file.allow=user` and additional path validation (disallow paths outside a known safe root), rather than the global `protocol.file.allow=always`.
- Add a guard, similar to `isClonePathSensitive` in `clone.ts`, that inspects `.gitmodules` submodule URLs before permitting `file://` protocol re-enablement.

### Proof of Concept (conceptual, pending confirmation of trigger conditions)
1. Attacker publishes a repository whose `.gitmodules` contains a submodule URL such as `file:///Users/victim/other-project` (a path that is plausible/guessable on many developer machines, e.g. a common IDE/project folder or a path leaked via prior interactions).
2. Victim clones/fetches this repository in GitHub Desktop and Desktop performs a checkout that reaches `updateSubmodulesAfterOperation` with `allowFileProtocol = true`.
3. `git -c protocol.file.allow=always submodule update --init --recursive` succeeds in cloning the local `other-project` repository's content into the attacker-controlled submodule path.
4. The victim's working tree now contains foreign repository content; if committed/pushed, this content is exfiltrated to the attacker's remote.

Because I could not fully trace the exact `app-store.ts` conditions that set `allowFileProtocol = true`, this should be treated as a **needs-verification** finding rather than a fully confirmed exploit chain — a Devin session with full repository access should inspect those 8 call sites in `app/src/lib/stores/app-store.ts` to determine reachability from untrusted remote content before treating this as conclusively exploitable.

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

**File:** app/src/lib/git/checkout.ts (L102-146)
```typescript
export async function checkoutBranch(
  repository: Repository,
  branch: Branch,
  currentRemote: IRemote | null,
  progressCallback?: ProgressCallback,
  allowFileProtocol: boolean = false
): Promise<true> {
  const title = `Checking out branch ${branch.name}`
  const opts = await getCheckoutOpts(
    repository,
    title,
    branch.name,
    currentRemote,
    progressCallback
      ? clampProgress(0, CheckoutStepWeight, progressCallback)
      : undefined,
    `Switching to ${__DARWIN__ ? 'Branch' : 'branch'}`
  )

  const baseArgs = getCheckoutArgs(progressCallback)
  const args = [...baseArgs, ...(await getBranchCheckoutArgs(branch))]

  await git(args, repository.path, 'checkoutBranch', opts)

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

  // we return `true` here so `GitStore.performFailableGitOperation`
  // will return _something_ differentiable from `undefined` if this succeeds
  return true
}
```

**File:** app/src/lib/git/checkout.ts (L163-207)
```typescript
export async function checkoutCommit(
  repository: Repository,
  commit: CommitOneLine,
  currentRemote: IRemote | null,
  progressCallback?: ProgressCallback,
  allowFileProtocol: boolean = false
): Promise<true> {
  const title = `Checking out ${__DARWIN__ ? 'Commit' : 'commit'}`
  const target = shortenSHA(commit.sha)
  const opts = await getCheckoutOpts(
    repository,
    title,
    target,
    currentRemote,
    progressCallback
      ? clampProgress(0, CheckoutStepWeight, progressCallback)
      : undefined
  )

  const baseArgs = getCheckoutArgs(progressCallback)
  const args = [...baseArgs, commit.sha]

  await git(args, repository.path, 'checkoutCommit', opts)

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
    target,
    allowFileProtocol
  )

  // we return `true` here so `GitStore.performFailableGitOperation`
  // will return _something_ differentiable from `undefined` if this succeeds
  return true
}
```
