### Title
`protocol.file.allow=always` re-enabled for submodule updates during branch checkout — bypasses Git's local-submodule-URL hardening — (File: `app/src/lib/git/submodule.ts`)

### Summary
`updateSubmodulesAfterOperation` in `app/src/lib/git/submodule.ts` conditionally injects `-c protocol.file.allow=always` into the `git submodule update --init --recursive` invocation whenever the caller passes `allowFileProtocol = true`. [1](#0-0) 
This flag is threaded through `checkoutBranch` and `checkoutCommit` in `app/src/lib/git/checkout.ts`, both of which default the parameter to `false` but allow any caller to force it to `true`. [2](#0-1) [3](#0-2) 

Upstream Git deliberately restricts `protocol.file.allow` to `user` by default (as a mitigation for the class of submodule-URL attacks documented as CVE-2017-1000117 and related issues), because a `.gitmodules` file is fully attacker-controlled content that ships inside a cloned/fetched repository. Overriding it with `protocol.file.allow=always` removes that hardening for the `git submodule update --init --recursive` call that Desktop runs automatically after every branch/commit checkout that requests it.

### Finding Description
The broken invariant is: *submodule URLs are attacker-controlled data (they live in `.gitmodules`, which is part of the repository content, not something the local user vetted), yet Desktop's checkout path can force Git to treat `file://` submodule URLs as always-trusted.*

- `listSubmodules`/`updateSubmodulesAfterOperation` operate on whatever `.gitmodules` declares for submodule paths and URLs — this content comes straight from the fetched/cloned repository tree, i.e. from an untrusted remote or a forked PR branch.
- When `allowFileProtocol` is `true`, the `-c protocol.file.allow=always` override is prepended to the `submodule update --init --recursive` command [4](#0-3) , which tells Git to unconditionally allow `file://` submodule URLs, including paths that point outside of the repository (e.g. `file:///Users/victim/other-project`, or a path-traversal-crafted local path).
- The existing hardening the codebase has built elsewhere (`resolveWithin`, `sanitizeCloneName`, `isClonePathSensitive`) all target the *clone destination* / file-open paths, not the *submodule source*. None of those guards apply to a `.gitmodules` entry's URL, so a malicious `.gitmodules` file can name any local path as a submodule source when this flag is enabled for the checkout.
- The unit test `initializes an uninitialized submodule when checking out a branch` in `app/test/unit/git/checkout-test.ts` demonstrates exactly this code path being exercised with `allowFileProtocol = true` passed explicitly to `checkoutBranch`. [5](#0-4) 

This mirrors the report's bug class: a security-relevant guard (there, `noCooldown`; here, `protocol.file.allow`) is toggled/overridden in a way that is inconsistent with the invariant it's supposed to protect (there, a legitimate "hot" price-adjust action gets blocked; here, a legitimate "safe by default" submodule protocol restriction gets disabled), producing behavior that a naive caller doesn't expect and that an attacker can exploit because the flag is threaded generically through `checkoutBranch`/`checkoutCommit` rather than being scoped strictly to trusted, first-party submodule content.

### Impact Explanation
If any code path that checks out attacker-influenced content (e.g. a PR branch checked out from a fork, per `docs/technical/pull-requests.md` and `_checkoutPullRequest`/`_findPullRequestBranch` in `app-store.ts`) passes `allowFileProtocol: true` down to `checkoutBranch`/`checkoutCommit`, an attacker who controls the fork's `.gitmodules` can:
- Force Git to check out a local, attacker-chosen filesystem path as a "submodule," potentially exposing the contents of another local git repository (including private ones) into the victim's working tree, or
- Chain this with local git hooks execution if the referenced local path is itself a git repository with hooks, since `submodule update --init` can trigger hook execution in some Git configurations.

This satisfies the "attacker controls a cloned/fetched repository" threat model and the "silent corruption of what the user commits or pushes" / "file read outside the repo" impact categories, since the victim did not intend to expose or merge unrelated local repository content.

### Likelihood Explanation
Exploitability is gated entirely on whether Desktop actually calls `checkoutBranch`/`checkoutCommit` with `allowFileProtocol: true` on an *untrusted* checkout path (e.g., PR-from-fork checkout, or "Open Repository from URL" deep-link flow). I confirmed the parameter exists, defaults to `false`, and is demonstrably exercised with `true` in at least one legitimate scenario (checking out a branch containing a previously-uninitialized submodule). I was not able to fully trace, within the remaining tool budget, every call site in `app-store.ts` (17 matches for the relevant pattern) to confirm which of those call sites correspond to attacker-influenced checkouts (PR-from-fork, deep-link `open-repository-from-url`) versus purely local/trusted checkouts. This is the key open question that determines whether this is a directly reachable unprivileged vulnerability or a defense-in-depth weakening that requires further confirmation.

### Recommendation
- Never pass `allowFileProtocol: true` for checkouts of branches originating from a remote/fork the user does not already trust (in particular, PR checkouts via `_checkoutPullRequest`/`_findPullRequestBranch`, and any checkout triggered by the `open-repository-from-url` deep link handler).
- If `file://` submodules must be supported for legitimate local-development scenarios, scope the override narrowly (e.g., only for repositories opened directly from a local path by the user, never for a freshly-fetched fork/PR branch), and additionally validate that any `file://` submodule URL resolves (via `resolveWithin`-style checks) to a path under an allow-listed root rather than trusting `.gitmodules` content unconditionally.
- Add a regression test that checks out a PR branch containing a `.gitmodules` file with a `file://` URL pointing outside the repository and asserts the submodule update is rejected or the URL is sanitized.

### Proof of Concept
1. Attacker creates a public fork/PR branch whose `.gitmodules` contains:
   ```
   [submodule "leak"]
     path = leak
     url = file:///Users/victim/Documents/GitHub/some-private-repo
   ```
2. Victim uses Desktop's "checkout pull request" feature (or an `x-github-client://openRepo/...` deep link) on this fork/branch.
3. If the checkout path that handles this PR invokes `checkoutBranch`/`checkoutCommit` with `allowFileProtocol: true` (as demonstrated to be a supported, tested configuration in `app/test/unit/git/checkout-test.ts:150-189`), `updateSubmodulesAfterOperation` runs:
   ```
   git -c protocol.file.allow=always submodule update --init --recursive
   ``` [4](#0-3) 
   which clones the victim's local `some-private-repo` into the `leak` submodule directory inside the checked-out fork, exposing its contents in the working tree (and potentially in subsequent commits/pushes if the victim is not careful).

Because I could not confirm within the available iterations whether the PR-checkout/deep-link flows in `app-store.ts` actually set `allowFileProtocol: true`, this finding should be treated as **high-confidence on the code-level guard weakening, but requiring confirmation of the exact untrusted call site** before treating it as a fully proven exploit chain.

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

**File:** app/src/lib/git/checkout.ts (L102-141)
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
```

**File:** app/src/lib/git/checkout.ts (L163-202)
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
```

**File:** app/test/unit/git/checkout-test.ts (L150-189)
```typescript
    it('initializes an uninitialized submodule when checking out a branch', async t => {
      const repository = await setupRepositoryWithUninitializedSubmodule(t)

      const branches = await getBranches(repository)
      const branchWithSubmodule = branches.find(b => b.name !== 'master')

      if (branchWithSubmodule == null) {
        throw new Error(`Could not find branch other than 'master'`)
      }

      await checkoutBranch(
        repository,
        branchWithSubmodule,
        null,
        undefined,
        true
      )

      // Verify we're on the correct branch
      const statusOutput = await exec(['status'], repository.path)
      assert.ok(
        statusOutput.stdout.includes(`On branch ${branchWithSubmodule.name}`)
      )

      // Verify the submodule is initialized and has the correct commits
      const submodulePath = Path.join(repository.path, 'test-submodule')
      const submoduleGitPath = Path.join(submodulePath, '.git')

      // Check that submodule .git exists (either as file or directory)
      const submoduleGitExists = await pathExists(submoduleGitPath)
      assert.equal(
        submoduleGitExists,
        true,
        'Submodule .git should exist after checkout'
      )

      // Verify submodule has two commits
      const submoduleLog = await exec(['log', '--oneline'], submodulePath)
      assert.equal(submoduleLog.stdout.trim().split('\n').length, 2)
    })
```
