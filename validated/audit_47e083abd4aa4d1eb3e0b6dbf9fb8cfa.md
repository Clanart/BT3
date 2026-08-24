## Finding

`app/src/lib/git/clone.ts` explicitly disables Git's own clone-time safety check that was added upstream specifically to stop malicious repositories from writing outside the checkout during a recursive clone. [1](#0-0) 

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

### Title
Git clone recursive-checkout protection disabled during clone - (File: app/src/lib/git/clone.ts)

### Summary
`clone()` runs `git clone --recursive` with `GIT_CLONE_PROTECTION_ACTIVE` forced to `'false'` in the child process environment. That variable is Git's own internal safety flag (introduced with the fixes for the class of clone/checkout symlink-in-`.git` vulnerabilities, e.g. CVE-2024-32002) that Git sets to track whether the recursive symlink/hardlink protection has already run for the top-level clone, so it isn't skipped or mis-triggered for nested submodule clones. Desktop's code forces it to `false` on the *initial* invocation, which is not how Git expects it to be used — it disables the very check meant to guard the operation Desktop is performing (`--recursive` clone of an attacker-supplied URL).

### Finding Description
The broken invariant: Git's protection is supposed to be "on" unless Git itself has already verified the top-level clone and is recursing into a submodule clone. Desktop's `clone()` never lets Git decide this — it unconditionally injects `GIT_CLONE_PROTECTION_ACTIVE=false` into the environment for every clone it performs, including the very first, attacker-controlled clone. There is no code path in `clone.ts` that sets this back to `true` or omits it based on whether the clone is top-level or nested.

The clone target `url` is fully attacker/user-controlled (any remote URL, including ones the user is directed to via a phishing link or "Open in Desktop"/`x-github-client://` deep link handled in `app/src/main-process/main.ts`), and the repository content served by that remote is entirely attacker-controlled. A malicious repository can contain submodules or tree entries crafted to abuse symlink/case-sensitivity/reserved-name tricks in `.git` that Git's clone protection is designed to detect and abort on. With the protection deactivated by Desktop, that detection is skipped for the operation being run against untrusted content.

The existing local mitigations in this file (`isClonePathSensitive`) only validate the *destination* path chosen by the user/UI — they do nothing to protect against malicious content once the safety net inside `git clone --recursive` has been switched off, and they run once against the top-level path before the recursive submodule clones happen.

### Impact Explanation
If the disabled check is the one that stops a crafted `.git`-directory symlink/hardlink from escaping the intended checkout tree during recursive submodule cloning, then cloning a malicious repository (or a legitimate-looking repo with a malicious submodule) through Desktop could let attacker content be written or linked outside the repository’s working directory — i.e. silent corruption of the working tree or write outside the intended clone destination, potentially clobbering hooks or files elsewhere on disk. This maps directly to the requested impact class: "attacker controls a cloned/fetched repository … result is code execution, file write … outside the repo."

### Likelihood Explanation
Likelihood is high for exposure (any `git clone <url>` in Desktop, including via CLI `--cli-clone`, the Clone dialog, or "Open in Desktop"/protocol-handler URLs) but the actual exploitability depends on the specific Git version's checkout-time hardening and whether it can still detect the attack through other layers (e.g. `core.protectNTFS`/`core.protectHFS`, which are separate config knobs not disabled here). I could not fully verify against the exact Git version bundled with this app or find prior commit history explaining why this flag was added (the repo only shows a single "Initial commit" for this file), so I cannot confirm with certainty that this line reintroduces a fully exploitable path rather than being a harmless/no-op override in the bundled Git version.

### Recommendation
Remove the explicit `GIT_CLONE_PROTECTION_ACTIVE: 'false'` override and let Git manage this flag itself (it is meant to be set internally by Git during recursive submodule clones, not by the calling application). If this was added to work around a specific error/perf issue, that workaround should be re-derived without disabling Git's built-in clone safety checks, and should at minimum be re-verified against current advisories for recursive-clone/submodule symlink attacks before shipping.

### Proof of Concept
Not independently reproducible from the index alone — reproducing this requires:
1. Building/running this exact Desktop build with a Git version where `GIT_CLONE_PROTECTION_ACTIVE=false` measurably disables the recursive clone safety check (verifiable via `git clone --recursive` with and without the env var against a crafted repo).
2. Crafting a malicious repository with a submodule/tree structure that triggers the specific protection Git added for CVE-2024-32002-class issues (symlinked `.git`/`.git/hooks` style entries) and cloning it in Desktop via the Clone dialog or a `x-github-client://` deep link.

I could not execute Git locally to confirm the exact behavior difference for the bundled `dugite`/Git version, so this should be validated by a Devin session with terminal access before treating it as a confirmed exploit rather than a strong code-level red flag.

### Citations

**File:** app/src/lib/git/clone.ts (L81-93)
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
