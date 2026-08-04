## Title
Reward transfer succeeds but reputation-mint failure silently reverts only the watermark update, enabling repeated (double) treasury payouts for the same consensus block span - (File: `modules/pallets/consensus-incentives/src/impls.rs`)

## Summary
`pallet-consensus-incentives::process_message` transfers the BRIDGE reward from the treasury to the relayer **before** minting the reputation asset and **before** advancing the `LastRewardedHeight` watermark that prevents double-payment. If the reputation mint step fails for any non-malicious, ordinary reason (e.g. the relayer account has never held the reputation asset and the computed reward is below the asset's `min_balance`, or any other `mint_into` failure), the function returns `Err(ReputationMintFailed)` after the funds have already moved, but the caller (`on_executed`) discards that error via `let _ = Self::process_message(...)`. Because the watermark update happens only after the mint call, it never runs, so the next consensus update recomputes the reward for the exact same (already-paid) block span and pays it out again from the treasury.

## Finding Description
`calculate_reward` derives the reward as `(latest_height - baseline) * cost_per_block`, where `baseline` is `LastRewardedHeight` (falling back to `previous_commitment_height`): [1](#0-0) 

`process_message` executes the payout side-effects in this order: transfer funds → emit event → mint reputation → **only then** advance `LastRewardedHeight`: [2](#0-1) 

`on_executed` calls `process_message` and explicitly swallows any error it returns: [3](#0-2) 

If `T::ReputationAsset::mint_into` fails — a routine, non-malicious outcome for `pallet-assets`-backed `Mutate` implementations when a brand-new holder's minted amount is below the asset's configured `min_balance`, or for any other transient mint failure — `process_message` returns `Err(ReputationMintFailed)` at line 68. Because the currency transfer at lines 53-59 already executed and committed, the relayer has already received the BRIDGE reward. The subsequent `LastRewardedHeight::<T>::mutate` at lines 70-72 is skipped since the function short-circuited via `?` before reaching it. `on_executed`'s `let _ = ...` means this failure is invisible to the caller and does not revert the earlier storage mutation (there is no transactional wrapper around `process_message`'s internal steps), so the treasury debit and event emission stand.

On the next `StateMachineUpdated` event for the same `state_machine_id`, `calculate_reward` reads the stale `baseline` (still the pre-payout watermark) and recomputes a reward covering the same block span that was already paid, transferring the treasury funds a second time for identical blocks. This repeats every time the mint step fails, effectively decoupling "was this span already paid" from "was this span recorded as paid."

This is a direct structural analog to the reported bug class: an accounting increment (the reward/debt) is applied unconditionally, while the guard that should gate repeat increments (the watermark advance, analogous to the `payOffWeeks` "only increase if there's enough to pay the following week" fix) is placed *after* a step that can independently fail — so a partial/failed step causes the same value to be paid out more than once.

## Impact Explanation
This is a double-settlement / duplicate-payout vulnerability against the on-chain treasury (`TreasuryAccount`), matching the bounty's explicit "replay/double-claim/double-settlement" and "stealing or loss of funds" categories. Any relayer whose account triggers a reputation-mint failure (which can occur under ordinary configuration, e.g. minimum-balance requirements on the reputation asset for a first-time holder, or any other legitimate `mint_into` error) causes the treasury to pay for the same block span repeatedly on every subsequent consensus submission, draining treasury funds without a corresponding increase in real "progress" being rewarded. No relayer or prover corruption is required — the trigger is a normal state (a fresh account below the reputation asset's minimum balance) hit through the standard, permissionless consensus-message submission path.

## Likelihood Explanation
Likelihood is credible but not high: it requires the reputation-mint step to fail while the currency transfer preceding it succeeds, which depends on the specific `ReputationAsset` (`pallet-assets`) configuration (e.g., `min_balance`) and the relayer account's prior holdings. This is a normal operational condition (first-time relayer, small `cost_per_block` yielding a reward below `min_balance`) rather than a contrived edge case, and it is fully reachable by any unprivileged relayer submitting a valid consensus message — no privileged actor, malicious peer, or leaked key is needed.

## Recommendation
Reorder the side effects so the watermark advances (or the whole operation is atomic) regardless of reputation-mint outcome, or make reputation-mint failures non-fatal to the payout bookkeeping:
- Advance `LastRewardedHeight` before or atomically with the funds transfer (using `frame_support::storage::with_transaction`, or updating the watermark first and rolling back everything together on any failure), so a downstream mint failure cannot leave the reward span unmarked as paid.
- Alternatively, treat `ReputationAsset::mint_into` failure as non-critical: log it and continue to update `LastRewardedHeight`, since minting is a secondary reputation record and should not gate the primary "already paid" bookkeeping.
- Add a regression test that fails the reputation mint (e.g., insufficient min balance) and asserts the treasury is not charged twice for the same block span on a subsequent `on_executed` call.

## Proof of Concept
1. Configure `StateMachinesCostPerBlock` for a state machine and configure `ReputationAsset` (pallet-assets) with a `min_balance` greater than the reward that a small block-span payout would mint for a brand-new holder account.
2. Submit a valid signed `ConsensusMessage` advancing the state machine from height H0 to H1, from a relayer account that has never held the reputation asset. `process_message` runs: `T::Currency::transfer` succeeds (treasury pays reward for span H0→H1), `deposit_event` fires, but `T::ReputationAsset::mint_into` fails because minted amount < `min_balance` for a new account → returns `Err(ReputationMintFailed)` at [4](#0-3) . `LastRewardedHeight` is never updated.
3. `on_executed`'s `let _ = Self::process_message(...)` discards the error; the call returns `Ok`, and the extrinsic (and its storage mutations, including the treasury debit) is not rolled back.
4. Submit a second valid `ConsensusMessage` advancing the same state machine to H2 (or resubmit the same span in a subsequent block). `calculate_reward` reads `baseline = LastRewardedHeight::get(state_machine_id).unwrap_or(previous_height)`, which is still the pre-H1 baseline because it was never advanced — so the reward computed again covers (at least) the H0→H1 span, which the treasury already paid in step 2, resulting in a second payout for the same blocks.

### Citations

**File:** modules/pallets/consensus-incentives/src/impls.rs (L53-73)
```rust
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
