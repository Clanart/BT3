## Analysis

The external report's core broken invariant: **a payout path silently drops a legitimately-owed amount when the paying contract can't currently cover it, with no on-chain bookkeeping and no later-claim mechanism** — the recipient just loses the reward permanently unless an admin manually intervenes.

The closest local analog is in `pallet-beefy-consensus-proofs`'s reward payment path for authority-set rotation proofs.

### Title
Rotation-proof rewards are silently dropped with no claim mechanism when the BEEFY-proofs treasury is underfunded - (File: `modules/pallets/beefy-consensus-proofs/src/lib.rs`)

### Summary
`Pallet::settle_first_proof` pays the BEEFY consensus-proof submitter via `pay_position_reward`, which does a `Currency::transfer` from the treasury. When that transfer fails (treasury balance too low), instead of reverting, the code explicitly swallows the error for rotation proofs, logs a warning, and reports `reward_paid = 0` — with no storage entry recording that the submitter is owed a reward. [1](#0-0) 

### Finding Description
`pay_position_reward` transfers `reward` from the treasury PalletId account to the submitter and returns `Error::RewardTransferFailed` on failure: [2](#0-1) 

In `settle_first_proof`, this error is deliberately caught and discarded specifically for rotation outcomes (`outcome.rotated == true`), with the comment justifying it as a design tradeoff: [1](#0-0) 

The event still fires with `rewarded: reward_paid` (zero), and the corrupted/lost value is the submitter's unpaid reward — there is no `StorageMap` tracking "submitter X is owed reward Y at height Z" and no extrinsic to reclaim it once the treasury is topped up. This exactly mirrors the `TokenSender.send()` pattern from the seed report: `if outputAmount > balance) return;` silently skips payment with no tracking. Here, `Err(e) if outcome.rotated => { ...; BalanceOf::<T>::default() }` silently skips payment with no tracking.

The pallet's own test explicitly documents and accepts this behavior: [3](#0-2) 

Note that a *messaging* proof (non-rotation) keeps the hard error and reverts, allowing the same work to be retried and rewarded later — but a *rotation* proof's state change is irreversible-in-practice (it's the only justification obtainable for that session), so the code chooses to keep the state change and drop the reward rather than roll back. Unlike the messaging-proof path, there is no analogous "retry the same commitment later for the same reward" possibility for the rotation submitter, since `AcceptedProvers`/`ProverCount` bookkeeping for that height is independently handled and the reward, once dropped, is unrecoverable by the honest submitter without governance intervention.

### Impact Explanation
An honest SP1/BEEFY prover who submits the valid, mandatory rotation justification (the only one obtainable for that session) permanently loses the earned reward if the treasury balance is insufficient at that exact moment — with no on-chain record of the debt and no self-service claim path once the treasury is refilled. This is a direct, uncompensated loss of funds to a legitimate actor performing exactly the expected on-chain action, matching the "stealing or loss of funds" impact class. Because rotation proofs are rare/critical events (session boundaries) and reward drops are silent (only a log line, not an on-chain event flagging non-payment distinctly from a successful zero-reward payout), affected provers have no way to detect or later prove they are owed a reward purely from chain state.

### Likelihood Explanation
This requires no malicious actor — it triggers whenever the treasury's balance is momentarily below `ProofReward` at the exact block a legitimate rotation proof lands (e.g., after a period of high reward payouts, an uncle-heavy epoch, or simple under-funding by governance). This is a normal operational condition rather than an attack requiring a compromised relayer/prover/admin, so it is a realistic protocol-fund-loss condition rather than a purely theoretical one.

### Recommendation
Add a storage map (e.g., `PendingRotationRewards: StorageMap<AccountId, BalanceOf<T>>`) that records the unpaid amount when `pay_position_reward` fails for a rotation proof, plus a permissionless extrinsic that lets the submitter claim the recorded amount once the treasury balance is sufficient, mirroring the mitigation from the seed report (track owed amount, allow later claim). This preserves the intended "don't block the rotation" behavior while eliminating the permanent, undocumented fund loss to the submitter.

### Proof of Concept
1. Governance sets `ProofReward = X` via `set_proof_reward`.
2. Treasury account balance drops below `X` (e.g., due to consecutive payouts or under-provisioning).
3. A valid SP1 rotation proof is submitted via `submit_proof`, verifies successfully in `verify_and_apply`, and `outcome.rotated == true`.
4. `settle_first_proof` calls `pay_position_reward`, whose `Currency::transfer` fails with `RewardTransferFailed`.
5. Because `outcome.rotated == true`, the error is caught at [4](#0-3) , `reward_paid` becomes zero, and the extrinsic still returns `Ok(...)` with `Pays::No`.
6. The rotation state change is persisted; `ProofAccepted { rewarded: 0 }` is emitted; the submitter's true earned reward is never recorded anywhere on-chain and is permanently unrecoverable absent manual governance action — reproduced deterministically by the existing test `an_unpayable_reward_cannot_block_a_rotation` at [5](#0-4) .

### Citations

**File:** modules/pallets/beefy-consensus-proofs/src/lib.rs (L563-581)
```rust
			// Same reasoning as the uncle bookkeeping above: the caller has already applied the
			// authority-set rotation, so a hard error here rolls it back, and since the mandatory
			// justification is the only one obtainable for that session every retry fails
			// identically until someone tops the treasury up — leaving the consensus state on the
			// old set in the meantime. A missed reward is the cheaper loss, so log and carry on.
			// Messaging proofs keep the hard error: reverting one is recoverable, because the work
			// is re-attempted by the next proof once the treasury can pay.
			let reward_paid = match Self::pay_position_reward(&submitter, 0) {
				Ok(reward) => reward,
				Err(e) if outcome.rotated => {
					log::warn!(
						target: "ismp",
						"[beefy-consensus-proofs] reward skipped for rotation to set {}: {e:?}",
						outcome.current_set_id,
					);
					BalanceOf::<T>::default()
				},
				Err(e) => Err(e)?,
			};
```

**File:** modules/pallets/beefy-consensus-proofs/src/lib.rs (L748-757)
```rust
			let treasury: T::AccountId =
				<T as Config>::TreasuryPalletId::get().into_account_truncating();
			<T as Config>::Currency::transfer(&treasury, submitter, reward, Preservation::Preserve)
				.map_err(|e| {
					log::warn!(
						target: "ismp",
						"[beefy-consensus-proofs] treasury reward transfer failed: {e:?}",
					);
					Error::<T>::RewardTransferFailed
				})?;
```

**File:** modules/pallets/testsuite/src/tests/pallet_beefy_consensus_proofs.rs (L188-217)
```rust
/// Same hazard, different source: an unpayable reward. `pay_position_reward` runs after the
/// caller has applied the rotation, so propagating `RewardTransferFailed` rolls it back — and
/// since the mandatory justification is the only one obtainable for that session, every retry
/// fails identically until the treasury is topped up, leaving consensus on the old set. The
/// reward is the cheaper thing to drop. Messaging proofs deliberately keep the hard error.
#[test]
fn an_unpayable_reward_cannot_block_a_rotation() {
	let mut ext = new_test_ext();
	let height = 800u64;

	ext.execute_with(|| {
		// The BEEFY proofs treasury holds nothing in genesis, so any non-zero reward makes the
		// transfer fail with `RewardTransferFailed`.
		ProofReward::<Test>::put(1_000_000u128);

		pallet_beefy_consensus_proofs::Pallet::<Test>::settle_first_proof(
			submitter(11),
			vec![PROOF_TYPE_SP1, 0xab],
			Some(H256::repeat_byte(11)),
			PROOF_TYPE_SP1,
			Vec::new(),
			rotation_outcome(height, 30),
		)
		.expect("an unpayable reward must not reject the rotation");

		assert_eq!(
			RotationProofs::<Test>::get().get(&30).copied(),
			Some(height),
			"the rotation must be recorded even though the reward could not be paid",
		);
```
