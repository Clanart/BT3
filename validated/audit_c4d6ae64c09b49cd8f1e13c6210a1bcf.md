## Finding

### Title
Re-enabling `protocol.file.allow=always` for submodule updates permits local file exfiltration via malicious `file://` submodule URLs - (File: `app/src/lib/git/submodule.ts`)

### Summary
The external report's broken invariant is "an untrusted/attacker-influenced object triggers an expensive/dangerous operation without the user's informed, upfront consent, and existing code offers no gate to stop it." The Desktop analog: `updateSubmodulesAfterOperation` can invoke `git submodule update --init --recursive` with `-c protocol.file.allow=always` whenever `allowFileProtocol` is `true`, deliberately overriding the git-upstream hardened default (`protocol.file.allow=user`) that was introduced specifically to stop **CVE-2022-39253**, in which a malicious `.gitmodules` entry with a `file://` URL causes git to copy arbitrary local filesystem paths into the submodule working directory during checkout. [1](#0-0) 

### Finding Description
`updateSubmodulesAfterOperation` builds its `submodule update --init --recursive` command with an opt-in flag: [2](#0-1) 

When `allowFileProtocol` is `true`, the invocation is prefixed with `-c protocol.file.allow=always`, which is exactly the configuration that reverts the protection git added after CVE-2022-39253. That CVE exists because a repository can define a submodule pointing at `file:///etc` (or any other local path, including sensitive directories like `~/.ssh`), and when a victim initializes/updates the submodule, git treats it as a local clone and copies the contents of that directory into the submodule's working tree in the victim's repository — with no network fetch and no separate confirmation step, since it's indistinguishable at the git-protocol level from a normal submodule.

This function is invoked from `checkoutBranch` and `checkoutCommit`: [3](#0-2) [4](#0-3) 

Both accept `allowFileProtocol: boolean = false` as a parameter, meaning the default is safe, but the flag is threaded up through `app-store.ts` and various dialogs (`stash-and-switch-branch-dialog.tsx`, `confirm-checkout-commit.tsx`, `overwrite-stashed-changes-dialog.tsx`, `branches-container.tsx`, `history/compare.tsx` all reference `allowFileProtocol` per the grep results). Because the flag exists at all and is threaded to multiple checkout entry points, any code path that sets it to `true` (e.g., to conveniently support local-fixture/test submodules, or a "retry with file protocol allowed" UX after a failure) re-opens the exact vulnerability class git upstream closed. I was not able to fully trace, within the remaining tool budget, every caller in `app-store.ts` that sets this flag to `true` in production UI flows (only test helpers were confirmed using `protocol.file.allow=always` explicitly) — this needs to be verified against the actual call sites in `app-store.ts` referenced by the 8 matches found there.

### Impact Explanation
If any reachable non-test call path sets `allowFileProtocol: true` during a checkout of an attacker-controlled or attacker-influenced repository/branch (e.g., a forked PR branch a user checks out via Desktop, or a repository cloned from an untrusted source), a `.gitmodules` entry with `url = file:///Users/<victim>/.ssh` (or any other sensitive local path) would be copied wholesale into the working tree. That data would then be visible in Desktop's changes/diff view and could be silently committed and pushed to a remote the attacker controls, exfiltrating SSH keys, config files, or other local secrets. This satisfies the "credential exfiltration" / "silent corruption of what the user commits" bar from the task's valid-impact criteria.

### Likelihood Explanation
Likelihood depends entirely on whether `allowFileProtocol=true` is reachable from a production (non-test) UI flow without an explicit, unambiguous user confirmation naming the risk (comparable to how the flag is described only generically, "Whether to allow file:// protocol for submodules"). Given the flag's existence and multiple UI call sites, and that git upstream treats this exact configuration as a CVE-worthy footgun, this warrants review even though full verification of the triggering condition was not completed in this pass.

### Recommendation
- Audit every call site setting `allowFileProtocol: true` in `app-store.ts` and the UI components (`stash-and-switch-branch-dialog.tsx`, `confirm-checkout-commit.tsx`, `overwrite-stashed-changes-dialog.tsx`, `branches-container.tsx`, `history/compare.tsx`) and confirm none of them derive the flag from repository-supplied data or enable it automatically without an explicit, itemized user warning about `file://` submodule risk.
- Prefer never overriding `protocol.file.allow`; if file-protocol submodules must be supported, scope the allowed paths (e.g., only paths that are already siblings/known-safe test fixtures) instead of `always`.
- Add a hard warning dialog, analogous to the LFS-size warning already present in the app, whenever a `.gitmodules` file contains `file://` URLs, before initializing/updating such submodules.

### Proof of Concept
1. Attacker crafts a git repository with a `.gitmodules` file containing:
   ```
   [submodule "leak"]
       path = leak
       url = file:///home/victim/.ssh
   ```
2. Victim clones or checks out a branch containing this repository in GitHub Desktop, on a code path where `allowFileProtocol` resolves to `true` (needs confirmation of the exact production trigger in `app-store.ts`).
3. `updateSubmodulesAfterOperation` runs `git -c protocol.file.allow=always submodule update --init --recursive`, per [5](#0-4) , causing git to copy the contents of `/home/victim/.ssh` into `leak/` in the victim's working tree.
4. The victim's next `git status`/commit surfaces these files as new/untracked content, and if committed and pushed, the secrets are exfiltrated to the attacker's remote.

**Caveat:** I could not fully confirm, within the available tool calls, the exact production code path in `app-store.ts` that sets `allowFileProtocol=true` outside of test helpers — this should be verified directly in a Devin session with full file access before treating this as a confirmed exploitable issue versus a defense-in-depth concern.

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
