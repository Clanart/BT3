This is a real local analog to the RFX borrowing-factor bug: a rate-based accrual is computed using the **current** rate applied over a **historical** block span, with no per-period snapshotting, so a rate change silently retroactively re-prices already-elapsed periods.

### Title
Consensus-incentive reward retroactively re-prices unclaimed block span at the current rate instead of the historical rate - (File: `modules/pallets/consensus-incentives/src/impls.rs`)

### Summary
`pallet-consensus-incentives` pays relayers for delivering consensus updates based on `blocks_since_last_reward * current_cost_per_block`. The `cost_per_block` used is always the value read *at claim time*, never the value that was actually in effect during the elapsed block span. This is structurally identical to the RFX `CUMULATIVE_BORROWING_FACTOR` bug: a rate-derived accrual is not checkpointed/settled before the rate changes, so the next settlement misapplies the new rate to the old period.

### Finding Description
`calculate_reward` computes the reward as: [1](#0-0) 

`baseline` is the `LastRewardedHeight` watermark (or `previous_height` on first reward), and `blocks = latest_height - baseline` can span an arbitrary, unbounded number of past blocks — there is no requirement to claim promptly. `block_cost` is read fresh from `StateMachinesCostPerBlock` at the moment `process_message` runs: [2](#0-1) 

`StateMachinesCostPerBlock` is a single mutable value per `state_machine_id`, with no history of what the cost was for a given historical range: [3](#0-2) [4](#0-3) 

The `LastRewardedHeight` watermark is only ever advanced when a reward is actually settled: [5](#0-4) 

This means the watermark never "checkpoints" the accrual before the rate is allowed to change — exactly the missing step the RFX report calls out (update the accruing state *before* the input that drives its rate changes). Any relayer can choose *when* to deliver the next consensus proof for a given `state_machine_id` (the delivering account is simply the on-chain signer of the consensus message, an ordinary, permissionless relaying action): [6](#0-5) 

By withholding delivery of a state machine's consensus update while blocks accumulate, then delivering it only after governance raises `cost_per_block` (a routine, publicly observable operational action, not an attack), the relayer collects `blocks_since_last_reward * new_higher_cost_per_block` for a span that was never priced at that rate. The treasury pays out more than was ever authorized for that historical period.

### Impact Explanation
This is a direct loss-of-funds path from the protocol treasury: `T::Currency::transfer` moves treasury balance to the relayer sized by an incorrect (inflated) rate. [7](#0-6) 

Because rewards accumulate over an unbounded, relayer-controlled span before settlement, the magnitude of the overpayment scales with how long a relayer withholds delivery and how much the rate is later raised — there is no cap tying a rate to the period it actually covered.

### Likelihood Explanation
No privileged or malicious actor is required: delivering the consensus message that triggers `on_executed`/`process_message` is a normal, permissionless relayer action, and governance updating `cost_per_block` is routine, expected operation (visible on-chain via `StateMachineCostPerBlockUpdated`). Any relayer that is already integrated with a chain simply needs to delay a delivery across a rate increase to profit — no coordination with governance, no compromised keys, and no exploitation of a race condition; it's a deterministic consequence of the missing per-period rate snapshot.

### Recommendation
Checkpoint/settle the accrued reward whenever `update_cost_per_block` (or `remove_incentives`) changes the rate for a `state_machine_id`, by paying out (or recording) the reward owed at the *old* rate up to the current `latest_commitment_height` and resetting `LastRewardedHeight` at that point — mirroring how `updateFundingAndBorrowingState` must run before any rate-affecting change in the RFX fix. Alternatively, store a rate history keyed by height ranges so `calculate_reward` can integrate the correct historical rate(s) over `[baseline, latest_height]` instead of applying a single current rate to the whole span.

### Proof of Concept
1. Governance sets `StateMachinesCostPerBlock[SM] = 1` via `update_cost_per_block`.
2. A relayer delivers consensus updates for `SM` normally for a while, keeping `LastRewardedHeight[SM]` close to `latest_commitment_height`.
3. The relayer then stops delivering updates for `SM` for a long period; `LastRewardedHeight[SM]` stays frozen at height `H0` while `latest_commitment_height` for `SM` (as tracked by the host from other relayers/consensus updates) advances to `H0 + N` for a large `N`.
4. Governance raises `StateMachinesCostPerBlock[SM]` to `100` (a routine parameter update, e.g. to reflect increased infra costs).
5. The same relayer finally delivers the next consensus message for `SM`. `process_message` → `calculate_reward` computes `reward = N * 100`, even though the entire span `[H0, H0+N]` was priced at `1` while it was accruing.
6. The treasury pays out `N * 100` instead of the `N * 1` that should have applied to that historical span — a direct, unauthorized overpayment sized purely by the relayer's choice of delivery timing.

### Citations

**File:** modules/pallets/consensus-incentives/src/impls.rs (L46-51)
```rust
		if let Some(block_cost) = StateMachinesCostPerBlock::<T>::get(state_machine_id) {
			let reward = Self::calculate_reward(&state_machine_id, block_cost)?;

			if reward.is_zero() {
				return Ok(());
			}
```

**File:** modules/pallets/consensus-incentives/src/impls.rs (L53-59)
```rust
			T::Currency::transfer(
				&T::TreasuryAccount::get().into_account_truncating(),
				&relayer_account,
				reward,
				Preservation::Expendable,
			)
			.map_err(|_| Error::<T>::RewardTransferFailed)?;
```

**File:** modules/pallets/consensus-incentives/src/impls.rs (L70-72)
```rust
			LastRewardedHeight::<T>::mutate(state_machine_id, |watermark| {
				*watermark = Some(watermark.unwrap_or_default().max(state_machine_height.height));
			});
```

**File:** modules/pallets/consensus-incentives/src/impls.rs (L92-99)
```rust
		let baseline = LastRewardedHeight::<T>::get(state_machine_id).unwrap_or(previous_height);

		let blocks = latest_height.saturating_sub(baseline);

		let blocks_as_balance: <T as pallet_ismp::Config>::Balance = blocks.saturated_into();
		let reward = blocks_as_balance.saturating_mul(block_cost);

		Ok(reward)
```

**File:** modules/pallets/consensus-incentives/src/impls.rs (L112-122)
```rust
		let maybe_relayer_account = messages.get(0).and_then(|first_message| {
			if let Message::Consensus(consensus_msg) = &first_message.message {
				let data = sp_io::hashing::keccak_256(&consensus_msg.consensus_proof);
				Signature::decode(&mut &consensus_msg.signer[..])
					.ok()
					.and_then(|sig| sig.verify_and_get_sr25519_pubkey(&data, None).ok())
					.map(|pub_key| pub_key.into())
			} else {
				None::<[u8; 32]>
			}
		});
```

**File:** modules/pallets/consensus-incentives/src/lib.rs (L70-79)
```rust
	// Mapping from state machineId to respective cost per block
	#[pallet::storage]
	#[pallet::getter(fn state_machines_cost_per_block)]
	pub type StateMachinesCostPerBlock<T: Config> = StorageMap<
		_,
		Blake2_128Concat,
		StateMachineId,
		<T as pallet_ismp::Config>::Balance,
		OptionQuery,
	>;
```

**File:** modules/pallets/consensus-incentives/src/lib.rs (L128-150)
```rust
	#[pallet::call]
	impl<T: Config> Pallet<T> {
		/// Update cost per block for a state machine
		#[pallet::call_index(0)]
		#[pallet::weight(T::WeightInfo::update_cost_per_block())]
		pub fn update_cost_per_block(
			origin: OriginFor<T>,
			state_machine_id: StateMachineId,
			cost_per_block: <T as pallet_ismp::Config>::Balance,
		) -> DispatchResult {
			T::IncentivesOrigin::ensure_origin(origin)?;

			StateMachinesCostPerBlock::<T>::mutate(state_machine_id.clone(), |maybe_cost| {
				*maybe_cost = Some(cost_per_block);
			});

			Self::deposit_event(Event::<T>::StateMachineCostPerBlockUpdated {
				state_machine_id,
				cost_per_block,
			});

			Ok(())
		}
```
