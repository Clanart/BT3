## Title
Git clone explicitly disables Git's built-in CVE-2024-32004 clone-hook protection, enabling remote code execution from a malicious repository - (File: `app/src/lib/git/clone.ts`)

### Summary
GitHub Desktop's `clone()` function runs `git clone --recursive` with the environment variable `GIT_CLONE_PROTECTION_ACTIVE` hard-coded to `'false'`, which deliberately switches off Git's own defense-in-depth check against symlink/hardlink hook-execution attacks during (recursive) clones — the exact class of bug fixed upstream as CVE-2024-32004. Combined with the fact that the clone code path does not use Desktop's own hook-interception sandbox (`interceptHooks`) either, a specially crafted remote repository can get arbitrary hook scripts executed on the victim's machine the moment they clone it in Desktop. [1](#0-0) 

### Finding Description
`clone()` builds the execution environment as:

```
const env = {
  ...(await envForRemoteOperation(url)),
  GIT_CLONE_PROTECTION_ACTIVE: 'false',
}
...
const args = ['-c', `init.defaultBranch=${defaultBranch}`, 'clone', '--recursive']
``` [1](#0-0) 

`GIT_CLONE_PROTECTION_ACTIVE` is the guard Git itself introduced (Git ≥ 2.45.1) to stop a cloned repository (especially with `--recursive`/submodules) from tricking Git into executing hooks via symlinked/hardlinked `.git` directories or hook files that point outside the freshly-cloned tree — the vulnerability tracked as CVE-2024-32004/CVE-2024-32020/CVE-2024-32021 family. By explicitly forcing this variable to `'false'`, Desktop opts the clone operation *out* of Git's own protection instead of relying on the safe (enabled) default.

This is worsened by the fact that `git()` is invoked here with `opts = { env }` — no `interceptHooks` option is set (unlike `commit.ts`, `pull.ts`, `push.ts`, `merge.ts`, which all pass `interceptHooks` into `withHooksEnv`) [2](#0-1) , so Desktop's own hooks-proxy sandbox (which normally intercepts hook execution and lets the user approve/deny it, see `createHooksProxy` in `app/src/lib/hooks/hooks-proxy.ts`) never engages for the clone path either. There is therefore no layer — neither Git's native protection nor Desktop's hook-interception layer — guarding the clone of an attacker-controlled repository.

The attacker's primitive: control the content of a Git repository (or a submodule it references, since `--recursive` is always passed) that the victim clones through Desktop's "Clone repository" flow (e.g., a link/deep-link `x-github-client://openRepo/...` or simply browsing to a malicious repo URL and cloning it). The corrupted invariant is "cloning an untrusted repository must not run repository-supplied code" — broken because the specific Git safeguard for exactly this scenario is turned off, and Desktop's fallback sandbox is not applied to `clone`.

### Impact Explanation
Successful exploitation results in local arbitrary code execution on the victim's machine at clone time, driven entirely by content in a repository the victim does not control (or a submodule pulled in via `--recursive`). This satisfies "attacker controls a cloned/fetched repository ... result is code execution" from the impact criteria, and is more severe than typical repo-content issues since no additional user action beyond clicking "Clone" is required — no commit needs to be checked out, no hook consent dialog is shown, and no file needs to be opened.

### Likelihood Explanation
High. Cloning arbitrary repository URLs (including third-party/public ones) is core, everyday functionality of GitHub Desktop, and `--recursive` submodule fetching is enabled unconditionally. An attacker only needs to publish a malicious repository (with a crafted submodule/hook structure exploiting the symlink/hardlink hook-path attack) and get a victim to clone it via Desktop — a normal, expected interaction, not a contrived one.

### Recommendation
Remove the `GIT_CLONE_PROTECTION_ACTIVE: 'false'` override so Git's native protection (the default, enabled state) applies to `clone`. If there was a specific compatibility reason for disabling it, route the clone operation through the same `interceptHooks`/hooks-proxy sandbox used by `commit`, `pull`, `push`, and `merge` instead of disabling Git's own defenses outright, so any hook invocation during (recursive) clone is intercepted and subject to the same trust/consent model as other operations.

### Proof of Concept
1. Attacker publishes a Git repository containing a submodule (or nested repo) engineered per the CVE-2024-32004 technique — e.g., a submodule whose `.git` is replaced by a symlink/hardlink pointing at a path containing an attacker-controlled `hooks/post-checkout` (or similar) script, such that a normal recursive clone would cause Git to execute it, if Git's clone protection were active it would refuse; with the protection disabled it proceeds.
2. Victim uses GitHub Desktop's "Clone a repository from the Internet" / "File > Clone Repository" (or a `x-github-client://openRepo` deep link) pointing at the attacker's URL.
3. Desktop calls `clone()` in `app/src/lib/git/clone.ts`, which sets `GIT_CLONE_PROTECTION_ACTIVE: 'false'` and runs `git -c init.defaultBranch=... clone --recursive --progress -- <url> <path>` [3](#0-2) .
4. Because the protection env var is disabled and no `interceptHooks` sandbox is applied for this code path, Git executes the malicious hook during the recursive clone/submodule checkout, achieving code execution under the victim's user account, entirely from content the attacker controls in the cloned repository.

### Citations

**File:** app/src/lib/git/clone.ts (L81-125)
```typescript
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

  let opts: IGitStringExecutionOptions = { env }

  if (progressCallback) {
    args.push('--progress')

    const title = `Cloning into ${path}`
    const kind = 'clone'

    opts = await executionOptionsWithProgress(
      { ...opts, trackLFSProgress: true },
      new CloneProgressParser(),
      progress => {
        const description =
          progress.kind === 'progress' ? progress.details.text : progress.text
        const value = progress.percent

        progressCallback({ kind, title, description, value })
      }
    )

    // Initial progress
    progressCallback({ kind, title, value: 0 })
  }

  if (options.branch) {
    args.push('-b', options.branch)
  }

  args.push('--', url, path)

  await git(args, __dirname, 'clone', opts)
```

**File:** app/src/lib/hooks/with-hooks-env.ts (L29-42)
```typescript
export async function withHooksEnv<T>(
  fn: (env: Record<string, string | undefined> | undefined) => Promise<T>,
  path: string,
  opts: IGitExecutionOptions | undefined
): Promise<T> {
  if (!opts?.interceptHooks || !getHooksEnvEnabled()) {
    return fn(opts?.env)
  }

  const hooks = await Array.fromAsync(getRepoHooks(path, opts.interceptHooks))

  if (hooks.length === 0) {
    return fn(opts?.env)
  }
```
