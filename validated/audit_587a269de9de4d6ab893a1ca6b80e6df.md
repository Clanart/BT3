### Title
Trampoline credential-helper token is not scoped to a single command, allowing any process reachable during a git operation to request credentials for a different host - ([File: app/src/lib/trampoline/trampoline-tokens.ts])

### Summary
The report's bug class is a **check-then-act gap**: a value is validated once (`used_slots < availableSlots`), but the entitlement it protects is not atomically consumed, so multiple concurrent callers can pass the same stale check. The closest verified analog in this codebase is the trampoline token mechanism used to gate access to GitHub Desktop's local credential/askpass server during git operations: `isValidTrampolineToken` performs a simple set-membership check [1](#0-0) , and the token is only revoked once, at the very end of the *entire* git operation, not after a single use [2](#0-1) .

### Finding Description
`withTrampolineToken` mints one random token per git invocation, hands it to the spawned `git` process (via environment, presumably consumed by `trampoline-environment.ts`), and only calls `revokeTrampolineToken` in the `finally` block after the whole operation (`fn(token)`) resolves [2](#0-1) . The validity check itself, `isValidTrampolineToken`, is a pure membership test on a `Set<string>` with no per-request consumption, no binding to a specific remote/host, and no binding to a specific command [3](#0-2) .

This mirrors the reported flaw exactly: `findActivePromoByRefCode` checked "is a slot available" without reserving it, and `createUserPromo` used the promo without re-checking or atomically decrementing. Here, the trampoline server presumably checks "is this token currently valid" (`isValidTrampolineToken`) and, if so, serves whatever credential/askpass request comes in — for the entire lifetime of the outer git command, not for a single request. Because a clone/fetch/push against an attacker-controlled remote can run arbitrary subprocesses during that window (via `.git/hooks`, submodule URLs pointing to other commands, `core.fsmonitor`, filters, or LFS smudge/clean commands), any such subprocess spawned inside that window inherits or can discover the still-valid token and can invoke the trampoline server itself, requesting credentials for a **different** hostname than the one Desktop is actually authenticating against.

I was not able to fully verify, within the available context, whether `trampoline-server.ts` / `trampoline-credential-helper.ts` additionally validate that the requested host matches the operation's original target host — that would be the guard that closes this gap, analogous to the recommended fix of "checking for available slots in `createUserPromo`". If no such per-request/per-host binding exists, the token behaves like the report's unreserved slot: valid for the whole checked window, usable by any caller that presents it, and not narrowed to the single legitimate use.

### Impact Explanation
If a malicious repository (attacker controls the cloned/fetched content, matching the required threat model) can trigger a subprocess during the trampoline-token window, and that subprocess can reach the trampoline server with the leaked token, it could obtain GitHub credentials/tokens scoped to accounts unrelated to the current remote — i.e., credential exfiltration for other GitHub Desktop accounts. This is High impact per the "Valid Impact" criteria (credential/token exfiltration via an attacker-controlled repository).

### Likelihood Explanation
Likelihood is Low-Moderate: it requires (a) a malicious repository able to run a subprocess during a Desktop-initiated git operation (achievable via hooks, submodules, or filter/clean/smudge commands configured in-repo, though Desktop may sandbox some of these), and (b) that subprocess being able to reach the trampoline server (typically bound to localhost with the token as the only secret) and successfully request credentials for a host other than the one being operated on, assuming no host-binding check exists server-side.

### Recommendation
Bind each trampoline token to the specific operation/remote it was issued for (host, and ideally a single expected credential request), and validate that binding — not just token presence — inside the trampoline server / credential-helper handler before returning any credential material. Alternatively, make tokens single-use (consumed atomically on first successful request) rather than valid for the full duration of the git command, closing the same "check without atomic consumption" gap identified in the original report.

### Proof of Concept
Not independently reproducible from the indexed code alone — reproducing this would require inspecting `app/src/lib/trampoline/trampoline-server.ts` and `trampoline-credential-helper.ts` to confirm whether they authorize a credential/askpass request based on token validity alone versus token+host binding, and then crafting a malicious repository with a hook/submodule/filter command that calls back into the trampoline HTTP/socket server mid-operation requesting credentials for an arbitrary hostname. I was unable to complete this verification within the available tool budget, so this should be treated as a **candidate finding requiring confirmation** of the server-side authorization logic before being considered conclusively exploitable — start a Devin session with repository access to read `trampoline-server.ts`, `trampoline-credential-helper.ts`, and `trampoline-environment.ts` in full to confirm or refute the host-binding gap.

### Citations

**File:** app/src/lib/trampoline/trampoline-tokens.ts (L1-16)
```typescript
const trampolineTokens = new Set<string>()

function requestTrampolineToken() {
  const token = crypto.randomUUID()
  trampolineTokens.add(token)
  return token
}

function revokeTrampolineToken(token: string) {
  trampolineTokens.delete(token)
}

/** Checks if a given trampoline token is valid. */
export function isValidTrampolineToken(token: string) {
  return trampolineTokens.has(token)
}
```

**File:** app/src/lib/trampoline/trampoline-tokens.ts (L24-37)
```typescript
export async function withTrampolineToken<T>(
  fn: (token: string) => Promise<T>
): Promise<T> {
  const token = requestTrampolineToken()
  let result

  try {
    result = await fn(token)
  } finally {
    revokeTrampolineToken(token)
  }

  return result
}
```
