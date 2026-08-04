## Analysis

The external bug is: a fee/resource is deducted from a user based on a *scheduled* outcome, but the outcome (reward issuance) never actually happens, and nothing refunds the deducted resource. The user is charged for something they never received, and the charge can recur because the failure isn't the user's fault.

The local analog is in Hyperbridge's **bandwidth metering path**, where the same "charge-before-verifying-success, no-refund-on-failure" pattern exists — and, unlike the paymaster/relayer-fee paths (which explicitly refund on timeout/callback-failure), the bandwidth gate has no such compensating mechanism, and the underlying ISMP replay design turns this into a repeatable drain.

### Root cause [1](#0-0) 

In `ProxyModule::on_accept` (both `gargantua` and `nexus` runtimes), the bandwidth gate deducts bytes from the **sending app's** prepaid allowance *before* the request is actually routed and executed by the destination module: [2](#0-1) 

If the subsequent module lookup/dispatch fails — e.g. `to` points to an unregistered module id (`"Destination module not found"`), a decode error, or any other `on_accept` error from the destination pallet — the bytes already consumed by `BandwidthGate::try_consume` are **not refunded**. Compare this with the relayer-fee path, which is explicitly refund-safe: `EvmHost.dispatchTimeOut`/`pallet-ismp::on_request_timeout` refund relayer fees on timeout or on failed-then-retried callbacks: [3](#0-2) 

No equivalent exists for `pallet-bandwidth`'s `try_consume` — once bytes are drained from the FIFO subscription list, they're gone: [4](#0-3) 

### Why this is repeatable (not a one-off loss)

Per Hyperbridge's own documented ISMP invariant, when a module's `on_accept` returns `Err`, the request **receipt is deleted specifically so the request can be replayed**: [5](#0-4) [6](#0-5) 

`handle()` is permissionless — any relayer/caller holding the original valid membership proof can resubmit the exact same commitment. Because the destination-module failure mode described above (unknown/misconfigured `to` module id, malformed body, etc.) is deterministic and permanent, the same request can be resubmitted indefinitely (bounded only by `timeout_timestamp`, which the app itself sets and can be `0` for "never expires"). **Each resubmission re-runs the bandwidth gate and drains bytes again**, since the gate has no per-commitment idempotency check — it only tracks aggregate byte balances, not which commitments have already been charged.

This lets an attacker (any permissionless party, no privileged role needed) drain an app's entire prepaid bandwidth allowance by repeatedly resubmitting a request that is known to permanently fail on the destination side, without ever delivering value to the app — structurally identical to the M-04 pattern of "house edge taken despite no reward issued," except here it is user-repeatable rather than one-shot, and it targets a real prepaid on-chain resource (bandwidth, purchased with `feeToken`/USDC value via `BandwidthManager.purchase()`).

### Title
Bandwidth allowance is permanently drained on every gate-consuming request whose destination `on_accept` fails, and the failing request can be replayed indefinitely to repeat the drain - (File: `parachain/runtimes/gargantua/src/ismp.rs`, `parachain/runtimes/nexus/src/ismp.rs`, `modules/pallets/bandwidth/src/lib.rs`)

### Summary
`ProxyModule::on_accept` calls `BandwidthGate::try_consume` (deducting bytes from the sending app's prepaid `Allowance`) *before* dispatching the request to its destination module. If the destination dispatch subsequently fails (unknown module id, decode error, or any other module-level `Err`), the consumed bytes are never refunded. Because ISMP intentionally deletes the request receipt on `on_accept` failure to allow legitimate retries, the exact same failing request can be resubmitted by anyone holding the proof, and each resubmission re-drains bandwidth from the paying app with no compensating credit.

### Finding Description
`BandwidthGate::try_consume` mutates `Allowance` storage unconditionally on success, deducting real, previously-purchased bandwidth from `(source, request.from)`: [7](#0-6) 

This call happens in the runtime's `ProxyModule::on_accept` *before* the request is matched against a destination pallet and dispatched: [8](#0-7) 

If the match falls through to `_ => Err(anyhow!("Destination module not found"))` (or any other destination-module error), the ISMP handler treats this as a normal `on_accept` failure and deletes the stored receipt specifically to permit retrying: [9](#0-8) 

Unlike relayer fees — which are refunded to `payer` on timeout via `on_request_timeout`/`dispatchTimeOut` — there is no bandwidth-refund path anywhere in `pallet-bandwidth`. The only ways bytes re-enter an allowance are a fresh `purchase()` or governance's `force_credit`, neither of which triggers automatically on failure.

### Impact Explanation
An app's prepaid bandwidth (real monetary value paid via `BandwidthManager.purchase()`) is consumed for messages that never actually execute on the destination, and this loss compounds because the failing request is replayable by design. A malicious or careless actor can force the same deterministically-failing request through `handle()` repeatedly until the target app's entire allowance across all its live subscriptions is exhausted, permanently denying it bridge access (`GateError::NoAllowance`) and destroying the funds it spent purchasing bandwidth — a direct loss-of-funds impact with no admin/relayer/prover compromise required.

### Likelihood Explanation
Likelihood is elevated because: (1) `handle()` is fully permissionless — any address with the message's already-public proof can resubmit it; (2) the failure trigger (module-not-found/decode-error on `to`) is easy to construct or occurs naturally from misconfiguration; (3) requests may be dispatched with `timeout: 0` ("never expires"), removing any time bound on how long the replay window stays open.

### Recommendation
Charge the bandwidth gate only after the destination module's `on_accept` returns `Ok`, or make the gate consumption idempotent per request commitment (track charged commitments so a resubmission of the same failed request cannot deduct twice), and/or add a refund path (mirroring the relayer-fee timeout refund) that credits back bytes when the destination dispatch fails, especially for permanent (non-retriable) failures.

### Proof of Concept
1. App `A` on chain `X` purchases bandwidth via `BandwidthManager.purchase()`, crediting `Allowance(X, A)`.
2. App `A` (or anyone acting on its behalf) dispatches a POST request from `X` with `to` set to a module id that does not exist in the destination runtime's router (or any payload that will deterministically fail `on_accept`), `timeout: 0`.
3. A relayer submits `RequestMessage` to `handle()`. `ProxyModule::on_accept` runs: `try_consume(X, A, bytes)` succeeds and deducts `bytes` from `Allowance(X, A)`; then the module match fails with `"Destination module not found"`.
4. Per `modules/ismp/core/src/handlers/request.rs`, the request receipt is deleted because `on_accept` errored.
5. Any party resubmits the identical `RequestMessage`/proof to `handle()` again. Step 3 repeats: bandwidth is deducted again for the same never-succeeding message.
6. Repeat until `Allowance(X, A)` is exhausted (`GateError::NoAllowance`), at which point `A` is locked out of the bridge despite having paid for bandwidth it never got to use.

### Citations

**File:** parachain/runtimes/gargantua/src/ismp.rs (L375-422)
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

		if request.dest != HostStateMachine::get() {
			Ismp::dispatch_request(
				Request::Post(request),
				FeeMetadata::<Runtime> { payer: [0u8; 32].into(), fee: Default::default() },
			)?;
			return Ok(Weight::from_parts(0, 0));
		}

		let pallet_id =
			ModuleId::from_bytes(&request.to).map_err(|err| Error::Custom(err.to_string()))?;

		match pallet_id {
			pallet_ismp_demo::PALLET_ID =>
				pallet_ismp_demo::IsmpModuleCallback::<Runtime>::default().on_accept(request),

			#[cfg(not(feature = "no-bandwidth"))]
			id if id == ModuleId::Pallet(pallet_bandwidth::pallet::PALLET_BANDWIDTH) =>
				pallet_bandwidth::Pallet::<Runtime>::default().on_accept(request),

			pallet_hyper_fungible_token::PALLET_ID =>
				pallet_hyper_fungible_token::Pallet::<Runtime>::default().on_accept(request),

			_ => Err(anyhow!("Destination module not found")),
		}
	}
```

**File:** parachain/runtimes/nexus/src/ismp.rs (L374-410)
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

		if request.dest != HostStateMachine::get() {
			Ismp::dispatch_request(
				Request::Post(request),
				FeeMetadata::<Runtime> { payer: [0u8; 32].into(), fee: Default::default() },
			)?;
			return Ok(Weight::from_parts(0, 0));
		}

		let pallet_id =
			ModuleId::from_bytes(&request.to).map_err(|err| Error::Custom(err.to_string()))?;

		match pallet_id {
			id if id == ModuleId::Pallet(pallet_bandwidth::pallet::PALLET_BANDWIDTH) =>
				pallet_bandwidth::Pallet::<Runtime>::default().on_accept(request),
			pallet_hyper_fungible_token::PALLET_ID =>
				pallet_hyper_fungible_token::Pallet::<Runtime>::default().on_accept(request),
			_ => Err(anyhow!("Destination module not found")),
		}
	}
```

**File:** modules/pallets/ismp/src/host.rs (L322-335)
```rust
	fn on_request_timeout(&self, _req: &Request, meta: Vec<u8>) -> Result<(), Error> {
		let leaf_meta = RequestMetadata::<T>::decode(&mut &*meta)
			.map_err(|_| Error::Custom("Failed to decode leaf metadata".to_string()))?;
		if leaf_meta.fee.fee > Zero::zero() {
			T::Currency::transfer(
				&RELAYER_FEE_ACCOUNT.into_account_truncating(),
				&leaf_meta.fee.payer,
				leaf_meta.fee.fee,
				Preservation::Expendable,
			)
			.map_err(|err| Error::Custom(format!("Failed to refund relayer fee: {err:?}")))?;
		}
		Ok(())
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

**File:** modules/ismp/core/src/handlers/request.rs (L99-126)
```rust
		.map(|request| {
			let wrapped_req = Request::Post(request.clone());
			let mut lambda = || {
				let cb = router.module_for_id(request.to.clone())?;
				// Re-check the receipt right before dispatch. The up-front pass above
				// runs before any callback executes; a prior request's on_accept in
				// this same batch could have stored a receipt for this request
				// (directly or by re-entering the handler), and we must not invoke
				// on_accept a second time.
				if host.request_receipt(&wrapped_req).is_some() {
					Err(Error::DuplicateRequest { meta: wrapped_req.clone().into() })?
				}
				// Store request receipt to prevent reentrancy attack
				let signer = host.store_request_receipt(&wrapped_req, &msg.signer)?;
				let res = cb.on_accept(request.clone()).map(|weight| {
					total_weights.saturating_accrue(weight);

					let commitment = hash_request::<H>(&wrapped_req);
					Event::PostRequestHandled(RequestResponseHandled {
						commitment,
						relayer: signer,
					})
				});
				// Delete receipt if module callback failed so it can be timed out
				if res.is_err() {
					host.delete_request_receipt(&wrapped_req)?;
				}
				Ok(res)
```

**File:** docs/content/protocol/ismp/requests.mdx (L102-113)
```text
The request `handle` is used to notify onchain `IsmpModule`s of new requests to be processed. A relayer will construct the `RequestMessage` which holds a batch of new `PostRequest`s, as well as a _multi-proof_<sup>[1]</sup> of their existence on the source chain. The handler will perform the following operations

- Assert that the state machine's consensus client is not frozen
- Assert that the configured `challenge_period` for the `StateCommitment` has elapsed
- Assert that the requests have not been previously processed
- Assert that the requests have not timed out
- Assert that the membership proof for the requests verify
- Finally dispatch the requests to the relevant `IsmpModule::on_accept` and store a receipt for each request to prevent requests from being replayed.

<Callout title={'Danger'} type={"warn"}>
It's important to note that if the `IsmpModule::on_accept` does not return `Ok`, the receipt of this request will not be persisted, allowing the request to be **replayed**. Consequently, the `IsmpModule` is responsible for maintaining all invariants before modifying it's internal state to prevent partial state changes that could result in critical vulnerabilities in their request handler. This model ensures that if a request cannot be executed successfully on a destination state machine, it can time out gracefully on the source.
</Callout>
```
