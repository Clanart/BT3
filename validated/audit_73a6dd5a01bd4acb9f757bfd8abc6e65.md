### Title
Stale-rate reward calculation in `pallet-consensus-incentives` lets relayers collect a governance rate hike retroactively over an unclaimed block backlog - ([File: modules/pallets/consensus-incentives/src/impls.rs])

### Summary
`calculate_reward` in `pallet-consensus-incentives` prices an entire span of unrewarded blocks (`latest_height - baseline`) using a single, current `StateMachinesCostPerBlock` rate, exactly the bug-class in the external report: a "batch quantity" is charged/paid at one rate without accounting for a rate change that occurred partway through the span it covers. Where the external report has a buyer under-pay for vaults that cross a price-increase threshold, here a relayer can be over-paid by the treasury for blocks that occurred before a rate increase, because the reward formula has no per-height rate history — only the current scalar `block_cost` is ever read.

### Finding Description
The reward is computed as: [1](#0-0) 

`baseline` is the relayer's last-rewarded watermark for the state machine, `latest_height` is the newest committed height, and `block_cost` is read once, at claim time, directly from current storage: [2](#0-1) 

`StateMachinesCostPerBlock` is a simple, immediately-effective governance value with no timelock or per-height history: [3](#0-2) 

There is no mechanism that splits the `[baseline, latest_height]` span across a rate change boundary and prices each sub-range at the rate that was actually in effect during it. Whatever `block_cost` is in storage at the moment `on_executed` fires is applied to the *entire* accumulated span — including blocks that accrued while a different (lower) rate was active. This is structurally identical to `NodeSale.sol`'s flaw: `numberOfVaults * getPricePerVault()` used a single current price for a quantity that spans a price-increase threshold.

Anyone can be the relayer credited here — relayer identity is derived purely from recovering the sr25519 signer off the consensus proof, with no permission or allowlist check: [4](#0-3) 

so this does not require a "compromised relayer" or privileged actor to execute the harvesting step — any party capable of submitting a valid consensus update for the state machine can trigger `process_message`/`calculate_reward` and capture whatever `block_cost` is live at that instant, for however large a backlog has accumulated since the last claim.

### Impact Explanation
If governance raises `StateMachinesCostPerBlock` for a state machine (a routine, expected operational action — not an exotic compromise), any outstanding unclaimed block backlog for that chain is paid out entirely at the new, higher rate the moment the next consensus update lands, even though most of that backlog accrued under the old, lower rate. The treasury (`T::TreasuryAccount`) pays out more BRIDGE/native currency than the pre-hike economics intended for those historical blocks — a direct, uncompensated fund loss from the treasury to whichever party happens to submit the next update. Because `LastRewardedHeight` only tracks a height watermark and never a rate history, this loss is baked into the formula and reproducible every time a rate change is followed by a backlog claim; it is not a one-off rounding error.

### Likelihood Explanation
The relayer role is permissionless (identity is recovered from the message signature only, not checked against any allowlist), and `update_cost_per_block` takes effect immediately with no delay, so any relayer that watches chain state can simply hold back submitting a pending, already-verifiable consensus update until after they observe (or predict) a rate increase, then submit — collecting the hiked rate over the full backlog in one shot. No malicious peer, prover compromise, or governance collusion is required; the attacker only needs normal, permitted relayer capability and ordinary chain observation, which makes this readily exploitable whenever rate changes and backlogs coincide (e.g., after periods of relayer inactivity for a given state machine).

### Recommendation
Do not price the full `[baseline, latest_height]` span with a single "current" `block_cost`. Instead:
- Record the rate (and the height at which it became effective) whenever `update_cost_per_block` is called, keeping a small history/checkpoint list per `state_machine_id`.
- In `calculate_reward`, split `[baseline, latest_height]` into sub-ranges bounded by rate-change checkpoints, and sum `sub_range_blocks * rate_effective_during_sub_range` — mirroring the report's recommendation to create a dedicated calculation path that accounts for threshold/rate crossings rather than applying one rate to the whole batch.
- Alternatively, force relayers to claim promptly (e.g., cap the maximum unclaimed height span, or auto-checkpoint `LastRewardedHeight` whenever `update_cost_per_block` fires) so that a rate change can never retroactively apply to a large pre-existing backlog.

### Proof of Concept
Conceptual sequence (Rust/pallet-level, illustrating the exploitable state transition — no live test harness was available in this session to execute it, but the code path is fully shown above):

1. Governance sets `StateMachinesCostPerBlock[SM] = 1` (low rate) via `update_cost_per_block`.
2. State machine `SM` advances from height 100 to height 100,000 over time, but no relayer submits a consensus update claiming the reward — `LastRewardedHeight[SM]` stays at 100 (or unset, falling back to `previous_commitment_height`).
3. Governance raises the rate: `update_cost_per_block(SM, 1000)` (rate hike takes effect immediately, no timelock).
4. Any relayer (no special privilege required — identity is recovered purely from the sr25519 signature on the submitted `ConsensusMessage`) submits a valid consensus update for `SM` reaching height 100,000.
5. `on_executed` → `process_message` → `calculate_reward` computes:
   - `baseline = 100`
   - `latest_height = 100,000`
   - `blocks = 99,900`
   - `block_cost = 1000` (the just-hiked rate, read fresh from storage)
   - `reward = 99,900 * 1000 = 99,900,000`, transferred from `T::TreasuryAccount` to the relayer — even though 99,900 of those blocks accrued while the rate was `1`, meaning the "correct" reward under the old rate for most of that span would have been `99,900 * 1 = 99,900`.
6. The relayer collects ~1000x more than the amount the pre-hike rate would have paid for the same delivered work, funded entirely by the treasury, purely by choosing to delay their claim until after the rate change — no compromised relayer, prover, or governance collusion needed.

### Citations

**File:** modules/pallets/consensus-incentives/src/impls.rs (L41-51)
```rust
	fn process_message(
		state_machine_height: StateMachineHeight,
		state_machine_id: StateMachineId,
		relayer_account: T::AccountId,
	) -> Result<(), Error<T>> {
		if let Some(block_cost) = StateMachinesCostPerBlock::<T>::get(state_machine_id) {
			let reward = Self::calculate_reward(&state_machine_id, block_cost)?;

			if reward.is_zero() {
				return Ok(());
			}
```

**File:** modules/pallets/consensus-incentives/src/impls.rs (L86-99)
```rust
		let previous_height =
			host.previous_commitment_height(state_machine_id.clone()).unwrap_or_default();

		// Use the rewarded watermark as the baseline and fall back to the previous height until
		// the first reward is recorded for this chain. The watermark only moves forward, so a
		// height that is rolled back and later resubmitted is not paid for a second time.
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

**File:** modules/pallets/consensus-incentives/src/lib.rs (L130-150)
```rust
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
