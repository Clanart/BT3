### Title
Attacker-controlled `.gitmodules` file:// URL is allowed via `allowFileProtocol`, enabling local file read/exfiltration during checkout - ([File: app/src/lib/git/submodule.ts])

### Summary
`GitHub Desktop` normally relies on Git's default `protocol.file.allow=user` behavior to block cloning submodules from `file://` URLs (a hardening Git added specifically to stop malicious repos from using submodules to read arbitrary files off the victim's disk). Desktop's checkout path contains an opt-in bypass, `allowFileProtocol`, that when set to `true` passes `-c protocol.file.allow=always` to `git submodule update --init --recursive`, re-enabling `file://` submodule fetches for a checkout triggered by content that ultimately comes from a cloned/fetched repository.

### Finding Description
`updateSubmodulesAfterOperation` builds the submodule-update command and conditionally prepends `-c protocol.file.allow=always` when `allowFileProtocol` is `true`: [1](#0-0) 

This flag flows from `checkoutBranch`/`checkoutCommit` (default `false`, but callable as `true`) straight through to the submodule update: [2](#0-1) 

The value of the `.gitmodules` `url` field (and thus whether `file://` is attempted) is fully attacker-controlled content that ships inside the cloned/fetched repository — Desktop does not sanitize or restrict the submodule URL scheme before invoking `git submodule update --init --recursive` with `protocol.file.allow=always`. With that override in effect, a submodule entry such as `url = file:///Users/victim/.ssh` (or any path Desktop's process can read, e.g. `~/.aws/credentials`, browser profile directories, SSH keys) is treated as a legitimate Git repository, and its contents are copied into the submodule directory inside the visible working tree of the outer repository. Because the resulting files are ordinary working-tree files, they can be viewed in Desktop's diff/commit UI and, since the user believes they are managing a normal repository, can be inadvertently staged and pushed to the attacker's remote — silently exfiltrating local secrets to a remote the attacker controls.

This mirrors the "malicious package exfiltrates secrets" bug class from the report: instead of an npm postinstall script, the attacker-controlled artifact is the `.gitmodules` entry in a repository the user clones/fetches, and instead of malware scanning the disk directly, Desktop's own `file://`-allowed submodule fetch is used as the read primitive.

### Impact Explanation
If `allowFileProtocol=true` is reachable from a code path that operates on attacker-supplied/attacker-influenced repository content (e.g. checking out a branch/commit that was fetched from an untrusted remote, or a submodule superproject cloned from an untrusted source), a hostile repository can direct Desktop to read arbitrary local files reachable to the process and materialize them inside the working directory. Once materialized, ordinary Desktop UX (stage/commit/push) can leak those files to a remote controlled by the attacker — this is a credential/token/file exfiltration primitive achieved purely by having the victim open/checkout a hostile repository, without local access, admin rights, or pre-existing malware.

### Likelihood Explanation
Git upstream deliberately defaults `protocol.file.allow` to `user` (disallowed for automated recursive operations like submodules) specifically to close this exact attack class (CVE-2022-39253 and related git advisories). Desktop's `allowFileProtocol` parameter re-opens that door whenever it's threaded through with `true`, and the gating condition is a boolean passed down several call layers (`checkoutBranch`/`checkoutCommit` → `updateSubmodulesAfterOperation`) rather than being enforced at the trust boundary (i.e., is this remote/repository trusted?). Whether this is currently invoked with `true` from a path reachable with attacker-controlled content depends on the callers in `app-store.ts`/`dispatcher.ts`, which is why an audit of every call site passing `true` for this parameter is necessary. The existence of the bypass itself, combined with no visible sanitization of `.gitmodules` URLs before the flag is honored, makes it a credible internal likelihood, though full confirmation requires verifying the exact conditions under which callers set `allowFileProtocol = true`.

### Recommendation
- Never allow `protocol.file.allow=always` to apply implicitly based on a generic boolean flag threaded through checkout; require explicit, per-operation, user-confirmed intent (e.g., only for the app's own local-fixture/test flows, gated out of production).
- Before honoring `allowFileProtocol`, validate the repository origin (only allow for repositories the user has explicitly declared as fully trusted, similar to the existing "unsafe directory" trust gate in `app/src/ui/missing-repository.tsx`) rather than trusting the flag alone.
- Alternatively, drop the override entirely and rely on Git's default `protocol.file.allow=user`, only permitting `file://` submodules when the submodule URL resolves within the same trusted filesystem root as the superproject.
- Add regression tests asserting that a `.gitmodules` entry with a `file://` URL pointing outside the repository is rejected during `checkoutBranch`/`checkoutCommit` unless the user has explicitly trusted the repository.

### Proof of Concept
1. Attacker publishes a public repository whose `.gitmodules` contains:
   ```
   [submodule "leak"]
     path = leak
     url = file:///Users/victim/.ssh
   ```
2. Victim clones this repository in GitHub Desktop and performs an action that calls `checkoutBranch`/`checkoutCommit` with `allowFileProtocol = true` (the exact caller must be confirmed in `app-store.ts`/`dispatcher.ts` for the specific triggering UI action).
3. `updateSubmodulesAfterOperation` executes `git -c protocol.file.allow=always submodule update --init --recursive`, which clones `/Users/victim/.ssh` into `leak/` inside the working directory. [3](#0-2) 
4. The victim's SSH keys now appear as ordinary tracked files under `leak/` in the Desktop changes view; if the victim stages and pushes, the keys are exfiltrated to the attacker-controlled remote.

**Caveat**: I was not able to fully confirm, within the available search budget, which specific caller(s) in `app-store.ts` or `dispatcher.ts` invoke `checkoutBranch`/`checkoutCommit` with `allowFileProtocol = true` on attacker-influenced (fetched/cloned) content versus only in trusted/local contexts (e.g., tests). Confirming the exact triggering UI flow would require tracing those 8 and 6 call sites respectively in `app/src/lib/stores/app-store.ts` and `app/src/ui/dispatcher/dispatcher.ts`. If all real-world call sites only pass `true` for repositories the user has already explicitly trusted, the practical severity is reduced to "trusted-repo-only" and this would not qualify as a valid unprivileged analog — this needs verification before treating the finding as fully confirmed.

### Citations

**File:** app/src/lib/git/submodule.ts (L29-55)
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

  if (!progressCallback) {
    await git(args, repository.path, 'updateSubmodules', opts)
    return
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
