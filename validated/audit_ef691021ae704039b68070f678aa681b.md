## Finding: Reward-mint inflation via PostRequest body splitting - (File: modules/pallets/messaging-incentives/src/lib.rs)

### Summary
`Pallet::message_bytes` computes the byte count used to mint `ReputationAsset` to the relayer by summing, **per individual `PostRequest`**, `max(body.len(), 32)`. Because the 32-byte floor is applied independently to every request rather than to the aggregate payload, an attacker who controls how many `PostRequest`s a given quantity of real payload is split into can inflate the total minted bytes/reward linearly with the number of requests, without increasing the actual bandwidth delivered.

### Finding Description
`message_bytes` is defined as: [1](#0-0) 

and it is consumed in `FeeHandler::on_executed`, where the per-message byte count is multiplied by the governance-set `MintPerByte` rate and minted directly to the relayer that signed the message: [2](#0-1) 

The in-code comment claims this per-request floor makes "packing requests into one envelope vs. splitting them across many" produce identical mints, and that applying the floor once per envelope (instead of per request) would be the exploitable variant. That reasoning only holds when the *number of underlying requests is fixed* and one merely regroups them into different `Message::Request` envelopes — the per-request floor does make envelope-repacking neutral. It does **not** address the case where the actual number of `PostRequest`s is itself attacker-controlled for a fixed amount of real payload: each additional near-empty `PostRequest` picks up its own independent 32-byte floor, so `sum(max(body_i, 32))` grows with the request count `N` even though total real data stays constant (e.g. near 0).

Concretely, for `N` requests each with body length `< 32` (including empty bodies), `message_bytes` returns `32 * N`, while the equivalent data delivered as a single request of the same total size floors at `max(total, 32)`. For `total < 32*N`, the split form yields a strictly larger byte count and therefore a proportionally larger mint, breaking the documented invariant ("Mints reputation tokens... scaled by message size") that reward should track real bandwidth consumed.

### Impact Explanation
Since `PostRequest`s are created by source-chain dispatch calls (`IsmpDispatcher`) that are reachable from unprivileged users/modules, an unprivileged actor can shape cross-chain traffic into many small/empty-body requests instead of consolidating them. Any relayer that delivers such a batch (including a self-operated or unprivileged actor who runs a permissionless relayer) receives a `ReputationAsset` mint disproportionate to the actual bytes bridged. This corrupts the reward-accounting invariant that minted `ReputationAsset` must be proportional to real bandwidth/data delivered, i.e., the computed `amount` in `Event::ReputationMinted` no longer reflects genuine bandwidth consumption and can be inflated at will by request-count manipulation.

### Likelihood Explanation
Exploitability depends on the relative cost of dispatching additional small ISMP requests on the source chain versus the value of the `ReputationAsset` minted per 32-byte floor at the governance-configured `MintPerByte` rate. Whenever per-request source-chain dispatch fees are lower than `32 * MintPerByte`, splitting is profitable, making this readily triggerable without any privileged or malicious-infrastructure assumptions — only ordinary unprivileged calls that dispatch cross-chain requests are required.

### Recommendation
Apply the minimum-byte floor once per `Message` envelope (i.e., `max(sum(body.len()), 32)`) rather than per individual `PostRequest`, so that the total mint for a fixed real payload does not grow with the number of requests it is split into. If per-request accounting is required for other reasons (e.g. weight/fee purposes), track and floor the aggregate body length instead of summing independent floors.

### Proof of Concept
1. Construct `RequestMessage` A: a single `PostRequest` with `body.len() == 32*N`.
2. Construct `RequestMessage` B: `N` separate `PostRequest`s, each with `body.len() == 0` (or any length `< 32`), same signer/module routing, whose combined real payload is empty/negligible.
3. Call `Pallet::message_bytes` on both:
   - A yields `max(32*N, 32) = 32*N`.
   - B yields `N * max(0, 32) = 32*N` — but for smaller real totals (e.g., true payload of size `< 32*N`), B strictly exceeds the byte count that would result from consolidating the same data into fewer requests, and for a purely empty-body split (`N` requests of 0 bytes) the delivered bandwidth is effectively 0 while `message_bytes` still returns `32*N`, which then gets minted via `on_executed` at `rate.saturating_mul(bytes_balance)` — an unbounded, request-count-scaled reward for near-zero real data. [3](#0-2)

### Citations

**File:** modules/pallets/messaging-incentives/src/lib.rs (L126-135)
```rust
	fn message_bytes(message: &Message) -> u32 {
		match message {
			Message::Request(req) => req
				.requests
				.iter()
				.map(|p| core::cmp::max(p.body.len() as u32, 32))
				.sum::<u32>(),
			_ => 0,
		}
	}
```

**File:** modules/pallets/messaging-incentives/src/lib.rs (L160-182)
```rust
	fn on_executed(
		messages: Vec<MessageWithWeight>,
		_events: Vec<IsmpEvent>,
	) -> DispatchResultWithPostInfo {
		let rate = MintPerByte::<T>::get();
		if !rate.is_zero() {
			for mw in &messages {
				let bytes = Self::message_bytes(&mw.message);
				let bytes_balance: BalanceOf<T> = (bytes as u128).saturated_into();
				let amount = rate.saturating_mul(bytes_balance);
				if amount.is_zero() {
					continue;
				}
				if let Some(relayer) = Self::relayer_for(&mw.message) {
					match T::ReputationAsset::mint_into(&relayer, amount) {
						Ok(_) =>
							Self::deposit_event(Event::ReputationMinted { relayer, bytes, amount }),
						Err(err) => log::warn!(
							target: "messaging-incentives",
							"reputation mint failed for {bytes}b: {err:?}",
						),
					}
				}
```
