## Finding: Git's clone-protection safeguard against malicious recursive submodules is explicitly disabled by Desktop

### Title
Recursive clone disables Git's `GIT_CLONE_PROTECTION_ACTIVE` safeguard, reintroducing CVE-2024-32002-class RCE via malicious submodules - (File: `app/src/lib/git/clone.ts`)

### Summary
`app/src/lib/git/clone.ts` performs every repository clone with `--recursive` and simultaneously forces the environment variable `GIT_CLONE_PROTECTION_ACTIVE` to `'false'`. [1](#0-0)  `GIT_CLONE_PROTECTION_ACTIVE` is the guard Git itself introduced (Git 2.45.1) to stop the class of vulnerability behind CVE-2024-32002, where a malicious repository with a crafted submodule (and a case-insensitive/symlink-friendly filesystem or drive-relative path) can trick `git clone --recursive` into writing/overwriting files under the parent `.git` directory (e.g. hooks) and execute them during the clone. Desktop is explicitly turning that Git-side protection off for every clone it performs.

### Finding Description
The broken invariant is: "recursive clones of untrusted, attacker-controlled repositories must run with Git's native anti-submodule-hijack protection enabled." Desktop violates it unconditionally:

- `clone()` builds the args with `'clone', '--recursive'` [2](#0-1) 
- and sets `env = { ...envForRemoteOperation(url), GIT_CLONE_PROTECTION_ACTIVE: 'false' }` [3](#0-2) 

This is the exact same "attacker controls a cloned/fetched repository" primitive from the seed report (there, an untrusted transfer corrupted `exchangeRate`, an internal invariant other logic trusted; here, an untrusted repository/URL is cloned while the invariant "clone protections are active" is corrupted to `false` by Desktop itself). The mitigating guard that would normally stop the attack path (Git's own `GIT_CLONE_PROTECTION_ACTIVE` check) is neutralized before the untrusted clone even starts, so whatever hardening Git ships for recursive submodule clones no longer applies inside Desktop.

Note that `submodule.ts`'s `updateSubmodulesAfterOperation` separately re-enables `protocol.file.allow=always` when `allowFileProtocol` is set [4](#0-3) , which independently reopens the `file://` submodule vector Git also locked down (CVE-2022-39253-style local file exfiltration via submodules), compounding the exposure once combined with clone protection being off.

### Impact Explanation
`clone()` is reachable directly from attacker-influenced input: the Clone Repository dialog, "Open in Desktop" deep links, and repository URLs pasted/clicked by the user, all funnel into this function with a `url` the attacker fully controls. With `GIT_CLONE_PROTECTION_ACTIVE=false` and `--recursive` both in effect, a malicious repository can embed a submodule crafted to exploit the filesystem-confusion RCE that this env var exists to prevent, potentially resulting in file writes outside the intended repository directory and code execution during the clone — matching the "code execution, file write or read outside the repo" impact class from the task's valid-impact list.

### Likelihood Explanation
Likelihood is high because:
- No local/physical access, admin rights, or prior compromise is needed — only clicking a link or entering a URL to clone (the same trigger used throughout the Desktop clone/deep-link flow).
- The disabling is unconditional and global to every clone Desktop performs, not opt-in or gated behind a trust prompt (unlike `add-existing-repository.tsx`'s unsafe-directory warning, which only guards *existing* local repos being added, not remote clones).
- `--recursive` is always passed, so any submodules referenced by the attacker's repository are processed automatically without a chance for the user to review or decline.

### Recommendation
Remove the forced `GIT_CLONE_PROTECTION_ACTIVE: 'false'` override in `app/src/lib/git/clone.ts` so Git's native clone protections remain active for recursive clones of untrusted remotes. If a specific known-safe scenario requires disabling it, gate that override behind an explicit, narrowly-scoped condition (e.g., only for local/trusted re-clones) rather than applying it to all clone operations, and audit `updateSubmodulesAfterOperation`'s `allowFileProtocol` flag similarly so `protocol.file.allow=always` is not applied to submodules originating from untrusted remotes.

### Proof of Concept
1. Craft a malicious Git repository containing a submodule structured to exploit the case-insensitive-filesystem/symlink `.git` confusion patched by Git 2.45.1 (the scenario `GIT_CLONE_PROTECTION_ACTIVE` was introduced to block).
2. Host it and get a victim to clone it via GitHub Desktop (Clone dialog or `x-github-client://openRepo/...` deep link), which calls `clone(url, path, options)`.
3. Observe that `clone()` executes `git -c init.defaultBranch=... clone --recursive -- <url> <path>` with `GIT_CLONE_PROTECTION_ACTIVE=false` set in the environment [5](#0-4) , i.e., with Git's own submodule-hijack protection turned off, reproducing the pre-patch vulnerable behavior inside Desktop regardless of the Git version bundled.

I was not able to directly execute or fully verify the resulting filesystem write/RCE inside this sandbox (no terminal access), so this PoC describes the reachable, code-verified misconfiguration rather than a confirmed exploit chain end-to-end; a background Devin session with terminal access would be needed to build and run the actual malicious submodule payload against a bundled Git version to confirm exploitability.

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
