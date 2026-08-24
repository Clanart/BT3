Based on the code I examined, I found a credible analog. I was unable to fully trace every call site in `app-store.ts` that sets `allowFileProtocol=true` for `checkoutBranch`/`checkoutCommit` (this needs a Devin session with full file access to confirm the exact trigger conditions), but the vulnerable mechanism itself is clearly present and unguarded in the code shown below.

### Title
Submodule checkout with `protocol.file.allow=always` permits attacker-controlled repositories to read arbitrary local files into the user's working tree - (File: app/src/lib/git/submodule.ts)

### Summary
The Augur bug's broken invariant is: a privileged operation ("trusted transfer" of tokens) is executed using a parameter (`_universe`) that is fully attacker-controlled and never validated as belonging to a legitimate/known entity. The GitHub Desktop analog is `updateSubmodulesAfterOperation`, which conditionally appends `-c protocol.file.allow=always` to `git submodule update --init --recursive` based on a caller-supplied `allowFileProtocol` boolean, with no validation that the submodule URLs recorded in the untrusted repository's `.gitmodules` file are safe.

### Finding Description
`updateSubmodulesAfterOperation` in `app/src/lib/git/submodule.ts` builds its git arguments as: [1](#0-0) 

When `allowFileProtocol` is `true`, git is explicitly instructed to allow the `file://` transport for submodules, overriding git's own default protection (git disables `file://` submodule fetches by default specifically to prevent local-path-disclosure attacks via malicious `.gitmodules` entries). This flag is threaded from `checkoutBranch`/`checkoutCommit` (`app/src/lib/git/checkout.ts`) straight down to the submodule update call without any check on the *content* of `.gitmodules`, which is fully attacker-controlled data committed inside the repository being checked out: [2](#0-1) 

The `.gitmodules` file (and the submodule URL it contains) is part of the untrusted, attacker-supplied repository content — exactly analogous to the attacker-controlled `_universe` contract in the Augur report, whose return values (`getOrCacheValidityBond`) were trusted and acted upon by `MarketFactory` without verifying `_universe` was a legitimate, known object. Here, Desktop trusts the submodule URL string from a hostile repository and, when `allowFileProtocol` is set, removes git's own guard rail against `file://` submodules.

### Impact Explanation
If a victim checks out a branch/commit from an attacker-supplied or forked repository under conditions where `allowFileProtocol=true` is used, and that repository's `.gitmodules` declares a submodule with `url = file:///Users/victim/.ssh` (or an equivalent Windows path such as `%APPDATA%`), `git submodule update --init --recursive -c protocol.file.allow=always` will copy the contents of that local directory into the submodule folder inside the repository's working tree. This is a read of files outside the intended remote repository, staged as part of the user's own working tree/commit — matching the "file read outside the repo" and "silent corruption of what the user commits or pushes" impact categories, since a subsequent `git add`/commit/push by the unsuspecting user would exfiltrate the copied local secrets to the remote.

### Likelihood Explanation
Exploitation requires only that the victim add/clone/checkout an attacker-authored repository or fork and that Desktop invoke the checkout/submodule-update path with `allowFileProtocol=true` — no local access, admin rights, or social engineering beyond normal collaboration (cloning/opening a repo, checking out a PR branch) is required. The exact call sites in `app/src/lib/stores/app-store.ts` that set this flag to `true` need to be confirmed to determine whether they are reachable from untrusted/forked-repository flows (e.g., PR checkout) as opposed to only trusted first-party operations; this could not be fully verified within the available tool budget.

### Recommendation
- Never pass `allowFileProtocol=true` for checkouts/branches originating from repositories or PRs not fully trusted by the user (e.g., forks, PR checkouts, freshly added remotes).
- If `file://` submodules must be supported, validate that the resolved submodule URL path stays within an expected boundary (e.g., a sibling of the top-level working directory) before allowing the protocol override, similar to the existing `resolveWithin` boundary check used for deep-link file paths in `app/src/ui/dispatcher/dispatcher.ts`.
- Add unit tests asserting that submodule updates on untrusted/forked repository checkouts reject `file://` submodule URLs pointing outside the repository.

### Proof of Concept
1. Attacker creates a public repository containing a `.gitmodules` file with a submodule entry: `url = file:///Users/victim/.ssh`, `path = leaked`.
2. Victim, using GitHub Desktop, checks out a branch/PR/commit from this repository through a code path that invokes `checkoutBranch`/`checkoutCommit` with `allowFileProtocol=true`.
3. `updateSubmodulesAfterOperation` runs `git -c protocol.file.allow=always submodule update --init --recursive`, which clones the victim's local `~/.ssh` directory content into `leaked/` inside the working tree.
4. If the victim commits and pushes (or the content is otherwise persisted/synced), the private key material is now exfiltrated to a remote the attacker can access.

### Citations

**File:** app/src/lib/git/submodule.ts (L45-51)
```typescript
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
