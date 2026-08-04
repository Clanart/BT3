## Finding

### Title
Relayer reward watermark is never persisted when reputation-asset minting fails, enabling repeated duplicate treasury payouts for the same block span - (File: `modules/pallets/consensus-incentives/src/impls.rs`)

### Summary
`pallet-consensus-incentives::process_message` transfers a token reward to the relayer *before* it persists the `LastRewardedHeight` watermark that gates future reward calculations. If the subsequent `T::ReputationAsset::mint_into` call fails, the function returns early and the watermark update is skipped, but the treasury transfer that already executed is **not rolled back**. The caller silently discards the error (`let _ = Self::process_message(...)`), so the overall extrinsic still returns `Ok`, meaning no transactional storage rollback occurs. The same block span can then be paid again on the next consensus message delivery, indefinitely.

### Finding Description
`calculate_reward` computes the reward using `baseline = LastRewardedHeight::<T>::get(state_machine_id).unwrap_or(previous_height)` and `blocks = latest_height - baseline`: [1](#0-0) 

`process_message` then performs, in order: currency transfer → event deposit → reputation mint → watermark update: [2](#0-1) 

If `T::ReputationAsset::mint_into` returns an error (e.g. `BelowMinimum` when a small reward is below the asset's existential/minimum balance, which is common for first-time relayer accounts or short block spans), `process_message` returns `Err(Error::<T>::ReputationMintFailed)` immediately after `?`, **before** the `LastRewardedHeight::<T>::mutate(...)` call executes. The `Currency::transfer` call above it, however, has already mutated the balances storage.

The caller in `on_executed` swallows this error: [3](#0-2) 

Actually verified at: [4](#0-3) 

Because `on_executed` still returns `Ok(PostDispatchInfo { .. })`, the outer dispatchable (`pallet-ismp`'s consensus message handler) succeeds, so FRAME's transactional dispatch wrapper does not revert any storage writes performed earlier in the call, including the already-completed treasury transfer. `LastRewardedHeight` is left unchanged, so on the next `StateMachineUpdated` event for the same state machine (submitted via a subsequent, ordinary consensus-message delivery), `calculate_reward` recomputes the reward for the **same** (or overlapping) `baseline..latest_height` span and pays it out again.

### Impact Explanation
This is a direct loss-of-funds bug against `T::TreasuryAccount`: an unprivileged relayer can repeatedly trigger the mint failure (e.g. by submitting consensus updates whose per-block reward stays below the `ReputationAsset`'s minimum balance) and collect duplicate treasury payouts for the same already-rewarded block span on every subsequent submission, since the watermark that is supposed to prevent double payment never advances. This matches the required impact class of double-claim/double-settlement and unauthorized transfer of protocol funds.

### Likelihood Explanation
No privileged actor, malicious peer, or compromised relayer/prover is required — any account capable of submitting a valid `ConsensusMessage` (the normal, permissionless relayer flow) can trigger this deterministically by causing rewards that fall below the reputation asset's minimum balance (a very plausible, easily reachable condition for small block spans / low `CostPerBlock` configurations, or simply a fresh relayer account before an ED-bearing balance exists). Once triggered once, the condition self-perpetuates because the watermark never advances, so every future delivery for that chain replays the same reward.

### Recommendation
Persist `LastRewardedHeight` **before** performing side-effecting transfers/mints (mirroring the sponsor's own fix pattern of updating state ahead of external effects), or make the whole reward flow atomic: if `mint_into` fails, either roll back the `Currency::transfer` explicitly, or update the watermark unconditionally regardless of the mint outcome (treating minting reputation as best-effort, non-blocking), so a mint failure never re-opens an already-paid block span.

### Proof of Concept
1. Configure `StateMachinesCostPerBlock` for a state machine with a low `cost_per_block` such that `reward = blocks * cost_per_block` is smaller than `ReputationAsset`'s minimum balance for a fresh account.
2. Submit a valid signed `ConsensusMessage` advancing `latest_height` by a small span. `process_message` runs: `Currency::transfer` succeeds (moving `reward` from treasury to relayer), `RelayerRewarded` event fires, then `ReputationAsset::mint_into` fails with `BelowMinimum` → `process_message` returns `Err`, `LastRewardedHeight` is **not** updated.
3. Submit another valid `ConsensusMessage` for the same state machine (even without further height advance, or with the same/overlapping span). `calculate_reward` reads the stale `baseline` (unchanged from step 2) and recomputes/re-pays the same reward.
4. Repeat step 3 indefinitely to continually drain `T::TreasuryAccount` for the same, already-"rewarded" block span.

### Citations

**File:** modules/pallets/consensus-incentives/src/impls.rs (L53-72)
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

**File:** modules/pallets/consensus-incentives/src/impls.rs (L151-155)
```rust
				let _ = Self::process_message(
					state_machine_height,
					state_machine_id,
					relayer_account.clone().into(),
				);
```
