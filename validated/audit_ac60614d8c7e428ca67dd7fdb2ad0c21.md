### Title
Non-atomic reward payout in `Pallet::process_message` lets a failing `ReputationAsset::mint_into` call prevent the `LastRewardedHeight` watermark from advancing while `T::Currency::transfer` has already paid out, enabling repeated over-payment for the same block span - (File: `modules/pallets/consensus-incentives/src/impls.rs`)

### Summary
`process_message` performs the reward payout (`T::Currency::transfer`) before the fallible reputation mint (`T::ReputationAsset::mint_into`), and only advances the `LastRewardedHeight` watermark after the mint succeeds. If the mint fails, the function returns `Err`, the watermark update is skipped, but the currency transfer that already executed is **not** rolled back because the caller (`FeeHandler::on_executed`) discards the error with `let _ = Self::process_message(...)`.

### Finding Description
`process_message` in `modules/pallets/consensus-incentives/src/impls.rs` executes in this order: [1](#0-0) 

1. `T::Currency::transfer(...)` moves `reward` from the treasury to `relayer_account` — this succeeds and is committed.
2. `Self::deposit_event(...)` fires.
3. `T::ReputationAsset::mint_into(&relayer_account, ...)` — if this fails (e.g. the account is frozen/non-existent/below ED for the reputation asset), the function returns `Err(Error::ReputationMintFailed)` **before** reaching the `LastRewardedHeight::<T>::mutate(...)` call.

Because `FeeHandler::on_executed` calls this with `let _ = Self::process_message(...)`, the error is swallowed and the enclosing extrinsic still returns `Ok(...)`: [2](#0-1) 

There is no `#[transactional]` wrapper or explicit rollback around `process_message`, so the already-applied `Currency::transfer` persists in storage even though the function subsequently errors out. The watermark (`LastRewardedHeight`) is the only thing that prevents `calculate_reward` from re-paying the same block span: [3](#0-2) 

Since the watermark never advances when the mint fails, every subsequent call to `process_message` for that `state_machine_id` recomputes `blocks = latest_height - baseline` using the same stale `baseline`, re-paying (fully or overlappingly) currency that was already transferred in the prior call(s).

The `relayer_account` credited is derived from the signature embedded in the submitted `Message::Consensus` (`consensus_msg.signer`), which is attacker/submitter-controlled at message-submission time (an unprivileged action): [4](#0-3) 

An attacker who controls the account used as `relayer_account` can deliberately keep it in a state where `ReputationAsset::mint_into` always fails (frozen, insufficient existential deposit for that asset, etc.), turning this into a repeatable/inflatable currency drain: each further reward-triggering call re-pays the overlapping/full span since the stale watermark instead of only the newly earned span.

### Impact Explanation
This breaks reward accounting integrity: the treasury can be repeatedly drained for the same already-rewarded block span because the "one-time payment per span" invariant relies entirely on `LastRewardedHeight` advancing atomically with the payout, which this code does not guarantee. This matches the bounty's "broken timeout/refund/reward accounting" and "double-claim" impact categories.

### Likelihood Explanation
Reasonably likely: `mint_into` failures on a `fungible::Mutate` implementation (e.g. `pallet-assets`) are a normal, easily triggerable condition (freezing an account, letting balance for that asset go below ED, or account non-existence for that asset), and the relayer/account credited is chosen by whoever submits the consensus message, which is an unprivileged action in this flow.

### Recommendation
Make the payout atomic: only mutate/commit `T::Currency::transfer` after all fallible steps (including `mint_into`) succeed, or wrap the whole reward-granting sequence in a transactional block (e.g. `with_storage_layer`/`#[transactional]`) so that a mint failure rolls back the currency transfer too. Alternatively, advance `LastRewardedHeight` unconditionally (independent of the mint outcome) so the watermark cannot get "stuck" while funds have already moved, and handle/report the reputation-mint failure separately (e.g. emit an event, retry queue) without gating it on the transfer's already-committed state.

### Proof of Concept
1. Configure a mock `T::ReputationAsset::mint_into` to return `Err` for a specific `relayer_account` (e.g. simulate it being frozen).
2. Set `StateMachinesCostPerBlock` for a `state_machine_id`, and have the ISMP host report `latest_commitment_height` = H.
3. Submit a consensus message signed so that `relayer_account` is the attacker-controlled frozen account; `on_executed` calls `process_message`, which:
   - Transfers `reward` from treasury to `relayer_account` (succeeds).
   - Emits `RelayerRewarded`.
   - Calls `mint_into`, which fails; `process_message` returns `Err`, `LastRewardedHeight` is **not** updated.
4. Submit again (or trigger another `on_executed` call) for the same `state_machine_id`/height H. `calculate_reward` recomputes `blocks = H - baseline` using the same unadvanced `baseline`, and `T::Currency::transfer` pays the treasury funds again for the same span.
5. Assert: treasury balance decreased twice for the identical block span, while `LastRewardedHeight` never advanced.

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
