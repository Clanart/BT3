### Title
Bandwidth gate not enforced on `GetResponse` delivery — free consumption of prepaid quota - ([File: parachain/runtimes/gargantua/src/ismp.rs, parachain/runtimes/nexus/src/ismp.rs])

### Summary
The `pallet-bandwidth` gate is documented as being consulted "for every message" and `ProxyModule::on_accept` correctly meters incoming `PostRequest`s against the sender's `(source, app)` allowance before dispatching to the destination pallet. `ProxyModule::on_response`, however, contains a comment claiming it "mirrors the request path in `on_accept`" but never actually calls `BandwidthGate::try_consume`. This is directly analogous to the reported bug class: a security/accounting enforcement mechanism (LavaMoat wrapping / here, the bandwidth gate) applied on the "normal" static path but silently absent on an adjacent, less-obvious dispatch path (dynamic `import()` / here, `GetResponse` delivery), letting that path bypass the control entirely.

### Finding Description
`pallet-bandwidth`'s doc comment states the gate is the hook "the runtime's ISMP router consults for every message; insufficient balance → rejected" [1](#0-0) . In `on_accept`, this is honored: bandwidth is metered against `pallet_bandwidth::BandwidthGate::try_consume` for every incoming Post request unless it's a purchase message [2](#0-1) .

In `on_response`, the equivalent gating is missing even though the surrounding comment implies it exists ("Bandwidth gate. Mirrors the request path in `on_accept`: the chain and module that produced the response pay for the bytes they deliver"): [3](#0-2) 
No call to `try_consume`/`BandwidthGate` exists anywhere in this function — it only checks `dest_chain` and dispatches to `pallet_ismp_demo`. The same absence exists in the Nexus runtime's `ProxyModule::on_response` [4](#0-3) .

Because `GET` requests dispatched by a local app are answered with a `GetResponse` carrying key/value data returned by the remote chain, and that response delivery is never charged against the `(source, app)` bandwidth allowance, an app can drive unlimited response-path traffic through the host without consuming (or ever needing) a bandwidth subscription. This breaks the invariant that "bandwidth balances must move exactly once and only to the rightful beneficiary and amount" — here bandwidth simply never moves for an entire class of ISMP traffic.

### Impact Explanation
This falls under the explicitly in-scope "bandwidth accounting" category. The prepaid bandwidth mechanism exists to meter and monetize per-app cross-chain message volume (`BandwidthCredited`/`BandwidthConsumed` events, subscription tiers) [5](#0-4) . An app that never purchases (or exhausts) a subscription can still consume arbitrary GetResponse bandwidth for free by issuing GET requests, because the resulting response callback path performs zero accounting. This is a direct loss of expected protocol revenue and an accounting bypass reachable by any unprivileged application/module without needing a malicious relayer, prover, or admin — the response itself is delivered through the normal, correctly-proven ISMP response handler; only the local charge is skipped.

### Likelihood Explanation
High confidence this is a genuine code/comment mismatch rather than intentional design: the comment in the exact function explicitly claims bandwidth gating occurs ("mirrors the request path"), yet no `try_consume` invocation is present, unlike the parallel, correctly-gated `on_accept`. The pattern is identical in both `gargantua` and `nexus` runtimes, and the gate is feature-gated to be skippable only via the explicit `no-bandwidth` build flag — not via any per-message logic — so there is no legitimate code path that intentionally exempts responses.

### Recommendation
Add the same `BandwidthGate::try_consume(&source, &app, bytes)` call to `on_response` that exists in `on_accept`, sizing `bytes` from the encoded `GetResponse` payload (mirroring `ismp::abi::encode_post_request` used for requests, using an analogous response encoder), before dispatching to the destination module, so that response-path traffic is metered against the same `(source, app)` allowance.

### Proof of Concept
1. Deploy an app pallet routed via `ProxyModule` that never registers/purchases a bandwidth subscription for itself (`Allowance` for `(source, app)` empty, not in `Allowlist`).
2. From that app, dispatch a `Request::Get` targeting a remote chain key whose value is large.
3. The remote chain, via the normal ISMP GET-response flow, returns a `GetResponse` with the requested (large) payload and a valid state proof.
4. On the local chain, `handlers/response.rs` verifies the proof and calls `router.module_for_id(...).on_response(response)`, invoking `ProxyModule::on_response`.
5. Observe: `on_response` never calls `try_consume`; no `BandwidthConsumed` event fires, and the app's `Allowance` (which was empty/zero) is untouched — response bytes were processed and delivered for free while an equivalent-sized `PostRequest` on the same app/source would have been rejected with `GateError::NoAllowance` in `on_accept`. [3](#0-2) [4](#0-3) [6](#0-5)

### Citations

**File:** modules/pallets/bandwidth/src/lib.rs (L31-33)
```rust
//!
//! [`BandwidthGate`] is the hook the runtime's ISMP router consults
//! for every message; insufficient balance → rejected.
```

**File:** modules/pallets/bandwidth/src/lib.rs (L140-155)
```rust
		TierSet { tier: TierIndex, config: Option<TierConfig> },
		/// A new subscription was appended on the `(app_chain, app)`
		/// list as a result of a paid purchase from `paid_from`.
		BandwidthCredited {
			app_chain: StateMachine,
			app: AppKey,
			/// Chain that paid; differs from `app_chain` on sponsorship.
			paid_from: StateMachine,
			tier: TierIndex,
			bytes: BandwidthBytes,
			expires_at: u64,
		},
		/// The gate deducted `bytes` from the head subscription(s) of
		/// `(source, app)`; `remaining` is the post-deduct sum across
		/// what's left.
		BandwidthConsumed { source: StateMachine, app: AppKey, bytes: u128, remaining: u128 },
```

**File:** modules/pallets/bandwidth/src/lib.rs (L509-565)
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

		Self::deposit_event(Event::BandwidthConsumed {
			source: *source,
			app: key,
			bytes: need,
			remaining: total - need,
		});
		Ok(())
	}
}
```

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

**File:** parachain/runtimes/gargantua/src/ismp.rs (L424-441)
```rust
	fn on_response(&self, response: GetResponse) -> Result<Weight, anyhow::Error> {
		// Bandwidth gate. Mirrors the request path in `on_accept`: the chain
		// and module that produced the response pay for the bytes they
		// deliver. Compiled out when the `no-bandwidth` flag is on.
		if response.dest_chain() != HostStateMachine::get() {
			return Ok(Weight::from_parts(0, 0));
		}

		let dest = &response.get.from;

		let pallet_id = ModuleId::from_bytes(dest).map_err(|err| Error::Custom(err.to_string()))?;

		match pallet_id {
			pallet_ismp_demo::PALLET_ID =>
				pallet_ismp_demo::IsmpModuleCallback::<Runtime>::default().on_response(response),
			_ => Err(anyhow!("Destination module not found")),
		}
	}
```

**File:** parachain/runtimes/nexus/src/ismp.rs (L412-418)
```rust
	fn on_response(&self, response: GetResponse) -> Result<Weight, anyhow::Error> {
		if response.dest_chain() != HostStateMachine::get() {
			return Ok(Weight::from_parts(0, 0));
		}

		Err(anyhow!("Destination module not found"))
	}
```
