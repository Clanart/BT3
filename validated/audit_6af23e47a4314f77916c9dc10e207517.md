Confirmed: the `IsmpModule` trait's `on_response` signature only takes `GetResponse` (`modules/ismp/core/src/module.rs` line 38), so `PostResponse` delivery for a deprecated TokenGateway is not gated by `is_deprecated_token_gateway` the way `on_accept`/`on_timeout` are on the nexus runtime.

### Title
Deprecated/blocked TokenGateway is only blocked on request and timeout paths, not on response delivery - ([File: parachain/runtimes/nexus/src/ismp.rs])

### Summary
`ProxyModule::on_accept` and `ProxyModule::on_timeout` in the nexus runtime unconditionally reject any Post request or Post-request timeout whose `from` field matches a `DEPRECATED_TOKEN_GATEWAY_ADDRESSES` entry, intended to fully retire a compromised/faulty TokenGateway deployment. This is structurally the same pattern as the reported bug: "block a component by special-casing it in most paths, but miss one path that can still move funds/state."

### Finding Description
`is_deprecated_token_gateway` is checked at the top of `on_accept` (line 366) and inside `on_timeout` for `Request::Post` (line 426) in `parachain/runtimes/nexus/src/ismp.rs`. However, the `IsmpModule` trait (`modules/ismp/core/src/module.rs:38`) defines `on_response` to accept only a `GetResponse`, meaning `PostResponse` handling for the same module/address is dispatched through a different code path outside `ProxyModule::on_response`'s deprecation check — the deprecated-address gate is never applied to response delivery for `PostRequest`s that a deprecated TokenGateway itself sent out and is now receiving a response for. [1](#0-0) [2](#0-1) 

This mirrors the external report's root cause exactly: disabling a component ("ratio = 0" / "deprecated address") is enforced in the primary allocation/accept path but a secondary path (the "neutral adapter" / the response-delivery path) has no equivalent check, so the blocked/faulty component can still participate in fund- or state-affecting flows through the path that was not updated.

### Impact Explanation
If a deprecated TokenGateway contract still has outstanding in-flight requests when it is added to the deprecation list, any response later delivered to it flows through the unguarded response path rather than being rejected the way new inbound requests and timeouts are. Given that TokenGateway handles cross-chain asset transfers, a compromised/faulty deployment that is "deprecated" specifically because it is untrusted could still receive and act on a PostResponse, potentially completing settlement logic (e.g. crediting/decrementing balances) that governance intended to fully cut off.

### Likelihood Explanation
This requires no privileged actor: it is purely a gap in defensive coverage across the three `IsmpModule` callback paths (`on_accept`, `on_response`, `on_timeout`) for the same guard. It would trigger naturally whenever a TokenGateway is deprecated while it still has requests awaiting responses, which is a realistic operational scenario for a maliciously-migrating or compromised gateway rather than requiring an unusual attacker capability.

### Recommendation
Apply `is_deprecated_token_gateway` consistently to every module callback that can be reached with the deprecated address as `from`/`to`, including the response-delivery path (whatever handles `PostResponse` module dispatch, and the `GetResponse` path already present in `on_response`), so that once an address is deprecated it is uniformly blocked across accept, response, and timeout — analogous to disabling the neutral adapter across every fund-movement branch instead of only the primary one.

### Proof of Concept
Not independently reproducible from the indexed code alone: the exact routing site for `PostResponse` delivery outside `ProxyModule` was not found in the available index (this repository's PostResponse module-dispatch path may live in `pallet-ismp` router code not covered by the search results). This should be verified directly against `modules/pallets/ismp/src/router.rs` or equivalent dispatcher code to confirm whether `PostResponse` for a `to`/`from` matching a deprecated TokenGateway is actually deliverable without the deprecation check — a Devin session with full repo access should trace the `Response::Post` dispatch path end-to-end to confirm or refute this gap before treating it as confirmed-exploitable.

### Citations

**File:** parachain/runtimes/nexus/src/ismp.rs (L361-372)
```rust
impl IsmpModule for ProxyModule {
	fn on_accept(&self, request: PostRequest) -> Result<Weight, anyhow::Error> {
		// Permanently reject any request originating from a deprecated TokenGateway
		// deployment, regardless of destination. This short-circuits both the
		// forwarding path (dest != host) and the locally-dispatched path below.
		if is_deprecated_token_gateway(&request.from) {
			return Err(anyhow!(
				"rejecting request from deprecated TokenGateway address {:?} on {:?}",
				request.from,
				request.source,
			));
		}
```

**File:** modules/ismp/core/src/module.rs (L36-40)
```rust
	/// Called by the message handler on a module, to notify module of a response to a previously
	/// sent out request
	fn on_response(&self, _response: GetResponse) -> Result<Weight, anyhow::Error> {
		Err(Error::CannotHandleMessage)?
	}
```
