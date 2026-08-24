## Analysis

I found a real Desktop analog with the same bug shape as the report: a single "escape hatch" flag that is meant to enable one narrow behavior but, as a side effect, disables an unrelated security guard for the entire operation — on attacker-controlled input (a cloned repository).

### Title
Recursive clone unconditionally disables Git's built-in clone protection via `GIT_CLONE_PROTECTION_ACTIVE=false` - (File: `app/src/lib/git/clone.ts`)

### Summary
GitHub Desktop's `clone()` function always passes `GIT_CLONE_PROTECTION_ACTIVE: 'false'` in the environment for every clone, and always clones with `--recursive`. `GIT_CLONE_PROTECTION_ACTIVE` is the env var Git itself introduced to gate its built-in protections against maliciously crafted repositories/submodules (the class of issues fixed by Git's CVE-2024-32002/32004/32020/32021 fixes, where a repository could smuggle a hostile `.git` directory or hooks through nested/symlinked working trees during a recursive clone). Setting it to `false` unconditionally turns that protection off for every clone Desktop performs, exactly mirroring the report's pattern: a flag meant to enable a narrow behavior (here, recursive submodule cloning) also silently disables an unrelated security control (Git's own clone-time repository-safety checks) for the whole operation.

### Finding Description [1](#0-0) 
`clone()` builds the environment for every invocation with:
```
const env = {
    ...(await envForRemoteOperation(url)),
    GIT_CLONE_PROTECTION_ACTIVE: 'false',
}
```
and always appends `--recursive` to the `git clone` arguments unconditionally, regardless of user choice or repository origin. There is no code path in this function that clones without disabling this protection, and no distinction between trusted (e.g. github.com) and untrusted/arbitrary remote URLs — the same environment is used for `git clone URL path` for any attacker-supplied `url` (e.g., via the CLI `x-github-client://` deep link handling in `dispatchCLIAction`, or a pasted clone URL).

This is analogous to the report's core issue: the intended narrow goal ("allow recursive submodule cloning to proceed") is achieved by disabling a broader safety mechanism (Git's clone protection) that also guards against unrelated attack classes (malicious embedded `.git` directories/symlinks and hook execution during clone of untrusted repositories). Just like setting `accessRegistry = address(0)` disabled both allowlist and denylist checks when only the allowlist needed bypassing, setting `GIT_CLONE_PROTECTION_ACTIVE=false` disables all of Git's clone-time protections when only "recursive clone works" was the goal.

### Impact Explanation
If Git's clone protection guards against on-disk artifacts (symlinked/hard-linked `.git` metadata, malicious submodule paths, or hook files) being materialized/executed during a recursive clone of a specially crafted repository, then disabling it for every clone means any attacker who can get a user to clone their repository through Desktop (a fully attacker-controlled repository) can potentially trigger the underlying protected-against behavior — up to file write outside the intended working directory or execution of hook-like content during the clone, which matches the "attacker controls a cloned/fetched repository ... result is code execution, file write ... outside the repo" category in Valid Impact.

### Likelihood Explanation
Every single clone performed by Desktop (initial clone dialog, "Clone Again" from missing-repository recovery, CLI `clone-url` action, and the underlying git clone workflow) goes through this exact code path, so the disabled protection is on 100% of the time with no opt-out or narrowing to trusted hosts. The likelihood that this specific combination of Git version + `GIT_CLONE_PROTECTION_ACTIVE=false` reintroduces an exploitable path depends on which Git version ships with Desktop and whether the protection it disables is still relevant to that version — I could not verify Git's exact behavior for this variable from the repository alone (this is upstream Git behavior, not something implemented in this codebase), so likelihood should be treated as **uncertain/unverified** rather than confirmed.

### Recommendation
- Confirm with the embedded Git version exactly what `GIT_CLONE_PROTECTION_ACTIVE=false` disables upstream, and whether that protection is still needed for the `--recursive` submodule-clone scenario Desktop is working around.
- If only a narrow class of false-positive protection triggers needs bypassing (e.g., a specific known-safe pattern during submodule clone), scope the bypass to that pattern rather than disabling clone protection wholesale.
- Otherwise, don't disable this protection unconditionally for all clones of arbitrary/untrusted URLs; at minimum, gate it or add compensating checks (e.g., post-clone verification that no unexpected `.git` symlinks/hooks were introduced) so cloning an attacker-controlled repository doesn't get a free pass on Git's own safety checks.

### Proof of Concept
Not independently reproducible from static analysis alone: exploiting this fully requires knowing the exact upstream Git behavior gated by `GIT_CLONE_PROTECTION_ACTIVE` for the bundled Git version, and a crafted malicious repository (e.g., with symlinked `.git`/submodule structures) — this falls outside what could be verified purely from the Desktop codebase in this session. The reachable/exploitable code path in Desktop is confirmed: [2](#0-1) 
which unconditionally sets `GIT_CLONE_PROTECTION_ACTIVE: 'false'` and `--recursive` for every `clone(url, path, options)` call, reachable from user-initiated clone flows and the CLI `clone-url` dispatch: [3](#0-2) 

I was unable to find any code path within this repository where `GIT_CLONE_PROTECTION_ACTIVE` is conditionally set (e.g., only for trusted hosts) — it appears to be unconditional in the current codebase, which supports the finding, but I could not verify the exact security guarantee this variable removes at the Git level since that logic lives in the embedded Git binary, not in this repo's source.

### Citations

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

**File:** app/src/ui/dispatcher/dispatcher.ts (L2050-2058)
```typescript
  public async dispatchCLIAction(action: CLIAction) {
    if (action.kind === 'clone-url') {
      const { branch, url } = action

      if (branch) {
        await this.openBranchNameFromUrl(url, branch)
      } else {
        await this.openOrCloneRepository(url)
      }
```
