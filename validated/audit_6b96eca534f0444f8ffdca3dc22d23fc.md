### Title
Collator selection reads a live, non-pallet-enforced reputation-token balance at the session snapshot, enabling reputation-transfer sandwich attacks on block-reward eligibility - (File: `modules/pallets/collator-manager/src/lib.rs`)

### Summary
The reported bug class is: a transferable score that determines reward eligibility is snapshotted/rewarded at a discrete trigger, and the score can be moved into an account right before the trigger and safely removed/reused after — capturing rewards without genuine participation. `pallet-collator-manager::new_session` reproduces this exact pattern for Hyperbridge's collator selection: it ranks and selects the collator set purely by a live call to `T::ReputationAsset::balance(&controller_account)`, and the pallet's own `Config` trait does nothing to guarantee that balance is non-transferable — that guarantee is asserted only in documentation/README, not enforced in code.

### Finding Description
`new_session` builds the collator set from a live balance read: [1](#0-0) 

and the `Config` trait only requires the reputation asset to implement `fungible::Mutate` (balance/burn/mint), with no `CanTransfer`/freezer bound proving non-transferability at the pallet level: [2](#0-1) 

The documentation states the Reputation Asset is "non-transferable," but that property, if it exists at all, is enforced entirely by *runtime* configuration of the underlying `pallet-assets` instance (e.g. a freezer hook), which is outside this pallet and outside what `new_session` can verify. `new_session` trusts whatever balance sits in `T::ReputationAsset` at the exact session boundary — it has no mechanism to distinguish "reputation earned by this controller's own relaying/proving work" from "reputation just received by transfer."

This mirrors the `BunniToken` bug precisely: there, a transferable score moved with the token and could be acquired right before a reward-triggering call and disposed of right after. Here, if the reputation asset instance permits any transfer of the free (non-held) balance — which is the `pallet-assets` default absent an explicit freezer — an account that has honestly earned reputation can transfer it to a colluding/sybil controller account immediately before the deterministic `new_session` boundary. That colluding controller is then ranked and potentially selected into `new_set`, gaining eligibility for block-authoring rewards via `note_author`: [3](#0-2) 

After selection, the pallet unconditionally burns the selected accounts' full reputation balance back to zero: [4](#0-3) 

So the "cost" of the maneuver is only a transient balance parking around the snapshot instant — the same "acquire right before the trigger, discard right after" primitive from the source report — and it can be repeated across sessions by rotating reputation among a rotating set of sybil controllers, or by "renting" reputation to whichever colluding account needs a `new_session` boost that cycle.

### Impact Explanation
If the reputation asset's transferability is not airtight at the runtime layer, an attacker can buy/borrow collator selection and the associated `$BRIDGE` block-authoring reward stream (`CollatorReward`, transferred from treasury on every authored block) without having performed the underlying messaging-relay, consensus-relay, or BEEFY-proving work the reputation was supposed to certify. This is unauthorized diversion of protocol reward payouts to an account that did not earn them — a direct funds-diversion impact on the collator reward treasury, and it also lets an attacker place an arbitrary/unvetted account into the active block-producing/fisherman set, which has further liveness and fraud-detection implications documented for collators.

### Likelihood Explanation
Medium: the attack requires no privileged role, malicious peer, relayer, or admin — it only requires an account holding a real reputation balance (earned honestly through any of the three "reputation-generating" roles) and a colluding second account, both fully permissionless. The only unresolved variable is whether the deployed `pallet-assets` instance backing `ReputationAsset` actually blocks transfers at the runtime level; the collator-manager pallet code itself provides no such guarantee, so the invariant "reputation cannot move between accounts" is not locally provable from this pallet and depends entirely on external, unverified runtime wiring.

### Recommendation
- Enforce non-transferability inside `pallet-collator-manager`'s trait bounds rather than relying on runtime convention — e.g. require the configured asset to be held via `pallet-assets-holder` with holds that cannot be transferred, or add an explicit compile-time/runtime assertion that the configured `ReputationAsset` instance has transfers disabled (a `Freezer`/`CanTransfer` implementation returning `false` unconditionally).
- Alternatively, snapshot reputation at accrual time (already recorded per-`RelayerRewarded`/prover event) rather than reading a live, movable balance at the session boundary, so a last-block transfer cannot influence the `new_session` ranking.
- Add a regression test that attempts to transfer the configured reputation asset between two accounts and asserts failure, to catch any future runtime misconfiguration before it reaches production.

### Proof of Concept
1. Confirm (or misconfigure) the runtime's `ReputationAsset` instance without a freezer hook that blocks `transfer`/`transfer_approved` on that asset id (the collator-manager pallet does not check for or require one, per `Config` at `modules/pallets/collator-manager/src/lib.rs:79-81`).
2. Account A (an honest relayer with a large `ReputationAsset` balance) registers/pairs Controller B (a colluding low-reputation account) as detailed in `register`/`approve_controller`.
3. In the block immediately preceding the session boundary, A calls `pallet_assets::transfer(ReputationAssetId, B, A's_full_balance)`.
4. `new_session` fires, reads `T::ReputationAsset::balance(&B)` (now inflated), and ranks/selects B into `new_set` ahead of legitimately-earning controllers (`modules/pallets/collator-manager/src/lib.rs:514-542`).
5. B collates for the session and collects `CollatorReward` payouts via `note_author` (`modules/pallets/collator-manager/src/lib.rs:475-497`).
6. The pallet burns B's (transferred) reputation to zero at selection time (`modules/pallets/collator-manager/src/lib.rs:548-567`), but B has already banked the session's collator rewards it never earned, and A can repeat the transfer with fresh reputation next cycle or route the same reputation to a different sybil.

### Citations

**File:** modules/pallets/collator-manager/src/lib.rs (L79-81)
```rust
		/// The pallet-assets instance that manages the reputation token.
		type ReputationAsset: fungible::Mutate<Self::AccountId, Balance = <Self as pallet::Config>::Balance>;

```

**File:** modules/pallets/collator-manager/src/lib.rs (L475-497)
```rust
	impl<T: Config> pallet_authorship::EventHandler<T::AccountId, BlockNumberFor<T>> for Pallet<T> {
		fn note_author(author: T::AccountId) {
			let reward = CollatorReward::<T>::get();

			if reward > Zero::zero() {
				let treasury_account = T::TreasuryAccount::get().into_account_truncating();

				let result = T::NativeCurrency::transfer(
					&treasury_account,
					&author,
					reward,
					frame_support::traits::ExistenceRequirement::KeepAlive,
				);

				if result.is_ok() {
					Self::deposit_event(Event::CollatorRewarded {
						collator: author,
						amount: reward,
					});
				}
			}
		}
	}
```

**File:** modules/pallets/collator-manager/src/lib.rs (L514-529)
```rust
			let mut candidates = pallet_collator_selection::CandidateList::<T>::get()
				.into_iter()
				.map(|info| info.who)
				.filter(|stash_account| !Unbonding::<T>::contains_key(stash_account))
				.filter_map(|stash_account| Controller::<T>::get(&stash_account))
				.filter(|controller_account| {
					!RemovedValidators::<T>::contains_key(controller_account) &&
						pallet_session::NextKeys::<T>::get(controller_account.clone().into())
							.is_some()
				})
				.map(|controller_account| {
					(T::ReputationAsset::balance(&controller_account), controller_account)
				})
				.collect::<Vec<_>>();

			candidates.sort_by_key(|(balance, _)| *balance);
```

**File:** modules/pallets/collator-manager/src/lib.rs (L548-567)
```rust
			for account_id in &new_set {
				let balance = T::ReputationAsset::balance(account_id);
				if balance.is_zero() {
					continue;
				}
				let result = T::ReputationAsset::burn_from(
					account_id,
					balance,
					Preservation::Expendable,
					Precision::Exact,
					Fortitude::Polite,
				);

				if result.is_ok() {
					Self::deposit_event(Event::ReputationReset {
						who: account_id.clone(),
						amount: balance,
					});
				}
			}
```
