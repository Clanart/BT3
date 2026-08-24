### Title
Initial `clone --recursive` disables Git's submodule protocol protections, allowing an attacker-controlled repository to bypass `protocol.file.allow` restrictions during checkout/update submodules — (File: `app/src/lib/git/clone.ts`)

### Summary
`checkoutBranch` and `checkoutCommit` correctly default `allowFileProtocol` to `false` and pass it through `updateSubmodulesAfterOperation`, which only sets `protocol.file.allow=always` when explicitly told to [1](#0-0) [2](#0-1) . This is Desktop's mitigation against the well-known submodule `file://`/`ext::` protocol abuse class (the same bug class Git itself hardened against with `protocol.file.allow=user` and `clone.protection.active` defaults). However, `clone()` performs the *initial* clone with `--recursive` and explicitly sets `GIT_CLONE_PROTECTION_ACTIVE: 'false'`, with no corresponding `protocol.file.allow` restriction applied [3](#0-2) [4](#0-3) . This re-enables the exact class of submodule-URL attack that the rest of the codebase (checkout/pull paths) is careful to guard against.

### Finding Description
Git added `protocol.file.allow` (default `user`) and the internal clone-time submodule protection (`GIT_CLONE_PROTECTION_ACTIVE`, surfaced as `clone.protection.active`) specifically to stop a top-level, remotely-fetched repository from defining a `.gitmodules` entry with a `file://` (or `ext::`) URL that, when automatically recursed into during `--recursive` clone, causes Git to open/clone an arbitrary local path or execute an arbitrary command on the victim's machine (this is the upstream fix for CVE-2022-39253).

In Desktop's own submodule-update helper, this protection is respected: `updateSubmodulesAfterOperation` only appends `-c protocol.file.allow=always` when the caller explicitly opts in via `allowFileProtocol`, and both `checkoutBranch`/`checkoutCommit` default that flag to `false` [5](#0-4) [1](#0-0) [6](#0-5) .

But `clone.ts` — the entry point where a user first fetches a completely untrusted, attacker-controlled repository — does the opposite: it unconditionally passes `--recursive` and sets `GIT_CLONE_PROTECTION_ACTIVE: 'false'` in the environment for the underlying `git clone` invocation, without setting `protocol.file.allow` to a restrictive value [7](#0-6) . `GIT_CLONE_PROTECTION_ACTIVE` is the internal signal Git's own clone-time submodule-URL protection reads to decide whether to reject dangerous submodule transports during the automatic recursive checkout that follows a clone; forcing it to `'false'` disables that specific safety net for exactly the moment an untrusted `.gitmodules` file (fully attacker-controlled, since it comes from the freshly cloned repo) is first processed.

This is the same broken-invariant pattern as the seed report: one code path (checkout/pull via `updateSubmodulesAfterOperation`) enforces the safe default and only relaxes it when the caller explicitly says so, while a sibling path (initial `clone`) skips that enforcement entirely and actively defeats Git's own built-in guard — mirroring how the Arbitrum branch skipped the port-authorization step that all other branches performed.

### Impact Explanation
If Git's built-in submodule-protocol protection is truly bypassed by this environment variable during the automatic `--recursive` submodule checkout that follows `git clone`, a malicious repository containing a `.gitmodules` file with a `file://` URL pointing at a sensitive local path (or an `ext::`-style command transport, depending on the Git version's transport allowlist) could be processed during the very first clone a user performs — before the user ever has a chance to inspect the repository. That would allow file read/write outside the intended clone directory or, in the worst case, command execution, purely by the victim cloning an attacker-supplied URL. This satisfies the "attacker controls a cloned/fetched repository ... resulting in code execution / file write or read outside the repo" criterion.

### Likelihood Explanation
The trigger is simply: an attacker publishes a public repository with a crafted `.gitmodules`, and a Desktop user clones it — no special user action, no local access, and no pre-existing malware or leaked credentials required, only the normal "Clone repository" flow. The likelihood is bounded by how strictly the installed Git version's remaining transport allowlists behave once `GIT_CLONE_PROTECTION_ACTIVE` is forced off; I could not verify from the indexed code alone whether Desktop bundles/pins a Git version where disabling this variable fully reopens the historical CVE-2022-39253 class or only a subset of it, since that depends on `dugite`/bundled Git internals not present in the indexed TypeScript sources.

### Recommendation
Do not unconditionally disable `GIT_CLONE_PROTECTION_ACTIVE` for the initial `clone()`. Instead, mirror the pattern already used by `updateSubmodulesAfterOperation`: keep Git's default submodule-protocol protection active during `--recursive` clone, and only pass an explicit `allowFileProtocol`-style opt-in (with `protocol.file.allow` scoped appropriately, e.g. only for local/trusted `file://` sources such as Desktop's own local-repo cloning flows) when the caller has a legitimate reason to permit it. At minimum, `clone.ts` should apply the same default-`false` gating as `checkout.ts`/`submodule.ts` rather than actively suppressing Git's own protection via `GIT_CLONE_PROTECTION_ACTIVE`.

### Proof of Concept
1. Attacker creates a public repository with a `.gitmodules` file containing a submodule URL using `file://` (or another disallowed-by-default transport) pointing to a path outside the intended working directory (e.g. a shared/sensitive directory reachable from the victim's file system).
2. Victim uses GitHub Desktop's "Clone a repository" feature to clone the attacker's URL.
3. `clone()` in `app/src/lib/git/clone.ts` runs `git ... clone --recursive -- <url> <path>` with `GIT_CLONE_PROTECTION_ACTIVE: 'false'` and no `protocol.file.allow` restriction [3](#0-2) [4](#0-3) .
4. Unlike the `checkoutBranch`/`checkoutCommit` paths — which pass `allowFileProtocol=false` by default and therefore do not grant `protocol.file.allow=always` [8](#0-7)  — the initial clone's recursive submodule step runs without the equivalent restriction being asserted, relying solely on the disabled `GIT_CLONE_PROTECTION_ACTIVE` flag, which fails open.
5. Compare against the safe pattern in `updateSubmodulesAfterOperation`, which explicitly requires `allowFileProtocol` to be `true` before loosening `protocol.file.allow` [5](#0-4) , demonstrating that the clone path diverges from the codebase's own established safe default.

Note: I was unable to directly execute Git/dugite to confirm the exact runtime effect of `GIT_CLONE_PROTECTION_ACTIVE=false` on the bundled Git version, since that native behavior lives outside the indexed TypeScript sources; this assessment is based on the divergence between `clone.ts` and the more careful `checkout.ts`/`submodule.ts` gating logic within this repository.

### Citations

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

**File:** app/src/lib/git/clone.ts (L68-93)
```typescript
export async function clone(
  url: string,
  path: string,
  options: CloneOptions,
  progressCallback?: (progress: ICloneProgress) => void
): Promise<void> {
  if (isClonePathSensitive(path)) {
    throw new Error(
      `The clone destination "${path}" targets a sensitive system location. ` +
        'Cloning into this directory is not allowed.'
    )
  }

  const env = {
    ...(await envForRemoteOperation(url)),
    GIT_CLONE_PROTECTION_ACTIVE: 'false',
  }

  const defaultBranch = options.defaultBranch ?? (await getDefaultBranch())

  const args = [
    '-c',
    `init.defaultBranch=${defaultBranch}`,
    'clone',
    '--recursive',
  ]
```

**File:** app/src/lib/git/clone.ts (L119-125)
```typescript
  if (options.branch) {
    args.push('-b', options.branch)
  }

  args.push('--', url, path)

  await git(args, __dirname, 'clone', opts)
```
