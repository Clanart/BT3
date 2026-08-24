### Title
Submodule updates can be forced to run with `protocol.file.allow=always`, reintroducing local-file/`file://` submodule disclosure - ([File: app/src/lib/git/submodule.ts])

### Summary
The external report's core issue is that a smart contract accepts caller-supplied parameters (pool/token addresses) without validating that they satisfy required invariants (matching maturity, factory, decimals, base-pool type) before wiring them into a new, security-critical object. The GitHub Desktop analog is `updateSubmodulesAfterOperation`, which accepts an `allowFileProtocol` flag and, when `true`, unconditionally injects `-c protocol.file.allow=always` into `git submodule update --init --recursive`, without validating anything about the *actual* submodule URLs it is about to fetch (which originate from a cloned/fetched repository's `.gitmodules`, i.e., attacker-controlled data).

### Finding Description
`updateSubmodulesAfterOperation` builds its argument list as: [1](#0-0) 
The `allowFileProtocol` boolean is a caller-supplied trust decision with no runtime check against the submodule entries that will actually be processed - it does not inspect `.gitmodules`, does not restrict which submodule paths/URLs the override applies to, and does not verify the submodule URLs are same-origin with the parent repository's remote. `checkoutBranch`/`checkoutCommit` forward whatever boolean they're given straight through: [2](#0-1) 
Git's own default (`protocol.file.allow=user`) exists precisely to prevent a cloned/fetched repository's `.gitmodules` from silently causing the client to init a submodule from an arbitrary local `file://` path chosen by the repo author (the class of bug behind CVE-2017-1000117/CVE-2018-11235). Passing `-c protocol.file.allow=always` overrides that protection for the whole `submodule update --init --recursive` invocation, meaning any submodule entry in the untrusted `.gitmodules` — not just the one the flag was intended for — is allowed to resolve to a local filesystem path.

This exactly mirrors the report's broken invariant: a security-relevant parameter (`allowFileProtocol`) is threaded into a low-level operation without on-chain (in this case, in-code) verification that the untrusted inputs it will apply to (submodule URLs coming from the fetched repo) actually meet the assumptions under which enabling the override is safe (e.g., that the URL is the one specific trusted path the caller intended, not an attacker-authored `.gitmodules` entry).

### Impact Explanation
If `allowFileProtocol=true` is reachable while operating on a repository whose `.gitmodules`/submodule config is attacker-controlled (a cloned or fetched repo), an attacker can add unrelated submodule entries with `file://` URLs pointing at arbitrary paths on the victim's filesystem (e.g., another local git repo, SSH-agent socket directories, or the user's home directory tree if it contains a `.git`), causing Desktop to check those paths out into the working tree. This is a repo-controlled path that can lead to local file disclosure into the working directory and, depending on git version/config, further escalate via crafted repository content (hooks, symlinks) once file-protocol restrictions are lifted for a `git submodule update` run against untrusted config.

### Likelihood Explanation
I was not able to fully confirm, within the indexed content, every call site in `app/src/lib/stores/app-store.ts` that decides when `allowFileProtocol` is passed as `true` (the grep only located the matches, not their surrounding logic/gating conditions). The function default is safely `false`: [3](#0-2) [4](#0-3) 
so this is only exploitable if some caller flips it to `true` for a general checkout/pull flow over an untrusted repository (as opposed to, e.g., only for Desktop's own test fixtures/dogfood scenarios). Because I could not verify that gating condition, likelihood is uncertain rather than confirmed.

### Recommendation
- Scope any `protocol.file.allow=always` override to the specific submodule path/URL it is intended for (e.g., via `-C <submodule-path>` and per-submodule `git config` rather than a blanket repository-wide override), instead of applying it to the whole `submodule update --init --recursive` invocation.
- Before enabling the override, validate that the submodule URL(s) it will affect match an expected trusted value (e.g., same host/owner as the parent repository, or an explicit allow-list), rather than trusting the boolean flag alone.
- Audit and document every call site that sets `allowFileProtocol=true` to confirm it is never reachable for an arbitrary attacker-authored `.gitmodules` file without additional confirmation from the user.

### Proof of Concept
Conceptual (call-graph based, since the exact `app-store.ts` gating logic could not be retrieved from the index):
1. Attacker publishes a public repository whose `.gitmodules` contains a legitimate-looking submodule plus an extra entry with `url = file:///Users/victim/.ssh` (or another sensitive local path).
2. Victim clones/fetches the repository in Desktop and triggers a checkout/pull flow that internally calls `updateSubmodulesAfterOperation(..., allowFileProtocol = true)`.
3. Because the override is applied to the whole `submodule update --init --recursive` command rather than to a specific validated submodule, git resolves the attacker-added `file://` entry as well, checking out content from the local path into the attacker's repository working tree. [1](#0-0) 

Given the incomplete confirmation of the exact `allowFileProtocol=true` trigger path in `app-store.ts`, treat this as a candidate finding requiring further verification of the call sites before treating it as a confirmed, reproducible vulnerability.

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

**File:** app/src/lib/git/checkout.ts (L163-169)
```typescript
export async function checkoutCommit(
  repository: Repository,
  commit: CommitOneLine,
  currentRemote: IRemote | null,
  progressCallback?: ProgressCallback,
  allowFileProtocol: boolean = false
): Promise<true> {
```
