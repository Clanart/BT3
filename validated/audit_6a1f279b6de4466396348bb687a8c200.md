Found the analog. The `try_consume` call in `ProxyModule::on_accept` gates bandwidth using `&request.from` as the `app` key — and `request.from` is attacker-chosen data carried on the wire by the *sender contract on the source chain*, not a value the bandwidth pallet independently authenticates against a registered identity. [1](#0-0) 

### Title
Bandwidth allowance drain across unrelated apps via spoofable `request.from` app key - (File: `parachain/runtimes/gargantua/src/ismp.rs`, `modules/pallets/bandwidth/src/lib.rs`)

### Summary
`pallet-bandwidth` meters outbound traffic per `(source chain, app)`, where `app` is keyed by whatever bytes appear in `request.from` on the inbound `PostRequest`. [2](#0-1)  The gate is consulted with `&request.from` taken directly off the wire before any dispatch to a specific pallet/module occurs: [1](#0-0) . `request.from` is set by the sending contract on the source EVM chain and is not cryptographically bound to any registered identity by the gate itself — unlike the purchase-credit path, which explicitly checks `request.from` against the registered `BandwidthManager` address before crediting [3](#0-2) , the *consumption* path (`try_consume`) performs no such binding — it simply treats whatever `app` bytes arrive as the bucket to drain.

This mirrors the Predy `reallocate()` bug: two logically distinct entities (there, Uniswap pairs; here, distinct on-chain apps) share one physical resource (there, the tick-range liquidity; here, the `(chain, app)` byte allowance) keyed by a value that is not exclusively scoped to the entity that funded it. Any party able to construct a `PostRequest` with `from = <victim's app identifier bytes>` from the same source chain drains the victim's paid-for bandwidth balance, exactly as a reallocate() on pair 2 drained pair 1's liquidity because the range key was shared rather than pair-scoped.

### Finding Description
1. A legitimate app on some `source` chain purchases bandwidth. `BandwidthManager.purchase()` dispatches a credit message whose body carries `app` (the identifier the app wants credited); `pallet-bandwidth::on_accept` verifies `request.from` matches the registered `BandwidthManager` for that source before crediting `Allowance[(app_chain, app)]`. [3](#0-2) 
2. Later, when *any* app on the same source chain dispatches an ordinary (non-purchase) request, the ISMP router calls `try_consume(&request.source, &request.from, bytes)` — using the raw `request.from` field of that specific request as the `app` key to drain. [1](#0-0) 
3. `try_consume` performs no authentication of `app` against anything — it simply mutates `Allowance::<T>::mutate(source, &key, ...)` for whatever `key` was passed in and drains the FIFO subscription list. [4](#0-3) 
4. Because `request.from` is fully attacker-controlled application-layer data (any contract on the source chain can set the `from` field of the `PostRequest` it dispatches to be an arbitrary byte string), an attacker's own contract can dispatch a request with `from` set to a victim app's identifier bytes. The gate then debits the victim's prepaid `Allowance` bucket instead of the attacker's own (nonexistent) bucket, letting the attacker's message ride for free while burning the victim's paid balance — and once the victim's balance is exhausted, its legitimate messages start being rejected (`GateError::NoAllowance` / `Insufficient`).

The purchase (crediting) side is properly authenticated against `BandwidthManager<T>` per source chain, but the consumption (draining) side has no equivalent check that `request.from` actually belongs to the entity dispatching that specific request.

### Impact Explanation
This is a direct violation of the "bandwidth balances must move exactly once and only to the rightful beneficiary" invariant from the bounty scope. An unprivileged attacker on any registered source chain can:
- Drain a paid competitor/victim app's prepaid bandwidth allowance for free, causing denial of the victim's legitimate cross-chain messages once quota is exhausted (`GateError::NoAllowance`).
- Effectively steal the economic value of the victim's purchase (bytes paid for at real ETH/USDC cost) without paying anything themselves.

This matches "stealing or loss of funds" / "logic attacks" acceptance criteria: real economic value (bandwidth purchased with tokens) is redirected to benefit an attacker at a victim's expense, purely through message crafting from an unprivileged EVM account — no relayer, prover, or governance compromise required.

### Likelihood Explanation
High. `request.from` is a routine field any contract dispatching through the local ISMP host can set to arbitrary bytes; no special privilege is needed to pick any byte string as `from`. As long as the attacker knows (or can observe on-chain, since `BandwidthCredited`/`AppKey` values are public events) the `app` byte-string identifier of a funded victim, the attack is a single ordinary dispatch call from the source chain.

### Recommendation
Bind the `app` key used for gate consumption the same way the credit path binds it — either:
- Require the router to authenticate `request.from` against a registry of module addresses actually deployed/known for that source chain (similar to `BandwidthManager<T>` for purchases), so an app can only drain its own bucket; or
- Derive the `app` key from something not spoofable per dispatching entity, e.g., include the concrete deployed contract address that dispatched the message (verified by the source-chain's ISMP host, not attacker-suppliable arbitrary bytes) rather than trusting the raw `from` field at face value.

### Proof of Concept
1. App V (victim) on `StateMachine::Evm(X)` purchases bandwidth via `BandwidthManager.purchase()`, crediting `Allowance[(app_chain, AppKey(V_bytes))]` with N bytes. [3](#0-2) 
2. Attacker deploys contract A on the same `Evm(X)` chain and calls the local `IDispatcher.dispatch(...)` to send a `PostRequest` whose `from` field is set to `V_bytes` (the victim's identifier) and `to` targets any real destination pallet.
3. On delivery, `ProxyModule::on_accept` calls `try_consume(&Evm(X), &V_bytes, bytes)`, which drains bytes from `Allowance[(Evm(X), AppKey(V_bytes))]` — the victim's paid allowance — even though the attacker's contract, not V, dispatched the message. [1](#0-0) 
4. Repeating this exhausts V's subscription; V's own subsequent legitimate dispatches are rejected with `GateError::NoAllowance`/`Insufficient`, while the attacker never paid for any bandwidth of its own.

### Citations

**File:** parachain/runtimes/gargantua/src/ismp.rs (L381-396)
```rust
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

**File:** modules/pallets/bandwidth/src/lib.rs (L105-118)
```rust
	/// Keyed by `app_chain` from the purchase message — *not*
	/// `request.source` — so a payer chain can sponsor an app that
	/// lives elsewhere. The inner `BoundedVec` holds subscriptions in
	/// chronological insertion order; the gate drains the front.
	#[pallet::storage]
	pub type Allowance<T: Config> = StorageDoubleMap<
		_,
		Twox64Concat,
		StateMachine,
		Blake2_128Concat,
		AppKey,
		SubscriptionList,
		ValueQuery,
	>;
```

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

**File:** modules/pallets/bandwidth/src/lib.rs (L509-555)
```rust
impl<T: Config> BandwidthGate for Pallet<T> {
	fn try_consume(
		source: &ismp::host::StateMachine,
		app: &[u8],
		bytes: u32,
	) -> Result<(), GateError> {
		let key = AppKey::truncate_from(app.to_vec());
		if Allowlist::<T>::contains_key(source, &key) {
			return Ok(());
		}

		let need: u128 = bytes.into();
		let now = <T as pallet_ismp::Config>::TimestampProvider::now().as_secs();

		let total = pallet::Allowance::<T>::mutate(source, &key, |list| {
			// Sweep expired in-place. Order-preserving.
			list.retain(|s| s.expires_at > now);

			if list.is_empty() {
				return Err(GateError::NoAllowance);
			}

			let total: u128 = list.iter().map(|s| s.remaining_bytes).sum();
			if total < need {
				return Err(GateError::Insufficient { remaining: total, required: need });
			}

			// Drain from the front in insertion order. Once a sub is
			// fully consumed, pop it and continue with the next.
			// `get_mut` defends against a malformed list that satisfies
			// the `total >= need` precheck but is structurally empty;
			// we'd otherwise panic via `list[0]`.
			let mut left = need;
			while left > 0 {
				let Some(head) = list.get_mut(0) else {
					return Err(GateError::NoAllowance);
				};
				let take = head.remaining_bytes.min(left);
				head.remaining_bytes = head.remaining_bytes.saturating_sub(take);
				left = left.saturating_sub(take);
				if head.remaining_bytes == 0 {
					list.remove(0);
				}
			}

			Ok(total)
		})?;
```
