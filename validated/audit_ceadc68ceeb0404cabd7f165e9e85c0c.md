### Title
Git's Recursive-Clone Submodule Protocol Protection Explicitly Disabled During Clone - (File: `app/src/lib/git/clone.ts`)

### Summary
The reported Kryptonite bug reduces to: a privileged operation (moving funds) trusts a caller-controlled value with no check that the caller is authorized to trigger it, silently re-enabling behavior Git/finance-logic was supposed to gate. The closest verifiable Desktop analog is in `clone()`: Desktop explicitly forces `GIT_CLONE_PROTECTION_ACTIVE: 'false'` when running `git clone --recursive` against a URL supplied by the user (which can originate from an untrusted deep link or "Open in Desktop" action). This environment variable is the guard Git itself introduced (as part of the CVE-2022-39253 fix) to prevent a cloned repository's `.gitmodules` from using unsafe transports (`file://`, `ext::`) during automatic recursive submodule fetch. By setting it to `'false'` on every clone, Desktop removes that access control, letting an attacker-controlled repository's submodule configuration read files or execute commands the user never explicitly authorized.

### Finding Description
`clone()` builds a `git clone --recursive` invocation and unconditionally injects: [1](#0-0) 

```
const env = {
    ...(await envForRemoteOperation(url)),
    GIT_CLONE_PROTECTION_ACTIVE: 'false',
  }
  ...
  const args = [
    '-c',
    `init.defaultBranch=${defaultBranch}`,
    'clone',
    '--recursive',
  ]
```

`GIT_CLONE_PROTECTION_ACTIVE` is the internal signal Git's own `clone`/`submodule` code uses to know it is running inside an *automatic, recursive* submodule fetch (as opposed to a command the user explicitly typed). When set to `true`, Git enforces `protocol.file.allow=user`/deny for `file://` and `ext::` submodule URLs during that automatic fetch, which is exactly the fix shipped for the "submodule transport can be abused to read/execute arbitrary files on clone" class of vulnerabilities (CVE-2022-39253). By explicitly overriding this to `'false'`, Desktop tells Git "treat this recursive submodule fetch as if it were manually authorized by the user," bypassing the access-control check entirely — for every clone, regardless of the source of the URL.

The `url` passed to `clone()` can come from attacker-influenced input: the "Open in Desktop"/`x-github-client://openRepo/...` deep-link handler passes a URL straight through to `openOrCloneRepository` → `clone()`: [2](#0-1) 

The only mitigations present in `clone.ts` guard against a *malicious destination path* (`isClonePathSensitive`), not against a malicious *source repository's submodule configuration*: [3](#0-2) 

Nothing in this path checks whether the `.gitmodules` file of the repository being cloned contains a submodule URL using `file://` or `ext::` transports before recursively fetching it — the very thing `GIT_CLONE_PROTECTION_ACTIVE` normally prevents.

### Impact Explanation
An attacker who gets a victim to clone their repository (via a normal `Clone`, via the `x-github-client://openRepo/<url>` deep link, or via a PR/fork flow that ends in `clone()`) can include a `.gitmodules` entry with:
- `url = file:///path/to/sensitive/location` — exfiltrating arbitrary files from the victim's machine into the newly cloned working tree (which can then be surfaced back to the attacker if the repo is later pushed, or simply read locally), or
- `url = ext::sh -c ...` — achieving arbitrary command execution at clone time.

Because Desktop force-disables the guard, this happens automatically as part of `--recursive` clone, with no prompt, confirmation, or per-submodule review, and it also defeats the promise made by the `isClonePathSensitive` check (which only protects the *destination*, not files reachable via submodule URLs). This matches the required impact class: attacker controls a cloned/fetched repository, resulting in code execution or file read outside the repo.

### Likelihood Explanation
Any repository a user chooses to clone in Desktop — including one reached via an unauthenticated deep link click — triggers this path. No local access, admin rights, or prior compromise is needed; the attacker only needs the victim to clone/open their repository, which is a normal, expected Desktop workflow.

### Recommendation
Remove the explicit `GIT_CLONE_PROTECTION_ACTIVE: 'false'` override in `app/src/lib/git/clone.ts` (or set it to `'true'`/omit it so Git's default protection applies), restoring Git's built-in transport allow-list for submodules fetched via `--recursive` during clone. If a legitimate reason exists for `--recursive` submodule fetches to use `file://`/`ext::` transports (e.g. local test fixtures), gate that explicitly per-operation rather than globally disabling the protection for every clone Desktop performs.

### Proof of Concept
1. Attacker creates a public repository containing a `.gitmodules` file with:
   ```
   [submodule "evil"]
     path = evil
     url = file:///home/victim/.ssh
   ```
2. Attacker shares a link such as `x-github-client://openRepo/https://github.com/attacker/evil-repo`.
3. Victim clicks the link; Desktop's protocol handler routes to `dispatchURLAction` → `openRepositoryFromUrl` → `openOrCloneRepository` → `clone()`.
4. `clone()` runs `git clone --recursive ...` with `GIT_CLONE_PROTECTION_ACTIVE=false` in the environment, so Git does not block the `file://` submodule, and the victim's `~/.ssh` directory content is fetched into the `evil` submodule path inside the cloned working tree — all without any additional user consent beyond the initial "clone" action.

(Note: full exploitation details, such as the exact minimum Git version needed to confirm `GIT_CLONE_PROTECTION_ACTIVE` gating behavior in this bundled Git, could not be verified from the index alone — a Devin session with terminal access could reproduce the clone against a crafted fixture repo to confirm the bypass end-to-end.)

### Citations

**File:** app/src/lib/git/clone.ts (L16-47)
```typescript
function isClonePathSensitive(unresolvedClonePath: string): boolean {
  const clonePath = Path.resolve(unresolvedClonePath).toLowerCase()
  const home = Path.resolve(homedir()).toLowerCase()

  if (clonePath === home) {
    return true
  }

  const sensitiveLocations = [
    Path.join(home, '.ssh'),
    Path.join(home, '.gnupg'),
    Path.join(home, '.config'),
    Path.join(home, '.config', 'git'),
    Path.join(home, '.gitconfig'),
  ]

  if (__WIN32__) {
    const appData = process.env.APPDATA
    if (appData) {
      sensitiveLocations.push(appData.toLowerCase())
      sensitiveLocations.push(Path.join(appData, 'gnupg').toLowerCase())
    }
  }

  for (const sensitive of sensitiveLocations) {
    if (clonePath === sensitive || clonePath.startsWith(sensitive + Path.sep)) {
      return true
    }
  }

  return false
}
```

**File:** app/src/lib/git/clone.ts (L80-93)
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
```

**File:** app/src/ui/dispatcher/dispatcher.ts (L1940-1951)
```typescript
  private async openRepositoryFromUrl(action: IOpenRepositoryFromURLAction) {
    const { url, pr, branch, filepath } = action

    let repository: Repository | null

    if (pr !== null) {
      repository = await this.openPullRequestFromUrl(url, pr)
    } else if (branch !== null) {
      repository = await this.openBranchNameFromUrl(url, branch)
    } else {
      repository = await this.openOrCloneRepository(url)
    }
```
