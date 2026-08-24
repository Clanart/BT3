## Analysis

The core broken invariant in the original report is: **a security guard that is supposed to block interaction with a known-malicious actor is disabled/inapplicable at the exact moment that actor is first encountered**, allowing the malicious payload to execute before the protection engages. The closest verified analog in this GitHub Desktop codebase is not a missing check, but a guard that is *explicitly turned off* at clone time — precisely at the point an attacker-controlled repository is first ingested.### Title
Recursive clone explicitly disables Git's built-in clone protection (`GIT_CLONE_PROTECTION_ACTIVE=false`), reopening CVE-2024-32002-class RCE from malicious repositories - (File: `app/src/lib/git/clone.ts`)

### Summary
The `clone()` function unconditionally sets the environment variable `GIT_CLONE_PROTECTION_ACTIVE` to `'false'` while running `git clone --recursive` against an arbitrary, attacker-supplied URL. `GIT_CLONE_PROTECTION_ACTIVE` is the kill-switch Git itself ships to guard against exactly the class of attack fixed in CVE-2024-32002 (malicious repository/submodule layouts on case-insensitive or symlink-supporting filesystems that let a `.git` directory in a submodule be overwritten with attacker content and have hooks executed during clone). By forcing this protection to `false`, GitHub Desktop re-enables that vulnerable clone path for every "Clone repository" / "Open in Desktop" / CLI clone operation.

### Finding Description
`clone()` builds the execution environment for `git clone` like this: [1](#0-0) 

and then invokes clone recursively: [2](#0-1) 

This is directly analogous to the report's broken invariant: a security check that is supposed to gate interaction with a potentially malicious remote object (`bannedNodeRunners` in the LSM case; Git's clone-time submodule/symlink protection here) is not in effect at the precise moment the malicious object is first processed. In the LSM bug, `rotateNodeRunnerOfSmartWallet` (the only path to set the ban flag) could not run until *after* the smart wallet already existed — the gate was structurally unreachable at first contact. Here, the gate (`GIT_CLONE_PROTECTION_ACTIVE`) does exist and is fully capable of blocking the malicious behavior, but the code actively disables it for the exact operation (recursive clone of an untrusted URL) it was designed to protect.

The attacker primitive: a malicious repository (or `git@`/`https://` remote the user is enticed to clone via the "Clone repository" dialog, an `x-github-client://openRepo/...` deep link, or the CLI `clone-url` action) can contain submodules whose `.git` directory / worktree structure is crafted to exploit case-folding or symlink handling once the clone's checkout hooks/config run. This is the same object class explicitly listed as in-scope: "the attacker controls a cloned/fetched repository." The `--recursive` flag on line 92 amplifies exposure because submodules are cloned/checked out automatically without further user interaction, which is the precise vector CVE-2024-32002 attacks.

Other existing guards in this file do not stop the path:
- `isClonePathSensitive()` only validates the destination path is not a sensitive system directory; it says nothing about the content of the cloned repository or its submodules.
- `envForRemoteOperation(url)` only manages credential/auth environment, not clone-content protections.

Neither guard substitutes for the disabled `GIT_CLONE_PROTECTION_ACTIVE` check.

### Impact Explanation
If `GIT_CLONE_PROTECTION_ACTIVE=false` disables the same protection Git added for CVE-2024-32002 (or a functionally equivalent clone-time safety check in the embedded Git version), a crafted repository cloned through Desktop could achieve file writes outside the intended working directory or arbitrary code execution via a malicious post-checkout/hook path during the recursive submodule clone — directly matching the in-scope impacts "code execution, file write or read outside the repo." Because this triggers on the initial, one-shot clone of an attacker-supplied URL (no local access, no prior compromise, no admin rights needed), it satisfies the "unprivileged" and "attacker controls a cloned/fetched repository" criteria.

### Likelihood Explanation
The path is reachable through multiple ordinary, unprivileged user actions already present in the app: manually cloning a URL in the Clone Repository dialog, clicking an `openRepo` deep link (`app/src/lib/parse-app-url.ts`, `open-repository-from-url` handling in `app/src/ui/dispatcher/dispatcher.ts`), or invoking the CLI `clone-url` action — all of which funnel into `clone()`. No unnatural steps are required beyond the normal "clone this repo" flow, so likelihood is high provided the underlying Git binary embedded with Desktop is a version where this protection is meaningful (this dependency on Git version/CVE applicability is the main verification gap — I could not confirm the exact embedded Git version or run the actual exploit here).

### Recommendation
Do not force `GIT_CLONE_PROTECTION_ACTIVE=false`. Remove the override entirely (or set it to `'true'`/leave unset so Git's own default/protective behavior applies), and audit whether `--recursive` should remain enabled by default for untrusted, user-supplied clone URLs versus prompting/confirming before recursively cloning submodules from unknown remotes.

### Proof of Concept
Static evidence (dynamic exploitation was not performed):
1. `app/src/lib/git/clone.ts` lines 81–84 show the environment explicitly setting `GIT_CLONE_PROTECTION_ACTIVE: 'false'` for every clone. [1](#0-0) 
2. Line 92 shows `--recursive` is always passed, so submodules of an attacker-controlled repository are cloned/checked out automatically without additional confirmation. [2](#0-1) 
3. This is reachable from unprivileged, attacker-influenced entry points: `app/src/lib/parse-app-url.ts` (`openRepo` deep link parsing) and `app/src/ui/dispatcher/dispatcher.ts`'s `openOrCloneRepository`/`dispatchCLIAction`, both of which pass an externally supplied `url` straight into the clone flow with no repository-content vetting prior to invoking `git clone`. [3](#0-2) 

**Uncertainty/limitations**: I was unable to determine from the indexed code alone (a) the exact embedded Git version GitHub Desktop ships with, or (b) whether this specific environment variable in that version still gates the CVE-2024-32002 code path (Git's internal implementation/naming of this protection has evolved across releases). Confirming actual exploitability would require building/running the app with a real malicious submodule payload against the bundled Git binary, which is out of scope for static analysis. I recommend a Devin session with terminal access to verify the bundled Git version and reproduce the clone against a PoC malicious repository before treating this as confirmed-exploitable rather than a strong static-analysis finding.

### Citations

**File:** app/src/lib/git/clone.ts (L81-84)
```typescript
  const env = {
    ...(await envForRemoteOperation(url)),
    GIT_CLONE_PROTECTION_ACTIVE: 'false',
  }
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

**File:** app/src/ui/dispatcher/dispatcher.ts (L2215-2233)
```typescript
  private async openOrCloneRepository(url: string): Promise<Repository | null> {
    const state = this.appStore.getState()
    const repositories = state.repositories
    const existingRepository = repositories.find(r =>
      this.doesRepositoryMatchUrl(r, url)
    )

    if (existingRepository) {
      return await this.selectRepository(existingRepository)
    }

    return this.appStore._startOpenInDesktop(() => {
      this.changeCloneRepositoriesTab(CloneRepositoryTab.Generic)
      this.showPopup({
        type: PopupType.CloneRepository,
        initialURL: url,
      })
    })
  }
```
