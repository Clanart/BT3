Based on the investigation, I found a concrete local analog: a bandwidth-accounting gate that is documented/commented as protecting a message path but is never actually invoked on that path — mirroring the original bug class where a restricted function exists but nothing in the intended caller ever exercises it, silently defeating the protection.

### Title
Bandwidth gate is never enforced on the GET-response delivery path, allowing unmetered message delivery - ([File: parachain/runtimes/gargantua/src/ismp.rs])

### Summary
`pallet-bandwidth` is designed to meter every inbound message from a managed source chain against a prepaid `(chain, app)` byte allowance, with the gate consulted "on every non-purchase request" per the documented architecture [1](#0-0) . In the `ProxyModule` runtime router, `on_accept` (POST request path) correctly calls `BandwidthGate::try_consume` before dispatch [2](#0-1) . However, `on_response` (GET response path) contains a comment explicitly stating the gate should "mirror the request path in `on_accept`: the chain and module that produced the response pay for the bytes they deliver," yet the function body never calls `try_consume` or any gate function at all — it only checks `dest_chain` and routes to the destination module [3](#0-2) .

### Finding Description
The bandwidth subscription model is a closed-loop metering system: apps pre-pay for byte allowances, and the `BandwidthGate::try_consume(source, app, bytes)` hook is supposed to be the sole enforcement point that drains the FIFO subscription list and rejects messages when the allowance is exhausted [4](#0-3) . The design intentionally exempts only purchase messages from the gate via `is_purchase_message`, so that a depleted app can still recharge [5](#0-4) .

In the runtime wiring, this exemption logic and the `try_consume` call are present for POST requests in `on_accept`: [6](#0-5) 

But the analogous `on_response` handler for inbound `GetResponse` messages has a comment asserting equivalent enforcement ("Mirrors the request path in `on_accept`") without any corresponding call to `try_consume`: [7](#0-6) 

This is structurally the same class of defect as the external report: a protection mechanism (there, an authorization restricting a swap message to a caller that never invokes it; here, a bandwidth gate that the code claims enforces metering on a path but is never actually wired into that path) is non-functional because the intended call site was never implemented, silently nullifying the control.

### Impact Explanation
Any GET request originating from this chain that receives a `GetResponse` from a connected chain is delivered and processed by the destination module (e.g., `pallet_ismp_demo`) without any bandwidth deduction, regardless of the app's subscription state. This breaks the "bandwidth balances must move exactly once and only to the rightful beneficiary and amount" invariant: an app with a fully exhausted or unregistered subscription can still receive/consume GetResponse-triggered execution at zero cost, while the pallet's economic model assumes GET response bytes are accounted for identically to POST bytes per the code comment. Effectively the accounting ledger for one entire message class (GetResponse) is bypassed at zero attacker cost and with no privileged position required — any application/relayer that is legitimately delivering responses benefits from this gap, i.e., normal usage silently avoids paying for bandwidth it is supposed to be metered for.

### Likelihood Explanation
This requires no malicious relayer, prover, or admin — it triggers on the default, unprivileged execution path any time a GET response is delivered to this parachain. The gap is deterministic (not race-dependent) and reproducible on every `on_response` call, since the code path simply never reaches a `try_consume` invocation for GetResponse traffic.

### Recommendation
Add the equivalent bandwidth-gate check inside `ProxyModule::on_response`, mirroring `on_accept`: compute the encoded response size, look up whether the response is delivered under a purchase-exempt path (or determine if GET responses should be exempt by design), and call `BandwidthGate::try_consume(&response.source_chain(), &response.get.from, bytes)` before routing to the destination module — rejecting the response delivery on `GateError` exactly as is done for POST requests.

### Proof of Concept
1. Register a `BandwidthManager` for a source chain and app but do not purchase any tier (or exhaust an existing subscription) for that `(chain, app)`.
2. Have the app dispatch a GET request from that chain; wait for the destination chain's state to be queried and a `GetResponse` delivered back through `pallet-ismp`.
3. Observe that `ProxyModule::on_response` in `parachain/runtimes/gargantua/src/ismp.rs` routes the response straight to `pallet_ismp_demo::IsmpModuleCallback::on_response` (lines 424-441) without ever calling `pallet_bandwidth::Pallet::<Runtime>::try_consume`.
4. Confirm via `Allowance::<T>::get(app_chain, app)` that no subscription bytes were deducted for the delivered `GetResponse`, even though the app has zero remaining allowance — the message is processed regardless, in contrast to the identical POST-request scenario which would be rejected with `GateError::NoAllowance` by the `on_accept` path.

Note: I was not able to verify within the available index whether GET-response metering was an explicit product requirement beyond the code's own comment (the `docs/content/developers/evm/bandwidth/*` pages describe gating only for "every non-purchase POST request" and do not separately document GET-response metering), so there is some residual uncertainty about whether this is a genuine regression versus a comment that is simply stale/aspirational. A Devin session with full repo access and the ability to check `nexus`'s `ismp.rs` and the `pallet-bandwidth` test suite in depth would help confirm the intended design.

### Citations

**File:** docs/content/developers/evm/bandwidth/overview.mdx (L83-93)
```text
## The Gate

Every non-purchase POST request from a registered source chain runs through `BandwidthGate::try_consume(source, app, bytes)`:

1. If the app is on the **allowlist** for that source, return `Ok` without touching the ledger.
2. Sweep expired subscriptions in place.
3. If no live subscriptions remain → `GateError::NoAllowance`.
4. Sum `remaining_bytes` across live entries. If the sum is short → `GateError::Insufficient { remaining, required }`. **No mutation happens in this case** — the caller can retry after a top-up.
5. Otherwise drain from the head until the requested bytes are satisfied. Pop entries that hit zero. Emit `BandwidthConsumed` with the post-deduct remaining.

The "no mutation on insufficient" property is load-bearing: it means a top-up race is safe — if a message arrives between when an app notices it's short and when the top-up lands, the message stays rejectable rather than half-consuming the subscription.
```

**File:** parachain/runtimes/gargantua/src/ismp.rs (L376-396)
```rust
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

**File:** modules/pallets/bandwidth/src/lib.rs (L509-564)
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
```
