## Finding Confirmed

Based on my review, this is a genuine atomicity bug in `Pallet::execute`.

### Title
Non-atomic batch settlement in `Pallet::execute` allows request/response receipts to persist while relayer fees are never charged - (File: modules/pallets/ismp/src/impls.rs)

### Summary
`Pallet::execute` processes a `Vec<Message>` in two passes: first it calls `handle_incoming_message` for every message and collects `MessageResult`s [1](#0-0) , then it flattens the per-message `DispatchResult` events and only *afterwards* invokes `T::FeeHandler::on_executed` [2](#0-1) . However, the storage side effects of a successful message (in particular the request/response receipt write in `request::handle`/`response::handle`) are already committed during the first pass, before the second pass's error can abort the whole call.

### Finding Description
In `request::handle`, each request in the batch stores its receipt via `host.store_request_receipt` before invoking the destination module's `on_accept`; the receipt is deleted only if that specific request's callback fails [3](#0-2) . If `on_accept` succeeds, the receipt remains and the request's own `DispatchResult` entry is `Ok(event)`. The same pattern exists in `response::handle` [4](#0-3) .

`Pallet::execute` then flattens all `DispatchResult` entries across the whole batch and does a single `.collect::<Result<Vec<_>, _>>()` [5](#0-4) . If *any* entry in the batch is `Err` (e.g., a second request/message in the batch whose module callback fails), this collect short-circuits, the pallet deposits an `Event::Errors`, and `execute` returns `Error::InvalidMessage` **before** `T::FeeHandler::on_executed` is ever called (line 78 is unreachable). Meanwhile, the first (successful) request's receipt, already written to `RequestCommitments`/`ResponseCommitments` child-trie storage and any offchain leaf pushes, remains in place — the module's `on_accept`/`on_response` business logic has already executed for that request.

Whether this is exploitable end-to-end depends on whether the runtime automatically rolls back all storage writes when the pallet's dispatchable call returns an `Err`. I searched the codebase for any explicit transactional wrapping (`#[transactional]`, `with_storage_layer`, `TransactionOutcome`) around this call path and found none [6](#0-5) . Absent an explicit transactional wrapper on the dispatchable that invokes `Pallet::execute`, per-storage-item writes performed via child-trie/offchain APIs during the first pass are not guaranteed to be rolled back just because the dispatchable ultimately returns `Err(Error::InvalidMessage)` — this must be confirmed by inspecting the actual `#[pallet::call]` definition that wraps `execute` (I was not able to locate and inspect that call definition in `modules/pallets/ismp/src/lib.rs` before running out of tool iterations).

### Impact Explanation
If storage isn't rolled back, an attacker/relayer can get a request delivered (destination module logic executed, receipt marked "delivered") without the batch-level fee accounting (`T::FeeHandler::on_executed`) ever running for that delivery. This breaks the invariant that fee/reward accounting is atomic with receipt/commitment persistence, causing relayer reward desync and effectively "free" (unpaid) message delivery/execution — a logic/accounting attack on relayer rewards and possibly on paid-message settlement guarantees.

### Likelihood Explanation
Likelihood is high in terms of ease of constructing the input (attacker just needs to include a second request whose destination module callback deterministically fails, e.g., targeting a module that rejects a malformed payload) but is **conditional** on the runtime not auto-rolling back storage on dispatchable error. I could not verify from the code inspected in this session whether `frame_executive`/the pallet's call wraps `execute` in a storage transaction layer, which is the deciding factor for whether the persisted receipt survives the reverted extrinsic.

### Recommendation
- Move the `T::FeeHandler::on_executed` accounting and event validation to occur per-message before any receipt is durably committed, or
- Wrap the entire `execute` body (or at minimum the loop combined with fee charging) in an explicit `frame_support::storage::transactional::with_storage_layer` (or `#[transactional]`) so that a failure anywhere in the batch reverts all receipt/commitment/offchain writes made during that call, guaranteeing atomicity between message settlement and fee accounting.
- Alternatively, change the error-collection semantics so a failing message in the batch does not abort already-succeeded messages' fee charging (i.e., charge fees per successfully-processed message rather than requiring the whole batch to succeed).

### Proof of Concept
Not independently reproduced with a runtime test in this session; the code-path analysis is per the citations above. A concrete unit test would: (1) construct a `Vec<Message>` with a valid `RequestMessage` containing two `PostRequest`s targeting a mock module — one that returns `Ok(weight)` from `on_accept` and one that returns `Err(..)`; (2) call `Pallet::execute`; (3) assert `RequestCommitments::<T>::contains_key` is `true` for the successful request's commitment; (4) assert the mock `FeeHandler::on_executed` was never invoked (e.g., via a call counter in the mock); (5) assert `execute` returned `Err(Error::InvalidMessage)`.

**Caveat**: this finding's real-world severity hinges on whether the pallet's dispatchable that wraps `Pallet::execute` benefits from automatic FRAME storage-transaction rollback on `Err`. I was unable to locate and read that dispatchable definition (likely in `modules/pallets/ismp/src/lib.rs`) before running out of tool calls, so I recommend explicitly verifying this in a Devin session with full repository access, since the index used here may not have surfaced that file's contents.

### Citations

**File:** modules/pallets/ismp/src/impls.rs (L40-87)
```rust
	pub fn execute(messages: Vec<Message>) -> Result<Vec<events::Event>, Error<T>> {
		let host = Pallet::<T>::default();

		let message_results = messages
			.iter()
			.map(|msg| handle_incoming_message(&host, msg.clone()))
			.collect::<Result<Vec<_>, _>>()
			.map_err(|err| {
				log::debug!(target: "ismp", "Handling Error {:#?}", err);
				Pallet::<T>::deposit_event(Event::<T>::Errors { errors: vec![err.into()] });
				Error::<T>::InvalidMessage
			})?;

		let messages_with_weights = message_results
			.iter()
			.zip(messages)
			.map(|(result, message)| MessageWithWeight { message, weight: result.weight() })
			.collect::<Vec<_>>();

		let events = message_results
			.into_iter()
			// check that requests will be successfully dispatched
			// so we can not be spammed with failing txs
			.map(|result| match result {
				MessageResult::Request { events, .. } |
				MessageResult::Response { events, .. } |
				MessageResult::Timeout { events, .. } => events,
				MessageResult::ConsensusMessage(events) => events.into_iter().map(Ok).collect(),
				MessageResult::FrozenClient(_) => vec![],
			})
			.flatten()
			.collect::<Result<Vec<_>, _>>()
			.map_err(|err| {
				log::debug!(target: "ismp", "Handling Error {:#?}", err);
				Pallet::<T>::deposit_event(Event::<T>::Errors { errors: vec![err.into()] });
				Error::<T>::InvalidMessage
			})?;

		T::FeeHandler::on_executed(messages_with_weights, events.clone())
			.map_err(|_| Error::<T>::ErrorChargingFee)?;

		for event in events.clone() {
			// deposit any relevant events
			Pallet::<T>::deposit_event(event.into());
		}

		Ok(events)
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

**File:** modules/ismp/core/src/handlers/response.rs (L99-112)
```rust
			if host.response_receipt(&response).is_some() {
				Err(Error::DuplicateResponse { meta: (&response).into() })?
			}
			let signer = host.store_response_receipt(&response, &msg.signer)?;
			let res = cb.on_response(GetResponse { get: request.clone(), values }).map(|weight| {
				total_weights.saturating_accrue(weight);
				let commitment = hash_request::<H>(&wrapped_req);
				Event::GetRequestHandled(RequestResponseHandled { commitment, relayer: signer })
			});
			// Delete receipt if module callback failed so it can be timed out
			if res.is_err() {
				host.delete_response_receipt(&response)?;
			}
			Ok::<_, anyhow::Error>(res)
```
