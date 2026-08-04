## Analysis

The TON bug's core defect is: **the "last processed" cursor is advanced based on what was *fetched*, not on what was actually *delivered/processed*, so a downstream failure/truncation causes a permanent, unrecoverable skip of the un-relayed items.**

The exact same defect exists in `handle_update` in the Hyperbridge inbound messaging pipeline.

### Title
Inbound relayer height cursor advances before message delivery is confirmed, causing permanent silent loss of ISMP requests/responses - (File: `tesseract/messaging/messaging/src/lib.rs`)

### Summary
`handle_update` mutates the caller-owned `previous_height` cursor to `state_machine_update.latest_height` immediately after fetching and filtering events, *before* those events have been translated into messages or successfully submitted to the destination chain. [1](#0-0) 

### Finding Description
`previous_height` is a `&mut u64` owned by the long-lived loop in `handle_notification`, and it defines the range that will be queried on the *next* tick via `chain_b.query_ismp_events(*previous_height, ...)`. [2](#0-1) [3](#0-2) 

After events for the range `(previous_height, latest_height]` are fetched and filtered, the code unconditionally advances the cursor:

```rust
// Advance latest known height by relayer
*previous_height = state_machine_update.latest_height;
``` [4](#0-3) 

This happens **before**:
1. `translate_events_to_messages` is called, which can fail and propagate via `?`, aborting the function early: [5](#0-4) 
2. `chain_a.submit(messages, ...)` is even attempted, and before its result is known: [6](#0-5) 

If submission fails outright (RPC error, nonce collision, gas estimation failure, node downtime, etc.), the code simply logs the error and returns — it does **not** roll back or retry the cursor:
```rust
Err(err) => {
    tracing::error!(..., "Failed to submit transaction",)
},
``` [7](#0-6) 

Because `previous_height` was already advanced to `latest_height` at line 339, the *next* invocation of `handle_update` will only query events strictly after `latest_height`. The events (PostRequest, GetRequest, GetResponse) that failed to submit in this range are never queried, translated, or retried again — they are permanently orphaned from the relaying pipeline. This is structurally identical to the TON `ObserveInbound` bug: the "watermark"/cursor is moved forward based on what was *seen*, not on what was *durably processed*, and a downstream truncation/failure (there: `maxTransactionsPerTick` truncation; here: submission or translation failure) causes the un-processed tail to be permanently skipped on the next iteration.

Note: unlike the unprofitable-message path (which explicitly persists unprofitable messages to a retry DB via `store_unprofitable_messages`, [8](#0-7) ), there is **no such persistence/retry path** for messages that fail during `translate_events_to_messages` or whose `submit` call errors entirely (the `Err(err)` branch at line 465-473 only logs).

### Impact Explanation
Cross-chain `PostRequest`/`GetRequest`/`GetResponse` messages that fail to be translated or submitted for transient reasons (RPC hiccup, gas spike, nonce race, temporary node unavailability) are dropped from the relaying pipeline permanently, since the cursor has already moved past their block range and will never re-query it. This directly causes loss of relayed requests/responses — funds or state associated with those cross-chain messages (e.g., token transfers via ISMP post requests) can be lost or become permanently stuck since no relayer will ever observe and resubmit them, and the request will eventually only be resolvable via timeout (if the source chain even supports timing out that specific request), which may not restore the original economic outcome.

### Likelihood Explanation
This requires no malicious actor — any transient submission failure (a very common, expected occurrence in blockchain relayers: RPC timeouts, gas re-estimation failures, temporary node outages, nonce management races) is enough to trigger it, since the cursor-advance-before-confirm ordering is unconditional on every call to `handle_update`. This differs from the "malicious relayer" exclusion in the bounty scope — it is a logic bug in the relayer's own bookkeeping that causes real fund-relevant messages to be silently and permanently dropped, not a misbehaving/malicious operator.

### Recommendation
Only advance `*previous_height` after the fetched events have been durably handled: either (a) successfully submitted (or explicitly filtered as duplicate/irrelevant), or (b) persisted to the unprofitable/retry store as is already done for other failure paths. On `translate_events_to_messages` error or `submit` error, do not advance the cursor for that block range — instead retry the same range on the next tick (or persist the un-relayed events for retry), mirroring the fix applied upstream in the TON report (reversing/reprocessing rather than skipping) so no in-range event is silently dropped.

### Proof of Concept
1. `previous_height = H0`. A new `StateMachineUpdated { latest_height: H1 }` arrives.
2. `query_ismp_events(H0, H1)` returns a non-empty set of `PostRequest`/`GetRequest` events in `(H0, H1]`.
3. Line 339 executes: `*previous_height = H1` — cursor advanced immediately.
4. `translate_events_to_messages` (line 360) errors (e.g., a proof-fetch/provider RPC failure) and `handle_update` returns via `?` before `chain_a.submit` is ever called — or `submit` itself returns `Err` due to a transient RPC/node failure (line 465).
5. `handle_notification`'s loop logs the error and simply awaits the next `state_machine_update` (lines 233-242) — it never retries the failed range because `previous_height` is now `H1`.
6. The next `StateMachineUpdated { latest_height: H2 }` triggers `query_ismp_events(H1, H2)`, which never includes the events from `(H0, H1]`.
7. All requests/responses in `(H0, H1]` are permanently lost from the relaying pipeline — equivalent to the TON scenario where transactions `0..29` were never executed after the cursor skipped ahead to `130`. [9](#0-8)

### Citations

**File:** tesseract/messaging/messaging/src/lib.rs (L215-255)
```rust
	let mut previous_height = chain_b.initial_height();

	while let Some(item) = state_machine_update_stream.next().await {
		match item {
			Ok(state_machine_update) => {
				if let Err(err) = handle_update(
					chain_a.clone(),
					chain_b.clone(),
					tx_payment.clone(),
					state_machine_update.clone(),
					&mut previous_height,
					config.clone(),
					coprocessor,
					&client_map,
					fee_acc_sender.clone(),
					get_request_sender.clone(),
				)
				.await
				{
					tracing::error!(
						target: LOG_TARGET,
						source = %chain_b.name(),
						dest = %chain_a.name(),
						state_machine = %state_machine_update.state_machine_id.state_id,
						?err,
						"Error while handling state machine update",
					);
				}
			},
			Err(e) => {
				tracing::error!(
					target: LOG_TARGET,
					source = %chain_b.name(),
					dest = %chain_a.name(),
					err = ?e,
					"Messaging task state-machine-update stream error",
				);
				continue;
			},
		}
	}
```

**File:** tesseract/messaging/messaging/src/lib.rs (L279-279)
```rust
	let result = chain_b.query_ismp_events(*previous_height, state_machine_update.clone()).await;
```

**File:** tesseract/messaging/messaging/src/lib.rs (L326-339)
```rust

	let state_machine = state_machine_update.state_machine_id.state_id;
	if events.is_empty() {
		tracing::info!(
			target: LOG_TARGET, "Skipping latest finalized height {} on {}, no new messages from {state_machine} in range {:?}",
			state_machine_update.latest_height,
			chain_a.name(),
			*previous_height..=state_machine_update.latest_height
		);
		*previous_height = state_machine_update.latest_height;
		return Ok(());
	}
	// Advance latest known height by relayer
	*previous_height = state_machine_update.latest_height;
```

**File:** tesseract/messaging/messaging/src/lib.rs (L360-372)
```rust
	let (messages, unprofitable) = translate_events_to_messages(
		chain_b.clone(),
		chain_a.clone(),
		events,
		state_machine_height.clone(),
		config.clone(),
		coprocessor,
		&client_map,
		// Inbound pipeline doesn't batch a consensus message alongside — the
		// dest's light client is advanced by a separate consensus task.
		None,
	)
	.await?;
```

**File:** tesseract/messaging/messaging/src/lib.rs (L381-383)
```rust
		let res = chain_a.submit(messages.clone(), coprocessor).await;
		match res {
			Ok(TxResult { receipts, unsuccessful, new_epochs: _ }) => {
```

**File:** tesseract/messaging/messaging/src/lib.rs (L465-473)
```rust
			Err(err) => {
				tracing::error!(
					target: LOG_TARGET,
					source = %chain_b.name(),
					dest = %chain_a.name(),
					?err,
					"Failed to submit transaction",
				)
			},
```

**File:** tesseract/messaging/messaging/src/lib.rs (L477-500)
```rust
	// Store currently unprofitable in messages in db
	if !unprofitable.is_empty() &&
		config.unprofitable_retry_frequency.is_some() &&
		chain_a.state_machine_id().state_id != coprocessor
	{
		tracing::trace!(
			target: LOG_TARGET,
			source = %chain_b.name(),
			dest = %chain_a.name(),
			count = unprofitable.len(),
			"Persisting unprofitable messages to the db",
		);
		if let Err(err) = tx_payment
			.store_unprofitable_messages(unprofitable, chain_a.state_machine_id().state_id)
			.await
		{
			tracing::error!(
				target: LOG_TARGET,
				source = %chain_b.name(),
				dest = %chain_a.name(),
				?err,
				"Error while storing unprofitable messages in the database",
			)
		}
```
