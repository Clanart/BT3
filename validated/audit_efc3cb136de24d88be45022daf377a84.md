## Analysis

The `SophonFarming` bug is a **retroactive re-pricing bug**: reward math applied a rate/allocation to an entire unclaimed accrual span using the *new* configuration instead of settling the span at the *old* configuration first. The direct Hyperbridge analog is in `pallet-consensus-incentives`, where the per-block relayer reward is computed lazily against an unbounded, watermarked block span and priced entirely at whatever `cost_per_block` happens to be configured at claim time. [1](#0-0) 

### Title
Retroactive Re-pricing of Unclaimed Consensus-Reward Block Spans Allows Treasury Drain — (`modules/pallets/consensus-incentives/src/impls.rs`)

### Summary
`calculate_reward` prices the *entire* unclaimed block span (`latest_height - LastRewardedHeight` watermark) using the `cost_per_block` value that is current **at claim time**, not the rate(s) that were actually in effect while those blocks accrued. `update_cost_per_block` performs no settlement of pending, already-elapsed spans before changing the rate — it is the exact structural analog of `SophonFarming.set()`/`add()` mutating pool weights without a mandatory `massUpdatePools()`.

### Finding Description
`StateMachinesCostPerBlock` can be updated at any time via `update_cost_per_block`, a privileged-but-ordinary governance call: [2](#0-1) 

The reward owed for a chain is only realized when a relayer submits a `ConsensusMessage`, which triggers `FeeHandler::on_executed` → `process_message` → `calculate_reward`: [3](#0-2) 

`calculate_reward` computes `blocks = latest_height - baseline` where `baseline` is `LastRewardedHeight` (a watermark that only advances on a **successful** reward transfer), then multiplies the *whole* span by the currently-configured `block_cost`: [4](#0-3) 

Critically, `LastRewardedHeight` only advances inside the success branch of `process_message` — if the treasury transfer fails (e.g., insufficient treasury balance, a normal operational condition, not an attacker-forced one) the watermark simply does not move, while `latest_commitment_height` on the `IsmpHost` continues to advance independently as later `ConsensusMessage`s are processed by the underlying consensus client. This lets an arbitrarily large unclaimed block span accumulate silently.

There is no per-span/per-rate checkpointing anywhere in the pallet: no snapshot of `block_cost` is taken against partial spans, and `update_cost_per_block` never calls anything analogous to `massUpdatePools()` to settle outstanding spans before the rate changes. This is precisely the bug class from the report: **allocation/rate changes are not preceded by settlement of already-accrued-but-unaccounted value**, so the entire backlog gets re-priced under the new rate.

### Impact Explanation
- If governance raises `cost_per_block` for a state machine while a large unclaimed block span (accrued under the old, lower rate) is outstanding, the **first relayer to submit the next `ConsensusMessage`** collects the entire backlog priced at the new, higher rate — an unbounded treasury overpayment, i.e., loss of funds for the protocol, extracted by an ordinary, unprivileged relayer performing a completely legitimate action (delivering a consensus proof). No malicious relayer, prover, or collusion is required — timing a normal submission after a routine rate change is enough.
- If governance lowers the rate, the inverse happens: an honest relayer's already-earned reward for blocks accrued under the old higher rate is unilaterally deflated to the new lower rate, permanently losing value with no recovery path (mirrors the `set()` case in the original report, where accrued rewards are silently nullified).
- Either direction is a direct "loss of funds" / incorrect-accounting outcome inside `TreasuryAccount::transfer`, matching the bounty's fund-loss and logic-attack criteria.

### Likelihood Explanation
`update_cost_per_block` is a routine governance operation expected to be called repeatedly as network costs change; the pallet places no operational requirement to first "flush" every state machine's outstanding span, and no test or code in `impls.rs`/`lib.rs` enforces one. A relayer only needs to observe a public rate-change event/extrinsic and time its next (otherwise ordinary) `ConsensusMessage` submission afterward — this is realistic and requires no privileged access, matching an "unprivileged attacker" path.

### Recommendation
Before applying a new `cost_per_block` in `update_cost_per_block`, force settlement of the outstanding span at the *old* rate — e.g., call an internal `settle_state_machine(state_machine_id)` that pays out (or explicitly checkpoints) `latest_commitment_height - LastRewardedHeight` at the currently-stored rate and advances the watermark, before writing the new rate into storage. This is the direct analog of making `massUpdatePools()` mandatory inside `add()`/`set()`.

### Proof of Concept
1. Governance sets `StateMachinesCostPerBlock[X] = 1` via `update_cost_per_block`.
2. For an extended period, no relayer submits a `ConsensusMessage` for `X` (or transfers keep failing due to a temporarily underfunded treasury), while the state machine's `latest_commitment_height` for `X` advances by `N` blocks through normal consensus-client processing; `LastRewardedHeight[X]` stays at the old watermark.
3. Governance raises the rate: `update_cost_per_block(X, 1000)`.
4. Any relayer submits the next valid `ConsensusMessage` for `X`. `calculate_reward` computes `reward = N * 1000` instead of the intended `N * 1` (or a mix of the two rates for the respective sub-spans), and `T::Currency::transfer` pays this entire inflated amount out of `TreasuryAccount` in one shot.
5. `LastRewardedHeight[X]` then advances to `latest_height`, permanently erasing any record that the span should have been priced under the old rate.

### Citations

**File:** modules/pallets/consensus-incentives/src/impls.rs (L41-75)
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

			T::Currency::transfer(
				&T::TreasuryAccount::get().into_account_truncating(),
				&relayer_account,
				reward,
				Preservation::Expendable,
			)
			.map_err(|_| Error::<T>::RewardTransferFailed)?;

			Self::deposit_event(Event::<T>::RelayerRewarded {
				relayer: relayer_account.clone(),
				amount: reward,
				state_machine_height,
			});

			T::ReputationAsset::mint_into(&relayer_account, reward.saturated_into())
				.map_err(|_| Error::<T>::ReputationMintFailed)?;

			LastRewardedHeight::<T>::mutate(state_machine_id, |watermark| {
				*watermark = Some(watermark.unwrap_or_default().max(state_machine_height.height));
			});
		}
		Ok(())
	}
```

**File:** modules/pallets/consensus-incentives/src/impls.rs (L78-99)
```rust
	fn calculate_reward(
		state_machine_id: &StateMachineId,
		block_cost: <T as pallet_ismp::Config>::Balance,
	) -> Result<<T as pallet_ismp::Config>::Balance, Error<T>> {
		let host = <T::IsmpHost>::default();
		let latest_height = host
			.latest_commitment_height(state_machine_id.clone())
			.map_err(|_| Error::<T>::CouldNotGetStateMachineHeight)?;
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

**File:** modules/pallets/consensus-incentives/src/lib.rs (L133-150)
```rust
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
