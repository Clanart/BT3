Based on the evidence gathered, here is the strongest local-code analog to the "combining two states picks the weaker/unsafe one" bug class from the report.

### Title
Re-enabling `protocol.file.allow=always` for submodule updates permits attacker-controlled `.gitmodules` `file://` URLs to pull arbitrary local paths into the working tree - ([File: app/src/lib/git/submodule.ts])

### Summary
`updateSubmodulesAfterOperation` in `app/src/lib/git/submodule.ts` conditionally prepends `-c protocol.file.allow=always` to the `git submodule update --init --recursive` invocation when its `allowFileProtocol` parameter is `true` [1](#0-0) . This flag is threaded through from `checkoutBranch` and `checkoutCommit` in `app/src/lib/git/checkout.ts`, which both accept an `allowFileProtocol` parameter (defaulting to `false`) and forward it unchanged to the submodule-update call after a checkout completes [2](#0-1) [3](#0-2) . `protocol.file.allow` was hardened to `user` (deny-by-default for embedded submodule/subtree operations) by upstream Git specifically to close the local file-read primitive where an attacker-controlled `.gitmodules` entry uses a `file://` (or local relative-path) URL to clone an arbitrary local path into the victim's working tree. Re-enabling `always` for submodule updates reopens that primitive for whichever checkout paths in Desktop pass `allowFileProtocol: true`.

### Finding Description
`.gitmodules` is data committed to the repository and is fully attacker-controlled in a cloned/forked/malicious repository - the same trust model as the `_from`/`_to` lock objects in the report, where one side of a "merge" operation is attacker-influenced and the code blindly adopts the more permissive value from it. Here, the "state" being combined is the git config used for the submodule operation: Desktop's own execution options plus, when `allowFileProtocol` is `true`, the attacker-supplied submodule URL. If a caller ever sets `allowFileProtocol: true` for a checkout of an untrusted branch/commit (as opposed to purely local test fixtures, where `pull-test.ts` and `submodule-test.ts` show the flag being used deliberately for on-disk fixture repos [4](#0-3) ), a submodule entry such as:

```
[submodule "leak"]
    path = leak
    url = file:///Users/victim/.ssh
```

would cause `git submodule update --init --recursive` (run with `protocol.file.allow=always`) to locally "clone" the attacker-specified path into the repository's working tree as a new submodule directory, since `file://` clones do not perform any network fetch, and Git's own transport-level directory checks for local clones do not prevent copying arbitrary local repository/directory contents this way once the protocol is allowed. No further explicit guard against the target path exists in `updateSubmodulesAfterOperation` beyond gating on the boolean flag itself [5](#0-4) .

### Impact Explanation
If reachable from a checkout of an untrusted repository, this allows: (1) disclosure of local files/directories (SSH keys, cloud credentials, other git repos) into the victim's working tree, which the user may then unknowingly `git add`/commit/push to a remote the attacker controls — a direct instance of "silent corruption of what the user commits or pushes" and "credential/token exfiltration" from the Valid Impact list.

### Likelihood Explanation
Exploitability depends entirely on which UI/dispatcher call sites pass `allowFileProtocol: true` into `checkoutBranch`/`checkoutCommit` for repositories that are not verified to be purely local. `grep` shows additional call sites in `app/src/lib/stores/app-store.ts` and `app/src/ui/dispatcher/dispatcher.ts` that reference `checkoutBranch`/`checkoutCommit`/`allowFileProtocol`, but I was not able to inspect their bodies before running out of tool budget, so I cannot confirm with certainty whether any of them pass `true` for checkouts originating from attacker-controlled/cloned repositories versus only for Desktop's own local-fixture or local-remote scenarios. This is a meaningful gap in verification.

### Recommendation
Confirm every call site that sets `allowFileProtocol: true` and restrict it strictly to cases where the repository's own remote is itself a trusted local path controlled by the user (never merely because the *branch being checked out* was fetched from a possibly-malicious remote). Where possible, avoid `protocol.file.allow=always` entirely and instead allow only `file://` submodule URLs that resolve within an explicitly allow-listed local directory.

### Proof of Concept
1. Attacker publishes a repository containing a `.gitmodules` file with `url = file:///Users/victim/.ssh` (or another sensitive local path guessed/known for the victim's OS).
2. Victim clones/fetches this repository in GitHub Desktop and checks out the malicious branch/commit via a code path that invokes `checkoutBranch`/`checkoutCommit` with `allowFileProtocol: true`.
3. `updateSubmodulesAfterOperation` runs `git -c protocol.file.allow=always submodule update --init --recursive`, cloning the victim's local `.ssh` directory content into the repo's working tree as the submodule.
4. Victim inspects/commits/pushes changes, inadvertently exfiltrating the copied local files to the attacker-controlled remote.

Given the incomplete verification of the exact `allowFileProtocol: true` call sites, this should be treated as a **candidate finding requiring confirmation** rather than a fully proven exploit chain.

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

**File:** app/test/unit/git/pull/pull-test.ts (L63-67)
```typescript
      // Initialize submodules in the cloned repo
      await exec(
        ['-c', 'protocol.file.allow=always', 'submodule', 'update', '--init'],
        cloned.path
      )
```
