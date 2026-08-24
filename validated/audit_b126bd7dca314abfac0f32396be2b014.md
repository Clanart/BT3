Given the constraints, I was unable to fully trace every call site that decides when `allowFileProtocol` is `true` in `app-store.ts` before running out of tool iterations. I'll present the strongest, evidence-backed analog and flag that specific gap.

### Title
Recursive submodule update globally re-enables `file://` protocol for all nested submodules regardless of trust origin - (File: app/src/lib/git/submodule.ts)

### Summary
The Union bug's broken invariant was: a value/decision that should be scoped to a single entity (one staker's exposure to one loan) was instead applied uniformly across N unrelated entities, multiplying the effect by N. The closest verified Desktop analog is `updateSubmodulesAfterOperation()` in `app/src/lib/git/submodule.ts`, which applies a single `-c protocol.file.allow=always` git config flag together with `submodule update --init --recursive` in one invocation. This means a single boolean decision (`allowFileProtocol`) — computed once outside this function — is silently propagated to every submodule at every nesting depth, without re-validating each individual submodule's own URL/origin.

### Finding Description
`updateSubmodulesAfterOperation` builds its args as: [1](#0-0) 

When `allowFileProtocol` is `true`, git is invoked with `protocol.file.allow=always` and `--recursive`. Git's `--recursive` flag causes this single config override to apply transitively to every submodule discovered while walking the (attacker-controlled) `.gitmodules` tree, at any depth, including submodules added after the top-level trust decision was made. The function receives `allowFileProtocol` as a single boolean parameter from its caller and has no per-submodule URL check: [2](#0-1) . There is no code path in this function (or in `checkout.ts`, which forwards the same flag unchanged) that inspects individual `submodule.<name>.url` entries before the recursive `git submodule update` call: [3](#0-2) .

This mirrors the Union pattern precisely: the "amount" (here, permission to use `file://`/local-path submodule URLs) that should be scoped to a single, already-validated entity (the top-level repository's own remote) is instead broadcast unmodified to an attacker-controlled, unbounded set of entities (every nested submodule), because the implementation trusts a single upfront flag rather than re-deriving it per item.

### Impact Explanation
If an attacker crafts a malicious repository (or injects malicious `.gitmodules`/nested-submodule entries into an otherwise legitimate repo the victim clones/fetches) containing submodules with `file://` or bare local-path URLs pointing at sensitive host locations, and the top-level operation is performed with `allowFileProtocol=true`, git's recursive submodule update will "clone" (i.e., copy) the contents of those local paths into the repository's working tree. This is the same bug class Git itself mitigated via CVE-2022-39253 by disabling `protocol.file` by default; Desktop's local override re-enables it recursively without scoping to a per-submodule trust decision. The resulting copied files land inside the user's working directory, where GitHub Desktop's UI would show them as untracked/new files — the user could unknowingly stage, commit, and push local file contents (e.g., SSH keys, config files) to a remote, i.e., "silent corruption of what the user commits or pushes" and "credential/file exfiltration."

### Likelihood Explanation
This requires: (1) `allowFileProtocol` to be `true` for the checkout/pull/clone operation, and (2) the repository being operated on to contain (directly or via a nested submodule) a `file://`/local-path submodule URL pointing outside the intended clone tree. I was not able to fully verify, within the available iterations, every caller in `app-store.ts` that sets `allowFileProtocol=true` for user-initiated flows (8 matches were found but not traced to completion), so I cannot confirm today whether this flag is realistically `true` for repositories cloned from arbitrary/untrusted remote URLs (e.g., GitHub-hosted forks) versus only for local/`file://`-origin repositories the user explicitly opened. This uncertainty affects whether the "attacker-controlled cloned repository" precondition in the Valid Impact section is met in practice — if `allowFileProtocol` is only ever `true` for repositories the user already trusts as local, the exploitable surface is narrower than described.

### Recommendation
Do not propagate a single top-level `allowFileProtocol` decision to the entire recursive submodule tree. Instead, resolve and validate each submodule's URL scheme individually (recursively) before allowing `git submodule update` to touch it, or restrict `protocol.file.allow=always` to only the exact submodule URL(s) that were already vetted as being part of the same trusted local clone origin, consistent with Git upstream's default-deny stance for `file://` submodules.

### Proof of Concept
1. Create a repository `victim-repo` containing a `.gitmodules` entry referencing a nested submodule `evil` whose URL is `file:///Users/target/.ssh` (or a Windows equivalent).
2. Get `victim-repo` cloned/opened in Desktop such that a checkout/pull path invokes `checkoutBranch`/`checkoutCommit`/`pull` with `allowFileProtocol=true` [4](#0-3) .
3. `updateSubmodulesAfterOperation` runs `git -c protocol.file.allow=always submodule update --init --recursive`, which recursively resolves the `evil` submodule and copies the contents of `~/.ssh` into `victim-repo/evil` [5](#0-4) .
4. The copied files now appear as new/untracked files in the Desktop working directory view and can be committed and pushed by the user, exfiltrating them to a remote the attacker controls.

Note: step 2's precondition (`allowFileProtocol=true` being reachable for an untrusted/attacker-supplied repository) is the part I could not fully confirm before running out of tool budget — a Devin session with full file access would need to trace all `app-store.ts` call sites setting this flag to confirm end-to-end exploitability from a purely remote/untrusted clone.

### Citations

**File:** app/src/lib/git/submodule.ts (L29-37)
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
```

**File:** app/src/lib/git/submodule.ts (L45-54)
```typescript
  const args = [
    ...(allowFileProtocol ? ['-c', 'protocol.file.allow=always'] : []),
    'submodule',
    'update',
    '--init',
    '--recursive',
  ]

  if (!progressCallback) {
    await git(args, repository.path, 'updateSubmodules', opts)
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
