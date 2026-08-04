### Title
Non-atomic relayer reward payout allows repeated treasury drains for the same block span when `ReputationAsset::mint_into` fails - (File: `modules/pallets/consensus-incentives/src/impls.rs`)

### Summary
`Pallet::process_message` performs the treasury→relayer `T::Currency::transfer` **before** updating the `LastRewardedHeight` watermark, and the mint of the reputation token happens in between. If `T::ReputationAsset::mint_into` fails, `process_message` returns `Err(ReputationMintFailed)` *after* the balance transfer has already been executed and *before* `LastRewardedHeight::<T>::mutate` runs, so the watermark is never advanced even though funds already moved.

### Finding Description
In `process_message`: [1](#0-0) 

the sequence is:
1. `T::Currency::transfer(treasury → relayer, reward)` — executes immediately and is not part of a transactional boundary that would be rolled back on later failure.
2. `Self::deposit_event(RelayerRewarded)`.
3. `T::ReputationAsset::mint_into(&relayer_account, reward)` — if this fails, the function returns `Err(Error::ReputationMintFailed)` immediately.
4. `LastRewardedHeight::<T>::mutate(...)` — only reached if step 3 succeeds.

Critically, the caller in `FeeHandler::on_executed` discards this error: [2](#0-1) 
and the enclosing extrinsic (`on_executed`) always returns `Ok(...)`: [3](#0-2) 

Because the top-level dispatchable ultimately succeeds, FRAME's transactional storage layer does **not** roll back the already-applied `T::Currency::transfer`. The only state that fails to persist is the `LastRewardedHeight` watermark update, since it's the last statement and never executes.

`calculate_reward` uses `LastRewardedHeight` as the baseline for the next reward computation: [4](#0-3) 

If the watermark never advances past a given height while `latest_commitment_height`/`previous_commitment_height` remain valid (e.g., via a resubmittable/idempotent consensus update path, or simply because the same `StateMachineUpdated` event for that height keeps reappearing in subsequent batches), every subsequent call recomputes the same non-zero reward for the same block span and repeats the treasury transfer — as long as `mint_into` keeps failing for that relayer account.

`mint_into` can fail deterministically for an unprivileged actor's own account, for example: pallet-assets' `Mutate::mint_into` returns an error if the mint would overflow the asset's `Balance` type or exceed the asset's supply cap. An attacker who deliberately drives up their own reputation-asset balance (accumulated over many prior legitimate rewards, since amounts are cumulative and unbounded) toward `Balance::MAX`/the configured cap can reliably make every future `mint_into` call for that same account fail, unlocking unlimited repeat payouts for the same already-rewarded height span.

### Impact Explanation
This breaks the "reward is paid exactly once per block span" invariant. An attacker (as relayer) can repeatedly trigger `process_message`/`on_executed` for the same `state_machine_id`/height (resubmitted or duplicate consensus updates), draining `T::TreasuryAccount`'s balance to their own account on every call, with `LastRewardedHeight` frozen and `calculate_reward` recomputing the identical non-zero reward each time. This is unauthorized/duplicate settlement of treasury funds to the relayer, matching the "duplicate settlement" and "loss of funds" impact categories.

### Likelihood Explanation
Requires the attacker to control a relayer account that submits (or causes resubmission of) consensus messages, and to have engineered their `ReputationAsset` balance to a value that makes `mint_into` deterministically fail (e.g., near `Balance::MAX` or an asset supply cap) — achievable purely through normal participation as a relayer over time, with no privileged access needed. The exploitation trigger itself (repeated/resubmitted consensus updates for the same height) is a normal, permissionless operation.

### Recommendation
Make the payout atomic: either wrap the transfer + mint + watermark update in a single `frame_support::storage::with_transactional` block that rolls back the currency transfer if minting fails, or reorder operations so `LastRewardedHeight` is updated (and the mint attempted) **before** performing the currency transfer, treating mint failure as non-fatal to the watermark (e.g., log/skip minting reputation but still record the watermark and pay only once), or use `#[transactional]` on `process_message` and propagate its error out of `on_executed` instead of silently discarding it with `let _ =`.

### Proof of Concept
1. Configure a mock `ReputationAsset::mint_into` implementation that returns `Err` for a specific relayer account (e.g., simulate hitting a supply cap/overflow).
2. Set `StateMachinesCostPerBlock` for a `state_machine_id`.
3. Call `Pallet::process_message` (or drive it via `on_executed`) with a given `state_machine_height`/`state_machine_id` — observe: `T::Currency::transfer` succeeds and debits the treasury, `RelayerRewarded` event fires, but `mint_into` fails and the function returns `Err(ReputationMintFailed)`; `LastRewardedHeight` is not updated.
4. Call `process_message` again with the same `state_machine_height`/`state_machine_id` — since `LastRewardedHeight` baseline is unchanged, `calculate_reward` recomputes the same non-zero reward, and the treasury is debited a second time.
5. Assert the treasury balance was debited more than once for the same block span, violating the one-time-payment invariant.

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

**File:** modules/pallets/consensus-incentives/src/impls.rs (L151-156)
```rust
				let _ = Self::process_message(
					state_machine_height,
					state_machine_id,
					relayer_account.clone().into(),
				);
			}
```

**File:** modules/pallets/consensus-incentives/src/impls.rs (L159-163)
```rust
		// Return with actual weight information
		// We use Pays::No to indicate that someone (the message sender) doesn't pay for this
		// operation, though we're using this mechanism to reward relayers rather than charge fees
		Ok(PostDispatchInfo { actual_weight: None, pays_fee: Pays::No })
	}
```
