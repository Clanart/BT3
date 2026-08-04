Based on my research, I found a concrete analog: `is_purchase_message` is used as a gate-bypass predicate, and its "fail-open on missing registration" behavior can be forced by an attacker who controls the `from` field on an unrelated, non-purchase request.

### Title
Bandwidth gate bypass via forged `from` field masquerading as a purchase message - ([File: modules/pallets/bandwidth/src/lib.rs])

### Summary
`ProxyModule::on_accept` in both the Gargantua and Nexus runtimes skips the `BandwidthGate::try_consume` check entirely whenever `pallet_bandwidth::Pallet::<Runtime>::is_purchase_message(&request)` returns `true`. That predicate only checks that `request.from == registered_manager_address` for `request.source`; it does **not** verify that the request body actually decodes as a valid purchase message, nor does it require the request to actually reach `pallet-bandwidth`'s `on_accept`.

### Finding Description
`is_purchase_message` is defined as: [1](#0-0) 

It is consumed identically in both runtime routers before the gate is consulted: [2](#0-1) [3](#0-2) 

The check is purely `request.source == registered chain && request.from == manager address`. It never validates `request.to` (the destination module), nor does it verify the request body is a decodable `PurchaseMessage` destined for `pallet-bandwidth`. Since `request.from` is attacker-controlled data on the source chain (any account/contract can set the `from` field of an ISMP dispatch on an EVM source chain that is *not* gated at the contract level — the manager contract address is public, and `from` is simply the dispatcher's `msg.sender` recorded by the source-chain `IDispatcher`), any app on a `BandwidthManager`-registered source chain that spoofs its dispatch `from` as the manager's address bypasses the bandwidth gate for **any arbitrary `to`/`body`**, not just genuine purchase messages.

Compare this to `on_accept`'s actual purchase validation, which is comprehensive (manager match + body decode + tier lookup): [4](#0-3) 

But the router's pre-check (`is_purchase_message`) that decides whether to *skip the gate* does not require the request to land on `pallet-bandwidth` at all — it only checks `from`. A message with an arbitrary `to` (e.g., a token-gateway module) whose `from` equals the manager address will skip `try_consume` and route straight to the destination module's `on_accept`, i.e., unpaid unlimited-size message delivery.

### Impact Explanation
This lets any app dispatch unlimited-size/unlimited-volume ISMP messages through Hyperbridge without ever purchasing bandwidth, directly undermining the metering/paid-usage invariant that `pallet-bandwidth` is designed to enforce ("Bridged assets... and bandwidth balances must move exactly once and only to the rightful beneficiary and amount" pivot). This is a logic attack producing unauthorized/unpaid message throughput at the expense of the protocol (revenue loss / resource abuse), reachable by any unprivileged sender on a registered source chain who can control the `from` field of their own ISMP dispatch to equal the manager address value (whether via a proxy contract, delegate-call pattern, or any dispatcher path that lets a caller set an arbitrary `from`).

### Likelihood Explanation
Likelihood depends on whether the source-chain `IDispatcher` implementation constrains `from` to `msg.sender` strictly for all dispatch paths. If any source-chain dispatch entrypoint allows a caller to set `from` to an arbitrary address (e.g., a relayed/meta-tx dispatch, or a shared dispatcher used by multiple apps), this is trivially exploitable by an unprivileged attacker with no special access. This needs confirmation against the specific `IDispatcher`/`EvmHost` contract implementation to be certain `from` is always strictly bound to `msg.sender`; I could not fully verify this within the available index (the EVM `IDispatcher`/host contract source wasn't retrieved in this pass), so likelihood is asserted with that caveat.

### Recommendation
`is_purchase_message` (and its callers) should require that the request additionally targets `pallet-bandwidth`'s module id (`request.to == PALLET_BANDWIDTH` encoding) and that the body decodes as a valid `PurchaseMessage`, not just that `from` matches the registered manager address. Alternatively, gate-skip should only be granted after `on_accept` on `pallet-bandwidth` has actually succeeded, rather than pre-emptively based on sender identity alone.

### Proof of Concept
1. Governance registers `BandwidthManager` at address `M` for source chain `S` via `set_manager(S, M)`.
2. An attacker on `S` dispatches an ISMP `PostRequest` with `from = M`, `to = <some other module, e.g. token gateway>`, and an arbitrary large `body`, via any dispatch mechanism on `S` that does not hard-bind `from` to `msg.sender`.
3. On delivery, `ProxyModule::on_accept` calls `is_purchase_message(&request)`, which returns `true` because `request.source == S` and `request.from == M.0.to_vec()`.
4. The bandwidth gate (`try_consume`) is skipped entirely; the request routes directly to the target module's `on_accept`, consuming Hyperbridge relay/dispatch resources without ever purchasing bandwidth. [1](#0-0) [2](#0-1)

### Citations

**File:** modules/pallets/bandwidth/src/lib.rs (L439-445)
```rust
		/// The router uses this to skip the gate on purchases —
		/// otherwise a depleted app couldn't recharge.
		pub fn is_purchase_message(request: &PostRequest) -> bool {
			BandwidthManager::<T>::get(&request.source)
				.map(|m| request.from == m.0.to_vec())
				.unwrap_or(false)
		}
```

**File:** modules/pallets/bandwidth/src/lib.rs (L454-465)
```rust
	impl<T: Config> IsmpModule for Pallet<T> {
		fn on_accept(&self, request: PostRequest) -> Result<Weight, anyhow::Error> {
			let manager = BandwidthManager::<T>::get(&request.source).ok_or_else(|| {
				anyhow::anyhow!(format!("no bandwidth manager registered for {:?}", request.source))
			})?;

			if request.from != manager.0.to_vec() {
				return Err(anyhow::anyhow!(format!(
					"purchase from unauthorised sender on {:?}: expected {:x?}, got {:x?}",
					request.source, manager.0, request.from
				)));
			}
```

**File:** parachain/runtimes/gargantua/src/ismp.rs (L375-396)
```rust
impl IsmpModule for ProxyModule {
	fn on_accept(&self, request: PostRequest) -> Result<Weight, anyhow::Error> {
		// Bandwidth gate. Always-enforce unless the `no-bandwidth` flag
		// is set; skipped for purchase messages so the recharge flow
		// itself doesn't need bandwidth. With the flag on the gate is a
		// no-op and this block is compiled out entirely.
		#[cfg(not(feature = "no-bandwidth"))]
		if !pallet_bandwidth::Pallet::<Runtime>::is_purchase_message(&request) {
			let bytes = ismp::abi::encode_post_request(&request).len() as u32;
			<pallet_bandwidth::Pallet<Runtime> as pallet_bandwidth::BandwidthGate>::try_consume(
				&request.source,
				&request.from,
				bytes,
			)
			.map_err(|err| {
				anyhow!(
					"bandwidth gate: {err} (source={:?}, from={:x?})",
					request.source,
					request.from
				)
			})?;
		}
```

**File:** parachain/runtimes/nexus/src/ismp.rs (L374-390)
```rust
		// Bandwidth gate. Always-enforce; skipped for purchase messages so the
		// recharge flow itself doesn't need bandwidth.
		if !pallet_bandwidth::Pallet::<Runtime>::is_purchase_message(&request) {
			let bytes = ismp::abi::encode_post_request(&request).len() as u32;
			<pallet_bandwidth::Pallet<Runtime> as pallet_bandwidth::BandwidthGate>::try_consume(
				&request.source,
				&request.from,
				bytes,
			)
			.map_err(|err| {
				anyhow!(
					"bandwidth gate: {err} (source={:?}, from={:x?})",
					request.source,
					request.from
				)
			})?;
		}
```
