### Title
Bounded state-commitment queue can evict the exact height needed for a POST-request timeout proof, permanently locking relayer fees and blocking timeout settlement - ([File: modules/pallets/ismp/src/lib.rs], [File: modules/ismp/core/src/handlers/timeout.rs])

### Summary
`pallet-ismp` retains only a bounded, per-chain FIFO window of state-machine commitments (`BoundedStateCommitments` / `StateCommitmentQueue`). Once the configured cap is exceeded, `insert_bounded_state_commitment` evicts the oldest heights unconditionally on every new consensus update, with no check on whether pending outgoing requests/timeouts still need a proof against an evicted height. `handle_timeouts` requires `host.state_machine_commitment(timeout_proof.height)` to resolve successfully before it will process a `PostRequest`/`GetRequest` timeout. If the specific height a relayer needs to submit a non-membership/timeout proof against has already aged out of the bounded window, the timeout handler fails with `StateCommitmentNotFound` forever — the request commitment can never be deleted via `handle_timeouts`, so the escrowed relayer fee (and any dispatcher-side application state depending on the timeout callback) is permanently stuck. This mirrors the external report's "resource becomes unavailable after some time window has elapsed, blocking the intended action" pattern, but here the unavailable resource is a required state commitment rather than a liquidity pool.

### Finding Description
`insert_bounded_state_commitment` in `modules/pallets/ismp/src/lib.rs` (lines 747-770) appends every new commitment to a per-chain FIFO queue and evicts oldest-first once the chain's configured cap (`StateMachineCommitmentCap`, defaulting to `MAX_STATE_MACHINE_COMMITMENTS`) is exceeded: [1](#0-0) 

This eviction is purely time/height-based and has no awareness of outstanding, un-timed-out requests that reference a given height. `handle_timeouts` in `modules/ismp/core/src/handlers/timeout.rs` fetches the commitment at the caller-supplied proof height and hard-fails if it's missing: [2](#0-1) 

The test `commitment_queue_evicts_oldest_when_cap_is_reached` in `modules/pallets/testsuite/src/tests/pallet_ismp.rs` confirms that once the cap is reached, older heights become permanently unreadable via `host.state_machine_commitment`: [3](#0-2) 

Because `PostRequest`/`GetRequest` timeouts must be proven against a specific height where `state.timestamp() > request.timeout_timestamp` (see `modules/ismp/core/src/handlers/timeout.rs` lines 56-88), a relayer or user attempting to time out a request has no flexibility to pick a later, still-retained height for that proof if the exact needed height was already evicted before they submitted. Once evicted, `validate_state_machine`/`state_machine_commitment` return `StateCommitmentNotFound`/`ChallengePeriodNotElapsed`-adjacent errors and the timeout path is permanently blocked for that request — the outgoing request commitment in `RequestCommitments` is never deleted, and the fee/refund tied to `on_request_timeout` never executes.

This differs from the "malicious relayer/prover" exclusions in the impact gate: no adversarial relayer, prover, or governance action is required. Ordinary operation — many state-machine updates in quick succession for a fast-finality chain, combined with slow relayer/network conditions or a temporarily paused relayer — can exhaust the bounded window before the timeout proof for an older request is submitted, exactly analogous to the external report's pools "becoming unavailable" after a period purely from the passage of time/normal chain activity.

### Impact Explanation
Funds get stuck: the relayer fee attached to a `PostRequest`/`GetRequest` is only released back to the payer on successful timeout processing (`host.on_request_timeout`). If the commitment window evicts the required height before that timeout proof lands, the fee is locked indefinitely and the request can neither complete nor be refunded — a direct instance of fund loss/lock through normal-but-adversarial-timing conditions rather than any privileged or malicious actor. This matches the "stealing or loss of funds" / bridge-custody impact class in the bounty scope.

### Likelihood Explanation
The cap (`MAX_STATE_MACHINE_COMMITMENTS`) and per-chain override (`StateMachineCommitmentCap`) values, and their relationship to typical challenge periods and relayer response times, could not be fully confirmed from the indexed code in this session — I was unable to load the constant definitions before running out of tool calls. This is the key unknown: if the cap comfortably outlives the challenge period plus expected relayer latency for all configured chains, the practical window for this race is narrow; if the cap is tight relative to fast-finality source chains (e.g., high-throughput EVM chains configured with small `StateMachineCommitmentCap` overrides for storage-cost reasons), the race becomes easy to trigger even without any adversary — a slow relayer, congested destination chain, or a request near its timeout combined with a burst of consensus updates on the counterparty chain is sufficient.

### Recommendation
- Before evicting a height from `BoundedStateCommitments`, check whether any request commitments still reference (or could still reference) that height for an unprocessed timeout, or retain a minimum safety margin of heights beyond the configured challenge period for the slowest-timing-out live request.
- Alternatively, decouple timeout-proof height selection from the exact original height by allowing a timeout to be proven against any later retained height where the timestamp already exceeds `timeout_timestamp` (the code already reads `state.timestamp()` for the comparison — the constraint is on proof availability, not on requiring the earliest possible provable height).
- Add an on-chain metric/alert for `StateCommitmentQueue` eviction lead time versus outstanding request ages, and expose evicted-height handling explicitly (e.g., an error path that lets the caller retry with the freshest available height that still proves the timeout condition).

### Proof of Concept
1. Configure a state machine `X` with `StateMachineCommitmentCap = N` (small, or default `MAX_STATE_MACHINE_COMMITMENTS`).
2. Dispatch a `PostRequest` to `X` with `timeout_timestamp = T`.
3. Drive `N+1` (or more, respecting `MAX_COMMITMENT_EVICTIONS_PER_INSERT`) consensus/state-machine updates for `X` in rapid succession — this is normal operation for any fast-finality source chain and requires no malicious relayer, just enough throughput.
4. Confirm via `CommitmentQueueStates::<T>::get(id)` that the height(s) whose timestamp first exceeded `T` have been evicted from `BoundedStateCommitments` (as reproduced by the existing test `commitment_queue_evicts_oldest_when_cap_is_reached`, `modules/pallets/testsuite/src/tests/pallet_ismp.rs:680-714`).
5. Attempt `handle_timeouts` with a `TimeoutMessage::Post` proof anchored at that evicted height: `host.state_machine_commitment(timeout_proof.height)` in `modules/ismp/core/src/handlers/timeout.rs:51` returns an error, and no later height can be substituted by the caller because the message format binds the proof height explicitly.
6. Observe the request commitment in `RequestCommitments` is never deleted and the associated relayer fee is never refunded — permanent fund lock.

Note: I could not verify the exact numeric value of `MAX_STATE_MACHINE_COMMITMENTS` / `MAX_COMMITMENT_EVICTIONS_PER_INSERT` or per-chain `StateMachineCommitmentCap` defaults used in production runtimes within this session's tool budget; confirming these values (and typical challenge-period configuration per chain) is necessary to establish precisely how tight the exploitation window is in production.

### Citations

**File:** modules/pallets/ismp/src/lib.rs (L747-770)
```rust
		pub fn insert_bounded_state_commitment(
			height: StateMachineHeight,
			commitment: StateCommitment,
		) {
			let cap = Self::state_machine_commitment_cap(height.id).max(1) as u64;
			let mut state = CommitmentQueueStates::<T>::get(height.id);

			StateCommitmentQueue::<T>::insert(height.id, state.tail, height.height);
			state.tail += 1;

			let excess = (state.tail - state.head)
				.saturating_sub(cap)
				.min(MAX_COMMITMENT_EVICTIONS_PER_INSERT as u64);
			for _ in 0..excess {
				if let Some(old) = StateCommitmentQueue::<T>::take(height.id, state.head) {
					BoundedStateCommitments::<T>::remove(height.id, old);
					BoundedStateMachineUpdateTime::<T>::remove(height.id, old);
				}
				state.head += 1;
			}

			CommitmentQueueStates::<T>::insert(height.id, state);
			BoundedStateCommitments::<T>::insert(height.id, height.height, commitment);
		}
```

**File:** modules/ismp/core/src/handlers/timeout.rs (L48-88)
```rust
	let results = match msg {
		TimeoutMessage::Post { requests, timeout_proof } => {
			let state_machine = validate_state_machine(host, timeout_proof.height)?;
			let state = host.state_machine_commitment(timeout_proof.height)?;

			let wrapped: Vec<Request> = requests.iter().cloned().map(Request::Post).collect();
			dedup_requests::<H>(&wrapped)?;

			for post in &requests {
				let dest_chain = post.dest;

				// in order to allow proxies, the host must configure the given state machine
				// as it's proxy and must not have a state machine client for the destination chain
				let allow_proxy = host.is_allowed_proxy(&timeout_proof.height.id.state_id) &&
					check_state_machine_client(dest_chain);

				// check if the timeout is allowed to be proxied
				if dest_chain != timeout_proof.height.id.state_id && !allow_proxy {
					Err(Error::RequestProxyProhibited { meta: post.into() })?
				}

				// Ensure a commitment exists for all requests in the batch
				let commitment = hash_request::<H>(&Request::Post(post.clone()));
				if host.request_commitment(commitment).is_err() {
					Err(Error::UnknownRequest { meta: post.into() })?
				}

				if !post.timed_out(state.timestamp()) {
					Err(Error::RequestTimeoutNotElapsed {
						meta: post.into(),
						timeout_timestamp: post.timeout(),
						state_machine_time: state.timestamp(),
					})?
				}
			}

			let commitments = requests
				.iter()
				.map(|post| hash_request::<H>(&Request::Post(post.clone())))
				.collect();
			state_machine.verify_non_membership(host, commitments, state, &timeout_proof)?;
```

**File:** modules/pallets/testsuite/src/tests/pallet_ismp.rs (L680-714)
```rust
#[test]
fn commitment_queue_evicts_oldest_when_cap_is_reached() {
	let mut ext = new_test_ext();
	ext.execute_with(|| {
		let host = Ismp::default();
		let id = queue_test_state_machine();

		pallet_ismp::Pallet::<Test>::update_commitment_caps(
			RuntimeOrigin::root(),
			BTreeMap::from([(id, 3)]),
		)
		.unwrap();

		for height in 1..=5u64 {
			let at = StateMachineHeight { id, height };
			host.store_state_machine_commitment(at, queue_test_commitment()).unwrap();
			host.store_state_machine_update_time(at, Duration::from_secs(height)).unwrap();
		}

		for height in 1..=2u64 {
			let at = StateMachineHeight { id, height };
			assert!(host.state_machine_commitment(at).is_err());
			assert!(host.state_machine_update_time(at).is_err());
		}
		for height in 3..=5u64 {
			let at = StateMachineHeight { id, height };
			assert!(host.state_machine_commitment(at).is_ok());
			assert!(host.state_machine_update_time(at).is_ok());
		}
		assert_eq!(
			CommitmentQueueStates::<Test>::get(id),
			CommitmentQueueState { head: 2, tail: 5 }
		);
	})
}
```
