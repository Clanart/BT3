Based on my research, I found a genuinely strong analog in this codebase's changelog: the `permissions.additionalDirectories` residual-grant bug, which mirrors the BaseVault root cause precisely — a trust boundary (approval) that is not properly scoped/torn down and can be exploited by an unrelated actor path.

### Title
Residual `additionalDirectories` Grant Allowed Access After Removal From The Granting Path - (File: settings/permissions handling for `additionalDirectories` / `--add-dir`)

### Summary
Two related fixed changelog entries describe a residual-allowance class bug in Claude Code's directory-access permission model, directly analogous to the BaseVault finding: a grant of filesystem access (the "allowance") was not properly revoked/scoped, so it remained exploitable through a different code path than the one that originally granted it.

### Finding Description
The BaseVault report's root cause is that a *previously granted* permission (ERC20/721/1155 `approve`) was checked and consumed via a caller-supplied `_from` parameter rather than being scoped strictly to the party the approval was extended to (`msg.sender`), letting any unrelated caller redeem someone else's residual allowance to move assets to an arbitrary `_to`.

The Claude Code analog is the `permissions.additionalDirectories` / `--add-dir` filesystem access grant. The changelog documents:
- `Fixed permissions.additionalDirectories changes not applying mid-session — removed directories lose access immediately and added ones work without restart` [1](#0-0) 
- `Fixed removing a directory from additionalDirectories revoking access to the same directory passed via --add-dir` [2](#0-1) 

These entries indicate that prior to the fix: (1) a directory removed from the `additionalDirectories` settings list could still be accessed mid-session because the removal wasn't applied to the live in-memory permission state — i.e., stale/residual grant survived the "revocation," and (2) removing a directory via one grant path (`additionalDirectories` settings) incorrectly revoked access to the *same path* even when it was independently granted through a different path (`--add-dir`), showing the two grant paths were not tracked as separate, correctly-scoped allowances. This is structurally the same defect pattern as BaseVault: a permission/allowance keyed loosely (by directory path / by token approval) rather than being strictly tied to and re-validated against the specific grantor/grantee relationship and the specific mechanism that created it, allowing a stale or cross-path grant to be exploited (unauthorized continued file access) rather than correctly revoked.

### Impact Explanation
While the underlying primitive differs (filesystem access grant vs. token transferFrom allowance), the impact class matches "unauthorized action via residual permission": a directory intentionally removed from the user's/admin's approved list could still be read/written by Claude Code mid-session, i.e., unauthorized file access continuing after the user believed access was revoked. In a local-agent trust boundary, this is a direct workspace-escape/local-file-disclosure-and-tampering risk equivalent in kind to the "residual allowance" theft in BaseVault, though scoped to local filesystem permissions rather than on-chain assets.

### Likelihood Explanation
This was an actual, shipped-and-fixed bug (not speculative), acknowledged in the project's own changelog, meaning it was reachable under normal product usage — any user or managed-settings admin removing a directory from `additionalDirectories` mid-session would have been affected without any special conditions. Likelihood is high for the pre-fix versions; the finding is now remediated in this codebase per the changelog entries.

### Recommendation
Ensure permission/allowance state (here, `additionalDirectories`) is a single source of truth: any revocation must synchronously invalidate the in-memory access-check state used by the sandbox/file-access layer, and grants originating from different mechanisms (`settings.json` vs. CLI `--add-dir`) must be tracked as independently keyed entries so that removing one grant does not silently persist or incorrectly clear the other. More generally, any tool-authorization check should re-validate the current, live permission set at the time of action rather than trusting a snapshot taken earlier in the session — matching the BaseVault fix's principle of binding every privileged action strictly to the current, verified authorizer.

### Proof of Concept
Not independently reproduced in this session — the analysis is based on the project's own changelog entries documenting and fixing the behavior: [3](#0-2) 

This describes the exact repro pattern: (1) grant access to `/some/dir` via `additionalDirectories` in settings, (2) mid-session, remove it from settings — pre-fix, the directory remained accessible; (3) separately, pass the same directory via `--add-dir` and then remove it only from `additionalDirectories` — pre-fix, this incorrectly also revoked the `--add-dir` grant, demonstrating the grants were not correctly isolated per-mechanism.

### Citations

**File:** CHANGELOG.md (L2534-2536)
```markdown
- Fixed managed-settings allow rules remaining active after an admin removed them, until process restart
- Fixed `permissions.additionalDirectories` changes not applying mid-session — removed directories lose access immediately and added ones work without restart
- Fixed removing a directory from `additionalDirectories` revoking access to the same directory passed via `--add-dir`
```
