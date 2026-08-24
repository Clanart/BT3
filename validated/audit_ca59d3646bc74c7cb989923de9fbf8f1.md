Based on my research, I found a plausible Desktop analog centered on **inconsistent enforcement of `protocol.file.allow` across the different code paths that trigger `git submodule update`**, which mirrors the OPCM bug's core pattern: a security-relevant configuration value (there, `contractsContainer`; here, whether `file://` submodule URLs are allowed) must be consistent across multiple parallel entry points, but only one of them explicitly threads/gates it.

### Title
Inconsistent `allowFileProtocol` gating across submodule-update call sites may allow file:// submodule URLs to bypass the checkout-only allowlist - (File: `app/src/lib/git/submodule.ts`, `app/src/lib/git/checkout.ts`)

### Summary
`updateSubmodulesAfterOperation` in `app/src/lib/git/submodule.ts` accepts an explicit `allowFileProtocol: boolean` and only passes `-c protocol.file.allow=always` to `git submodule update --init --recursive` when the caller opts in [1](#0-0) . The only confirmed caller of this function is `checkoutCommit` in `app/src/lib/git/checkout.ts`, which explicitly forwards an `allowFileProtocol` parameter (default `false`) [2](#0-1) . Other git operations that also recursively update submodules (e.g. `clone`, which always runs with `--recursive` [3](#0-2) , and `pull`, which the test suite confirms updates submodule references after `pull()` is invoked [4](#0-3) ) were not found to call `updateSubmodulesAfterOperation` with the same explicit boolean gate — my grep for `updateSubmodulesAfterOperation` only matched `checkout.ts` and `submodule.ts`, not `pull.ts` or `clone.ts` [5](#0-4) .

### Finding Description
The `file://` protocol is dangerous for submodules because a submodule URL controlled by a malicious/compromised repository maintainer can point at an arbitrary local path (e.g. `file:///home/user/.ssh`) and get "cloned" into the working tree during a submodule update, silently pulling local files into the repository. `updateSubmodulesAfterOperation` treats this as opt-in via `allowFileProtocol`, and `checkoutCommit` deliberately threads that flag through from its caller [2](#0-1) . This is the same "parallel-component consistency" shape as the OPCM bug: multiple call sites that perform functionally identical operations (recursive submodule initialization/update) must agree on a shared trust/config value, but the guard is implemented and verified only for one of the several call sites.

### Impact Explanation
If `clone` (which unconditionally runs `git clone --recursive` [3](#0-2) ) or `pull` does not apply the same `protocol.file.allow` denial that `checkoutCommit` opts into by default, a malicious repository with a `file://` submodule URL could have that submodule silently populated with local filesystem content during an initial clone or during a routine pull — bypassing the explicit `allowFileProtocol=false` default that `checkoutCommit` enforces. That local content would then be visible to the user's working tree and could be committed/pushed unknowingly, constituting local file read/exfiltration via an attacker-controlled repository object, one of the explicitly valid impact classes.

### Likelihood Explanation
Medium confidence, not fully confirmed: I was able to confirm the `allowFileProtocol` gate exists and is threaded specifically in `checkoutCommit`/`updateSubmodulesAfterOperation`, and that `clone.ts` always uses `--recursive` without visibly calling `updateSubmodulesAfterOperation`'s gate. I could not retrieve the full contents of `app/src/lib/git/pull.ts` or confirm exactly how/whether `clone`'s `--recursive` flag is separately hardened against `file://` submodules elsewhere (e.g., via a global git config set at `envForRemoteOperation` time). This is a limitation of the codebase index available to me, not a confirmed absence of a guard.

### Recommendation
Verify (with full repository access) whether `clone.ts` and `pull.ts` apply the same `protocol.file.allow` denial-by-default that `checkoutCommit` applies, and if not, thread the same explicit `allowFileProtocol` parameter/default through every code path that triggers `git submodule update --init --recursive`, or set `protocol.file.allow=false` globally for all git invocations by default and only allow it per-operation where explicitly permitted.

### Proof of Concept
Not independently verified end-to-end due to incomplete visibility into `pull.ts`/`clone.ts` submodule handling in this session. A conceptual PoC: an attacker publishes a public repository with a `.gitmodules` entry pointing to `file:///` + a sensitive local path; a victim clones or pulls this repository in Desktop; if the `clone`/`pull` code paths do not deny `file://` submodule URLs the way `checkoutCommit` does, the submodule content is populated from the local filesystem into the visible working tree.

**Given the incomplete verification of `pull.ts`/`clone.ts` internals (index coverage limits prevented full retrieval), I'd recommend starting a Devin session with full repo access to confirm the exact submodule-update code paths for `clone` and `pull` before treating this as a confirmed, ready-to-report finding.**

### Citations

**File:** app/src/lib/git/submodule.ts (L1-15)
```typescript
import { git, IGitStringExecutionOptions } from './core'
import { Repository } from '../../models/repository'
import { SubmoduleEntry } from '../../models/submodule'
import { pathExists } from '../path-exists'
import { executionOptionsWithProgress, IGitOutput } from '../progress'
import {
  envForRemoteOperation,
  getFallbackUrlForProxyResolve,
} from './environment'
import { AuthenticationErrors } from './authentication'
import { IRemote } from '../../models/remote'
import { Progress } from '../../models/progress'
import { join, resolve } from 'path'
import { readFile } from 'fs/promises'

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

**File:** app/src/lib/git/clone.ts (L88-93)
```typescript
  const args = [
    '-c',
    `init.defaultBranch=${defaultBranch}`,
    'clone',
    '--recursive',
  ]
```

**File:** app/test/unit/git/pull/pull-test.ts (L55-107)
```typescript
describe('git/pull', () => {
  describe('with submodules', () => {
    it('updates submodule references after pulling changes', async t => {
      // Setup: Create parent with submodule, clone it
      const { parent, submodule } = await setupRepositoryWithSubmodule(t)

      const cloned = await cloneRepository(t, parent)

      // Initialize submodules in the cloned repo
      await exec(
        ['-c', 'protocol.file.allow=always', 'submodule', 'update', '--init'],
        cloned.path
      )

      const submodulePath = Path.join(cloned.path, 'test-submodule')

      // Verify initial state
      const initialLog = await exec(['log', '--oneline'], submodulePath)
      const initialCommitCount = initialLog.stdout.trim().split('\n').length
      assert.equal(initialCommitCount, 2, 'Should start with 2 commits')

      // Add a new commit to the submodule
      await makeCommit(submodule, {
        commitMessage: 'Third commit in submodule',
        entries: [{ path: 'another-file.txt', contents: 'more content' }],
      })

      // Update the submodule reference in parent and commit
      await exec(
        ['-c', 'protocol.file.allow=always', 'submodule', 'update', '--remote'],
        parent.path
      )
      await exec(['add', 'test-submodule'], parent.path)
      await exec(['commit', '-m', 'Update submodule reference'], parent.path)

      const remote: IRemote = {
        name: 'origin',
        url: parent.path,
      }

      // Pull the changes
      await pull(cloned, remote, undefined)

      // Verify submodule was updated to the new reference
      const finalLog = await exec(['log', '--oneline'], submodulePath)
      const finalCommitCount = finalLog.stdout.trim().split('\n').length

      assert.equal(
        finalCommitCount,
        3,
        'Submodule should now have 3 commits after update'
      )
    })
```
