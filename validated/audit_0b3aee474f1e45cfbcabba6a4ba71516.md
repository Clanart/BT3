## Title
Relayer reward watermark not updated on partial failure causes duplicated/inflated treasury payouts — ([File: `modules/pallets/consensus-incentives/src/impls.rs`])

### Summary
`pallet-consensus-incentives::Pallet::process_message` transfers the relayer reward and updates `LastRewardedHeight` (the watermark that prevents re-paying for the same block span) in the wrong order, exactly mirroring the reported Voter.sol bug where `_updateFor` (the gating/accounting mutation) runs inconsistently relative to the fund movement. Here the fund transfer happens *before* the watermark is persisted, and a later, unrelated failure in the same function aborts before the watermark mutation runs — leaving the transfer (and its event) committed while the accounting state that should prevent re-crediting the same block range is left stale.

### Finding Description
`process_message` executes in this order:
1. `calculate_reward` — reward = `(latest_height - baseline) * block_cost`, where `baseline = LastRewardedHeight::get(...)`.
2. `T::Currency::transfer(treasury -> relayer, reward, ...)` — funds move immediately.
3. `deposit_event(RelayerRewarded { .. })`.
4. `T::ReputationAsset::mint_into(&relayer_account, reward)` — can fail (e.g. `BelowMinimum` from `pallet-assets` when the reward is smaller than the asset's minimum balance and the account has no existing deposit).
5. Only after all of the above: `LastRewardedHeight::<T>::mutate(...)` advances the watermark. [1](#0-0) 

Because step 4 can return `Err(Error::<T>::ReputationMintFailed)` via `?`, the function returns early **after the currency transfer at step 2 has already executed and the event at step 3 has already been deposited**, but **before** the watermark mutate at step 5 runs. Substrate does not automatically roll back prior storage/currency mutations inside a plain internal function on an `Err` return — only the outer dispatchable's transactional wrapper would do that, and here the caller explicitly swallows the error: [2](#0-1) 

`on_executed` always returns `Ok(...)` regardless of `process_message`'s result, so the extrinsic that invoked it commits successfully with the reward transfer intact and the watermark unadvanced.

On the *next* invocation of `on_executed` for the same `state_machine_id` (any subsequent consensus update, submitted by any relayer, privileged or not), `calculate_reward` reads the same stale `baseline` and computes a reward covering the full span from that stale baseline to the new `latest_height` — a span that includes blocks already paid out in the prior call. The relayer is paid again for blocks it (or another relayer) was already rewarded for, and this keeps compounding on every subsequent call for which mint_into again fails at a small enough reward, or even once mint succeeds later, that one payment alone still double-counts the already-paid span.

### Impact Explanation
This directly drains `T::TreasuryAccount` funds to relayer accounts in excess of the intended one-payment-per-block-span model, matching the bounty's "stealing or loss of funds" and "double-claim/double-settlement" impact categories. No malicious peer, prover, or admin is required — any relayer that delivers a consensus update whose computed reward happens to fall below the reputation asset's minimum balance (governance-configured `cost_per_block` combined with a small block span is a routine, unprivileged, naturally occurring condition) triggers the code path, and every future consensus delivery for that state machine will re-pay part or all of the already-rewarded span.

### Likelihood Explanation
The trigger condition — `T::ReputationAsset::mint_into` returning `Err` — is a standard `pallet-assets`/`fungible::Mutate` failure mode (`BelowMinimum`) that occurs whenever `reward.saturated_into()` is below the reputation asset's existential/minimum balance for an account with no prior holding. Given governance sets `block_cost` and relayers naturally deliver small, frequent consensus updates, this is easily reachable in normal operation without any privileged or adversarial party — it only requires an ordinary relayer submitting a normal, valid consensus proof.

### Recommendation
Reorder `process_message` so the watermark (`LastRewardedHeight`) is committed atomically with, and logically before, the irreversible currency transfer — or make the whole sequence transactional so a failure in the reputation mint rolls back the transfer as well. Concretely: compute the reward and update `LastRewardedHeight` first (or wrap steps 2–5 in `frame_support::storage::with_transaction`/`#[transactional]` so any failure reverts the transfer), and do not let a reputation-mint failure leave the currency payout committed while the anti-double-pay watermark stays stale.

### Proof of Concept
1. Governance sets a small `StateMachinesCostPerBlock` for a state machine, and/or the reward for a 1-block advance is naturally smaller than the reputation asset's minimum balance.
2. A relayer submits a valid consensus update advancing `latest_height` by a small span. `process_message` runs: `calculate_reward` returns a small non-zero reward, `T::Currency::transfer` succeeds crediting the relayer, `deposit_event` fires, then `T::ReputationAsset::mint_into` fails with `BelowMinimum` (relayer's reputation-asset account doesn't yet exist and the reward is below the minimum deposit). `process_message` returns `Err(ReputationMintFailed)`; `LastRewardedHeight` is never updated; `on_executed` still returns `Ok`, so the extrinsic commits with the transfer intact.
3. Any later consensus update for the same `state_machine_id` (from the same or a different relayer) calls `calculate_reward` again with the stale `baseline`, recomputing a reward that re-covers the block span already paid in step 2 — a second payout for blocks already compensated, draining the treasury for a span that was already settled. [3](#0-2)

### Citations

**File:** modules/pallets/consensus-incentives/src/impls.rs (L46-75)
```rust
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

**File:** modules/pallets/consensus-incentives/src/impls.rs (L78-100)
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
	}
```

**File:** modules/pallets/consensus-incentives/src/impls.rs (L147-156)
```rust
			for (state_machine_id, latest_height) in highest_per_state_machine {
				let state_machine_height =
					StateMachineHeight { id: state_machine_id.clone(), height: latest_height };

				let _ = Self::process_message(
					state_machine_height,
					state_machine_id,
					relayer_account.clone().into(),
				);
			}
```
