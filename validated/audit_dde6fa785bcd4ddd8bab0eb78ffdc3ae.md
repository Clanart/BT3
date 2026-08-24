No vulnerability found for this question.

**Reasoning:**

The premise misreads how `checkConstraint` is wired together in `endpointSatisfies`: [1](#0-0) 

For a given constraint object `{ dotcom, ghe, es }`, three independent `checkConstraint` calls are OR'd together, each with its *own* `epMatchesType`:
- `checkConstraint(dotcom, isDotCom(ep))` — uses `isDotCom(ep)`, not `isGHES(ep)`.
- `checkConstraint(ghe ?? dotcom, isGHE(ep))` — uses `isGHE(ep)`.
- `checkConstraint(es, isGHES(ep), ...)` — this is the only branch that uses `isGHES(ep)` as `epMatchesType`.

For `supportsRerunningIndividualOrFailedChecks` and `supportsAliveSessions`, which are defined as `endpointSatisfies({ dotcom: true })`: [2](#0-1) 

`es` is `undefined` (and `ghe` defaults to `dotcom`, i.e. `true`, but that branch checks `isGHE(ep)`, not `isGHES(ep)`). So the third `checkConstraint` call becomes `checkConstraint(undefined, isGHES(ep), ...)`. Looking at `checkConstraint`: [3](#0-2) 

When `epConstraint === undefined`, the function returns `false` unconditionally ("Denial of endpoint type regardless of version") — it never even reaches the `epMatchesType` check. So even if `isGHES(ep)` were `true` for a given endpoint, that branch contributes `false`, not `true`. There is no code path where `isGHES` being `true` causes a dotcom-only capability to resolve to `true`; the only way to get `true` out of `supportsAliveSessions`/`supportsRerunningIndividualOrFailedChecks` is for `isDotCom(ep)` itself to return `true`.

`isDotCom` performs a strict comparison: [4](#0-3) 

It requires the endpoint to equal `getDotComAPIEndpoint()` exactly, or the parsed `hostname` to be exactly `api.github.com` or `github.com` — not a prefix/suffix match, so it cannot be spoofed by a look-alike hostname. The `ep` value itself is derived from account/endpoint configuration (added via sign-in flows), not from attacker-controlled repository content, API response bodies, or arbitrary deep-link parameters that would let an unprivileged attacker inject a value that both parses to hostname `github.com`/`api.github.com` and is actually a forged GHES server — that would require control over how the user's own account endpoint was registered, which is outside the defined attack surface (no attacker-controlled remote/API-object path reaches the `ep` argument of `isDotCom`).

Given `checkConstraint`'s branch selection (not the value implied in the question) and `isDotCom`'s strict hostname equality, the described "spoofed `isGHES` matches, dotcom-only capability granted" scenario does not correspond to actual code behavior, and no attacker-controlled input path was identified that reaches `isDotCom`/`isGHES` in a way that could produce the claimed corruption.

### Citations

**File:** app/src/lib/endpoint-capabilities.ts (L47-54)
```typescript
export const isDotCom = (ep: string) => {
  if (ep === getDotComAPIEndpoint()) {
    return true
  }

  const { hostname } = new URL(ep)
  return hostname === 'api.github.com' || hostname === 'github.com'
}
```

**File:** app/src/lib/endpoint-capabilities.ts (L107-115)
```typescript
  // Denial of endpoint type regardless of version
  if (epConstraint === undefined || epConstraint === false) {
    return false
  }

  // Approval of endpoint type regardless of version
  if (epConstraint === true) {
    return epMatchesType
  }
```

**File:** app/src/lib/endpoint-capabilities.ts (L129-134)
```typescript
export const endpointSatisfies =
  ({ dotcom, ghe, es }: VersionConstraint, getVersion = getEndpointVersion) =>
  (ep: string) =>
    checkConstraint(dotcom, isDotCom(ep)) ||
    checkConstraint(ghe ?? dotcom, isGHE(ep)) ||
    checkConstraint(es, isGHES(ep), getVersion(ep) ?? assumedGHESVersion)
```

**File:** app/src/lib/endpoint-capabilities.ts (L147-159)
```typescript
export const supportsRerunningIndividualOrFailedChecks = endpointSatisfies({
  dotcom: true,
})

/**
 * Whether or not the endpoint supports the retrieval of action workflows by
 * check suite id.
 */
export const supportsRetrieveActionWorkflowByCheckSuiteId = endpointSatisfies({
  dotcom: true,
})

export const supportsAliveSessions = endpointSatisfies({ dotcom: true })
```
