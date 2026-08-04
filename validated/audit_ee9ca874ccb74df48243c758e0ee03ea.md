## Title
Bandwidth is debited even when the module callback fails, letting a permissionless replay of a single failed request repeatedly drain an app's bandwidth allowance - (File: `parachain/runtimes/gargantua/src/ismp.rs`, `parachain/runtimes/nexus/src/ismp.rs`, `modules/ismp/core/src/handlers/request.rs`)

### Summary
The Zetachain report shows a class of bug where a malformed/unhandled payload is not properly consumed or reverted, letting the erroring path be retried indefinitely with real side effects. In Hyperbridge's `ProxyModule::on_accept` (the router callback invoked by the core ISMP request handler), the bandwidth gate deducts real bytes from an app's `pallet-bandwidth` subscription **before** the destination module lookup is attempted. If the lookup fails (unknown/mistyped `to` module id), `on_accept` returns `Err`, but the bandwidth deduction is a plain storage mutation with no transactional rollback tied to that `Err`. The core handler (`modules/ismp/core/src/handlers/request.rs`) treats a failed `on_accept` as "retryable" by deleting the request receipt, which removes the only replay guard for that request — allowing the same message+proof to be resubmitted to `handle()` repeatedly by any relayer, debiting the victim app's bandwidth balance again on every replay.

### Finding Description
In `parachain/runtimes/gargantua/src/ismp.rs` (and the equivalent in `nexus/src/ismp.rs`):

```rust
fn on_accept(&self, request: PostRequest) -> Result<Weight, anyhow::Error> {
    // Bandwidth gate ... consumed BEFORE destination resolution
    if !pallet_bandwidth::Pallet::<Runtime>::is_purchase_message(&request) {
        let bytes = ismp::abi::encode_post_request(&request).len() as u32;
        <pallet_bandwidth::Pallet<Runtime> as pallet_bandwidth::BandwidthGate>::try_consume(
            &request.source, &request.from, bytes,
        ).map_err(...)?;
    }
    ...
    let pallet_id = ModuleId::from_bytes(&request.to).map_err(...)?;
    match pallet_id {
        ...
        _ => Err(anyhow!("Destination module not found")),
    }
}
```

`BandwidthGate::try_consume` in `modules/pallets/bandwidth/src/lib.rs` (lines 509-565) directly mutates `Allowance::<T>` via `pallet::Allowance::<T>::mutate(...)`, draining `remaining_bytes` from the FIFO subscription list and popping exhausted entries. This mutation is unconditional and irreversible from within `on_accept` — there is no `with_transaction`/`#[transactional]` wrapper anywhere in the repo (confirmed empty grep result) that would roll back this storage write when the later `Err(anyhow!("Destination module not found"))` is returned.

Back in the core handler `modules/ismp/core/src/handlers/request.rs`:
```rust
let res = cb.on_accept(request.clone()).map(|weight| { ... });
// Delete receipt if module callback failed so it can be timed out
if res.is_err() {
    host.delete_request_receipt(&wrapped_req)?;
}
```
The comment states the intent is to let a failed request "be timed out" — i.e., normal retry semantics for a legitimate transient failure. But because `handle()` ultimately returns `Ok(MessageResult::Request { events: result, ... })` regardless of individual per-request `Err`s (the `Vec<Result<...>>` is collected and wrapped in `Ok` at line ~134), the whole extrinsic succeeds at the FRAME dispatch level. No top-level revert occurs, so the already-executed `Allowance::<T>::mutate` bandwidth debit is permanently committed to storage.

Because the request receipt was deleted, `host.request_receipt(&req).is_some()` is `false` on a second submission of the identical `RequestMessage` (same request + same historical membership proof, which remains valid as long as the state commitment for that height is retained). Submitting proofs to `handle()` is a permissionless relayer operation. An attacker (or any third party who observes a request destined for a wrong/mistyped `to` module id, or simply repeats their own message that always fails at the destination-module-lookup step) can therefore resubmit the exact same request+proof arbitrarily many times. On each resubmission, the gate deducts real bandwidth bytes from `(request.source, request.from)` again, since the destination lookup will always fail identically and the receipt is deleted again — producing an infinite loop of "billable" bandwidth-only replays with no eventual success.

### Impact Explanation
This directly breaks the bandwidth-accounting invariant that "bandwidth balances must move exactly once and only to the rightful beneficiary and amount." A single message that fails to resolve to a destination module can be replayed without limit to fully drain a legitimate app's prepaid bandwidth subscription — a real resource/fund loss (the app paid `BandwidthManager.purchase()` fees for that allowance) — without the victim app doing anything except a benign misconfiguration (e.g. a `to` typo, or a destination module later being deprecated as seen in `nexus/src/ismp.rs`'s `is_deprecated_token_gateway` check). Once drained, all of the victim's subsequent legitimate cross-chain messages are rejected by the gate (`GateError::NoAllowance`/`Insufficient`), producing exactly the "withdrawals get stuck" DoS pattern from the source report, but here it is attacker-amplifiable and comes with actual economic loss of paid-for bandwidth rather than a benign relayer hang.

### Likelihood Explanation
Likelihood is high: submitting `RequestMessage`s with membership proofs to the ISMP handler is a permissionless relayer action by design (any relayer can carry/replay a message as long as the state commitment for the referenced height is still available, which is normal operation before pruning/challenge-period expiry). No privileged role, malicious node, or compromised relayer/prover is required — a completely ordinary, unprivileged party can just resend an already-observed request+proof pair. The only precondition is that some request eventually hits the `_ => Err(anyhow!("Destination module not found"))` (or any other post-gate failure) arm of `on_accept`, which is trivially reachable by a mistyped `to` field or a deprecated destination.

### Recommendation
- Consume bandwidth only after the destination module is confirmed to exist and the callback is otherwise guaranteed to make forward progress, or
- Wrap each per-request module dispatch (`cb.on_accept(...)`) in a storage transaction (`frame_support::storage::with_transaction`) that reverts all storage mutations performed during that specific `on_accept` call whenever it returns `Err`, so gate consumption is atomic with successful delivery, or
- If a receipt is deleted to permit retries, ensure the corresponding bandwidth debit (and any other side-effecting mutation performed by the failing callback) is also reverted in the same failure path.

### Proof of Concept
1. App `A` on a bandwidth-managed source chain dispatches a `PostRequest` with `to` set to a module id that does not (or no longer, per `is_deprecated_token_gateway`) resolve in `ProxyModule::on_accept`'s `match pallet_id { ... }`.
2. A relayer submits the `RequestMessage` (with valid membership proof) to `handle()` in `modules/ismp/core/src/handlers/request.rs`. `on_accept` consumes `bytes` from `A`'s bandwidth allowance via `try_consume`, then hits `_ => Err(anyhow!("Destination module not found"))`.
3. `res.is_err()` triggers `host.delete_request_receipt(&wrapped_req)`, and `handle()` still returns `Ok(...)` at the top level — the extrinsic succeeds and the bandwidth debit persists.
4. Any party (attacker) resubmits the identical `RequestMessage`/proof to `handle()` again. `host.request_receipt` is empty (deleted in step 3), so it is accepted as a fresh request; `try_consume` deducts bandwidth again; the same lookup failure repeats.
5. Repeat step 4 until `Allowance::<T>` for `(A's source chain, A)` is fully drained, confirmed via `pallet_bandwidth::Pallet::<T>::remaining(...)` returning 0, after which A's legitimate messages are rejected by `BandwidthGate::try_consume` with `GateError::NoAllowance`. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) 

**Note on confidence**: I was unable to load `modules/pallets/ismp/src/impls.rs` and `modules/ismp/core/src/handlers.rs` in full (tool errors on the final iteration) to conclusively confirm the outer extrinsic-level dispatch always returns `Ok`/commits storage regardless of per-request `Err`s inside the batch. The grep for `with_transaction`/`#[transactional]` returned zero matches repo-wide, which supports the absence of any rollback mechanism, but a full read of the extrinsic entry point would strengthen this claim further. If a Devin session has access to the full repository, verifying the exact dispatchable that calls `handlers::request::handle` and confirming it does not wrap module callbacks in a storage transaction would close this last gap.

### Citations

**File:** parachain/runtimes/gargantua/src/ismp.rs (L375-421)
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
```

**File:** parachain/runtimes/nexus/src/ismp.rs (L361-409)
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

**File:** modules/ismp/core/src/handlers/request.rs (L99-135)
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
			};

			let res = lambda().and_then(|res| res);
			res
		})
		.collect::<Vec<_>>();

	Ok(MessageResult::Request { events: result, weight: total_weights })
}
```
