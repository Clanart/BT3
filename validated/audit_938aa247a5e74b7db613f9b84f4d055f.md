Based on what I was able to confirm in the code, here's my assessment.

### What the code shows

The pallet defines a bounded retention window for state commitments per state machine, governed by a cap: [1](#0-0) 

This confirms the mechanism described in the finding: `BoundedStateCommitments` is retained per chain up to `StateMachineCommitmentCap` (or the `MAX_STATE_MACHINE_COMMITMENTS` default), and `insert_bounded_state_commitment` performs FIFO eviction (up to `MAX_COMMITMENT_EVICTIONS_PER_INSERT` entries per insert) purely to keep the queue within that count-based cap — the comments make no mention of checking whether an evicted entry's challenge period has elapsed.

The eviction is invoked from the `IsmpHost::store_state_machine_commitment` hook, which is called on every successful consensus update: [2](#0-1) 

And retrieval for proof verification (`state_machine_commitment`) simply does a direct lookup with no fallback if the entry was evicted: [3](#0-2) 

Separately, `challenge_period`/`state_machine_update_time` are stored and read independently of the FIFO cap — nothing in the design ties the eviction count to elapsed challenge-period time: [4](#0-3) [5](#0-4) 

### Limitation

I was not able to read the actual body of `insert_bounded_state_commitment` (the function definition itself, beyond its doc comment) before the tool budget ran out, so I cannot cite the exact eviction algorithm (e.g., whether it uses a separate ring-buffer/queue storage item, or scans `BoundedStateCommitments` directly) or confirm whether there is a challenge-period-aware guard inside the function that the doc comments simply don't mention. The doc comments strongly suggest the eviction is purely count-based (`StateMachineCommitmentCap`) and has no time/challenge-period condition, which is consistent with the reported vulnerability, but this should be verified against the full function body before treating this as conclusively confirmed.

Given this, if the function indeed has no challenge-period check (which the surrounding documentation implies), the finding is valid:

### Title
FIFO eviction in `insert_bounded_state_commitment` can purge in-challenge-period `BoundedStateCommitments` entries before their challenge period elapses - (File: modules/pallets/ismp/src/lib.rs)

### Summary
`insert_bounded_state_commitment` bounds `BoundedStateCommitments` per `StateMachineId` to `StateMachineCommitmentCap` entries using FIFO eviction on every new consensus-derived state commitment insert, with no check on whether the evicted entry's challenge period (tracked separately via `ChallengePeriod`/`BoundedStateMachineUpdateTime`) has elapsed.

### Finding Description
An unprivileged relayer can submit `Message::Consensus` updates through `handle_unsigned` at a rate faster than the challenge period. Each successful consensus update calls `store_state_machine_commitment` → `Pallet::insert_bounded_state_commitment`, which inserts a new entry and evicts old ones once the per-`StateMachineId` cap is exceeded. Because eviction is driven purely by the count of stored heights and not by whether `now < state_machine_update_time + challenge_period`, an entry that is still within its challenge window (i.e., still legitimately awaiting acceptance/use for proof verification) can be evicted from `BoundedStateCommitments` and `BoundedStateMachineUpdateTime` before that window elapses.

### Impact Explanation
Once evicted, `state_machine_commitment` returns `Error::StateCommitmentNotFound` for that height [3](#0-2) , so any request/response/timeout proof anchored to that height can no longer be verified — a false rejection of an otherwise-legitimately-anchored state root. This does not by itself allow forged proof acceptance, but it breaks availability/correctness of the proof-verification path and could be leveraged to grief pending cross-chain messages (denial of settlement) by flooding consensus updates.

### Likelihood Explanation
Requires only unprivileged submission of a sequence of valid consensus messages (assuming the attacker is a permitted relayer able to call `handle_unsigned` — this is a routine, unprivileged action in ISMP), so likelihood is high if `StateMachineCommitmentCap` is set low relative to the counterparty chain's consensus update rate and its challenge period.

### Recommendation
Guard eviction in `insert_bounded_state_commitment` so that an entry is never removed while `timestamp() < state_machine_update_time + challenge_period`, or size `StateMachineCommitmentCap` dynamically/defensively based on expected update rate × challenge period, and/or skip storing new heights (or reject the update) rather than evicting unexpired entries when the queue is full.

### Proof of Concept
1. Configure `StateMachineCommitmentCap` for a `StateMachineId` to a small value (e.g., 2).
2. Set `ChallengePeriod` for the same `StateMachineId` to a nontrivial duration.
3. Insert an initial commitment at height H0 via a valid `Message::Consensus`.
4. Before `now >= state_machine_update_time(H0) + challenge_period`, submit `cap` additional valid consensus updates in rapid succession.
5. Assert `BoundedStateCommitments::get(id, H0)` is `None` even though `now < state_machine_update_time(H0) + challenge_period`, demonstrating premature eviction of an in-window commitment.

Note: this PoC and full confirmation require inspecting the actual body of `insert_bounded_state_commitment`, which I could not retrieve within the available tool budget — recommend verifying the eviction loop directly before treating this as fully confirmed.

### Citations

**File:** modules/pallets/ismp/src/lib.rs (L86-100)
```rust

	/// Default number of state commitments retained per chain in
	/// [`BoundedStateCommitments`]. Chains can be given a different retention
	/// depth via [`StateMachineCommitmentCap`], sized to their finality
	/// cadence: a chain that finalizes every few seconds needs a much larger
	/// cap than one that finalizes every few minutes to cover the same
	/// wall-clock window.
	pub const MAX_STATE_MACHINE_COMMITMENTS: u32 = 10_240;

	/// Upper bound on evictions performed by a single
	/// [`Pallet::insert_bounded_state_commitment`] call. At steady state each
	/// insertion evicts exactly one entry; the headroom lets the queue drain
	/// gradually after a per-chain cap is lowered without unbounded work in
	/// one call.
	pub const MAX_COMMITMENT_EVICTIONS_PER_INSERT: u32 = 4;
```

**File:** modules/pallets/ismp/src/host.rs (L59-65)
```rust
	fn state_machine_commitment(
		&self,
		height: StateMachineHeight,
	) -> Result<StateCommitment, Error> {
		BoundedStateCommitments::<T>::get(height.id, height.height)
			.ok_or_else(|| Error::StateCommitmentNotFound { height })
	}
```

**File:** modules/pallets/ismp/src/host.rs (L73-85)
```rust
	fn state_machine_update_time(
		&self,
		state_machine_height: StateMachineHeight,
	) -> Result<Duration, Error> {
		BoundedStateMachineUpdateTime::<T>::get(
			state_machine_height.id,
			state_machine_height.height,
		)
		.map(|timestamp| Duration::from_secs(timestamp))
		.ok_or_else(|| {
			Error::Custom(format!("Update time not found for {:?}", state_machine_height))
		})
	}
```

**File:** modules/pallets/ismp/src/host.rs (L185-192)
```rust
	fn store_state_machine_commitment(
		&self,
		height: StateMachineHeight,
		state: StateCommitment,
	) -> Result<(), Error> {
		Pallet::<T>::insert_bounded_state_commitment(height, state);
		Ok(())
	}
```

**File:** modules/pallets/ismp/src/host.rs (L289-291)
```rust
	fn challenge_period(&self, state_machine: StateMachineId) -> Option<Duration> {
		ChallengePeriod::<T>::get(&state_machine).map(Duration::from_secs)
	}
```
