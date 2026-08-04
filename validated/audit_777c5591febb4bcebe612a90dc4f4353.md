Confirmed: `OutboundConsensusDeliveryReward` and `OutboundRequestDeliveryReward` are both read at claim time via `OutboundConsensusDeliveryReward::<T>::get(destination)` [1](#0-0)  and `OutboundRequestDeliveryReward::<T>::get(&module_id)` [2](#0-1) , with no epoch/snapshot binding the reward to the block height at which the underlying delivery event (the rotation or the request receipt) actually occurred.

### Title
Reward-rate read at claim time (not delivery time) lets relayers be under/over-paid for already-completed work - (File: `modules/pallets/relayer/src/outbound_consensus.rs`, `modules/pallets/relayer/src/outbound_request.rs`)

### Summary
Both outbound-delivery reward claims price the relayer's completed work using the *current* value of a governance-settable reward-rate storage item, not the rate that was in effect when the relayer actually delivered the consensus rotation or request. This is the same broken invariant as the `NFTStaking` bug: a mutable global rate parameter is applied retroactively to work performed under a different rate, with no epoching, checkpointing, or rate-lock-in at the time the qualifying on-chain event happened.

### Finding Description
Delivery work is proven completely independently of reward pricing:
- For `OutboundConsensusDeliveryClaim`, the actual "delivery" event is `HandlerV2.handleConsensus` writing the relayer's address into `EvmHost._epochs[set_id]` on the destination chain — this can happen at any past block [3](#0-2) .
- For `OutboundRequestDeliveryClaim`, the delivery event is the destination host writing `RequestReceipts[commitment]` — again, at any past block [4](#0-3) .

The claim extrinsics are unsigned and can be submitted by anyone holding the relevant delivery proof/signature at any later time — there is no deadline binding the claim to a reward rate that existed near the delivery block. When `process_outbound_consensus_delivery_claim` / `process_outbound_request_delivery_claim` finally run, they fetch `OutboundConsensusDeliveryReward::<T>::get(destination)` [5](#0-4)  or `OutboundRequestDeliveryReward::<T>::get(&module_id)` [6](#0-5)  — the *current* storage value, not a value snapshotted at proof-verified height. Governance can freely change these rates at any time via `set_outbound_consensus_delivery_reward` and `set_outbound_request_delivery_reward` [7](#0-6) .

This mirrors the report's core defect exactly: "Rewards are computed at claim time using current values without epoching or checkpointing," creating unfair/inconsistent treatment between relayers who deliver identical work at the same time but claim at different times, or between deliveries that straddle a rate change.

### Impact Explanation
Because the idempotency guards (`OutboundConsensusRotationsClaimed`, `OutboundRequestsClaimed`) only prevent double-claims, not stale-rate claims, the amount paid out of the treasury for a fixed, already-completed unit of work is fully at the mercy of whatever the reward rate happens to be at claim submission time [8](#0-7) [9](#0-8) . If governance raises a reward mid-flight, any relayer who delivered under the old (lower) rate but delays submitting the claim collects the higher payout — an unearned treasury drain relative to relayers who claimed promptly for the same delivered set_id/commitment class. Conversely a rate cut punishes relayers who happened to claim late for work done earlier, and a relayer could deliberately delay a claim while lobbying/anticipating a rate increase to extract more from the treasury than the work was priced at when performed. This is a treasury fund-movement correctness issue: the exact same "deliver rotation/request" action can yield different payouts purely based on claim timing, which is a mispriced/unauthorized-amount transfer out of `TreasuryPalletId`.

### Likelihood Explanation
Both claim extrinsics are unsigned, public entry points (`ensure_none(origin)`) [10](#0-9) [11](#0-10)  that any relayer can hold onto and submit whenever it is most advantageous, with no deadline forcing prompt submission. Reward-rate updates are a normal, expected governance operation (documented as routine tuning, not a malicious act) — see `set_outbound_consensus_delivery_reward`/`set_outbound_request_delivery_reward` [7](#0-6)  — so the mispricing window opens every time economics are adjusted, which is realistically a recurring operational event, not a rare edge case.

### Recommendation
Snapshot the reward rate at the block/height the qualifying delivery event is proven to have occurred (e.g., record `reward_at_epoch` when `EvmHost.recordEpoch`/`RequestReceipts` was written, or checkpoint the current reward into the claim record at first-sight time), and pay out using that locked-in rate rather than `OutboundConsensusDeliveryReward::get`/`OutboundRequestDeliveryReward::get` read live at claim time. Alternatively, require claims to be submitted within a bounded window of the delivery height so at most one rate epoch can apply, or version the reward maps by effective-height and pick the rate whose epoch covers the delivery height.

### Proof of Concept
1. Governance sets `OutboundConsensusDeliveryReward(EvmDestX) = 100` via `set_outbound_consensus_delivery_reward`.
2. RelayerA delivers a rotation (`set_id = 5`) to `EvmDestX`, recorded on-chain in `EvmHost._epochs[5]`.
3. RelayerA delays submitting `claim_outbound_consensus_delivery_reward`.
4. Governance later raises the reward: `OutboundConsensusDeliveryReward(EvmDestX) = 1000` (a routine economic adjustment, not malicious).
5. RelayerA now submits the claim for the `set_id = 5` delivery that happened under the old 100-unit rate; `process_outbound_consensus_delivery_claim` reads the *current* value (1000) at line 174 and pays RelayerA 1000 instead of the 100 that was in effect when the work was actually done [12](#0-11) .
6. A second relayer who did equivalent work (different `set_id`) but claimed promptly under the old rate only received 100 — same class of work, 10x price difference purely due to claim timing. The identical structure applies to `OutboundRequestDeliveryReward` and `process_outbound_request_delivery_claim`.

### Citations

**File:** modules/pallets/relayer/src/outbound_consensus.rs (L16-25)
```rust
//! Outbound consensus delivery rewards.
//!
//! Relayers that deliver mandatory (authority-set rotation) consensus proofs
//! to an EVM destination earn a per-chain `OutboundConsensusDeliveryReward`.
//! The on-chain attribution lives in the destination's
//! `EvmHost._epochs[set_id]` slot, populated by `HandlerV2.handleConsensus`
//! via `EvmHost.recordEpoch(set_id, msg.sender)` the first time a consensus
//! proof brings the new set id on chain. This module proves the slot value
//! against Hyperbridge's stored state commitment for the destination and
//! transfers the configured reward.
```

**File:** modules/pallets/relayer/src/outbound_consensus.rs (L126-129)
```rust
		ensure!(
			!OutboundConsensusRotationsClaimed::<T>::contains_key(destination, set_id),
			Error::<T>::OutboundRotationAlreadyClaimed,
		);
```

**File:** modules/pallets/relayer/src/outbound_consensus.rs (L174-186)
```rust
		let reward = OutboundConsensusDeliveryReward::<T>::get(destination);
		ensure!(reward > BalanceOf::<T>::default(), Error::<T>::OutboundNoRewardConfigured);

		let treasury: T::AccountId =
			<T as Config>::TreasuryPalletId::get().into_account_truncating();
		let payee_account: T::AccountId = payee.into();
		<<T as pallet_ismp::Config>::Currency as Mutate<T::AccountId>>::transfer(
			&treasury,
			&payee_account,
			reward,
			Preservation::Preserve,
		)
		.map_err(|_| Error::<T>::OutboundRewardTransferFailed)?;
```

**File:** modules/pallets/relayer/src/outbound_request.rs (L16-29)
```rust
//! Outbound request delivery rewards.
//!
//! Relayers that deliver a hyperbridge-originated request (host-executive,
//! intents-coprocessor, token-governor, the relayer pallet's own withdrawal
//! path, etc.) to a destination earn the per-`module_id`
//! [`crate::pallet::OutboundRequestDeliveryReward`]. The on-chain attribution
//! lives in the destination's `RequestReceipts[commitment]` slot, written by
//! the destination's ISMP host the first time the request is delivered. This
//! module proves that slot against Hyperbridge's stored state commitment for
//! the destination and transfers the configured reward.
//!
//! Unlike [`crate::outbound_consensus`], this claim supports both EVM and
//! substrate destinations: the receipt key and relayer decoding branch on the
//! destination state machine type.
```

**File:** modules/pallets/relayer/src/outbound_request.rs (L138-141)
```rust
		ensure!(
			!OutboundRequestsClaimed::<T>::contains_key(commitment),
			Error::<T>::OutboundRequestAlreadyClaimed,
		);
```

**File:** modules/pallets/relayer/src/outbound_request.rs (L148-149)
```rust
		let reward = OutboundRequestDeliveryReward::<T>::get(&module_id);
		ensure!(reward > BalanceOf::<T>::default(), Error::<T>::OutboundRequestNoRewardConfigured);
```

**File:** modules/pallets/relayer/src/lib.rs (L389-397)
```rust
		#[pallet::call_index(3)]
		#[pallet::weight({1_000_000})]
		pub fn claim_outbound_consensus_delivery_reward(
			origin: OriginFor<T>,
			claim: OutboundConsensusDeliveryClaim,
		) -> DispatchResult {
			ensure_none(origin)?;
			Self::process_outbound_consensus_delivery_claim(claim)
		}
```

**File:** modules/pallets/relayer/src/lib.rs (L399-450)
```rust
		/// Governance-set per-chain reward for delivering mandatory consensus
		/// proofs to that destination.
		#[pallet::call_index(4)]
		#[pallet::weight(<T as frame_system::Config>::DbWeight::get().reads_writes(0, 1))]
		pub fn set_outbound_consensus_delivery_reward(
			origin: OriginFor<T>,
			state_machine: StateMachine,
			amount: BalanceOf<T>,
		) -> DispatchResult {
			T::RelayerOrigin::ensure_origin(origin)?;
			OutboundConsensusDeliveryReward::<T>::insert(state_machine, amount);
			Self::deposit_event(Event::OutboundConsensusDeliveryRewardUpdated {
				state_machine,
				new_reward: amount,
			});
			Ok(())
		}

		/// Pay the configured `OutboundRequestDeliveryReward` to the relayer
		/// that delivered a hyperbridge-originated request to the destination.
		///
		/// Unsigned. Spam-protected by `validate_unsigned` (the encoded
		/// payload becomes a unique tag, so a duplicate submission with the
		/// same `commitment` is rejected at the txpool stage).
		#[pallet::call_index(5)]
		#[pallet::weight({1_000_000})]
		pub fn claim_outbound_request_delivery_reward(
			origin: OriginFor<T>,
			claim: OutboundRequestDeliveryClaim,
		) -> DispatchResult {
			ensure_none(origin)?;
			Self::process_outbound_request_delivery_claim(claim)
		}

		/// Governance-set per-`module_id` reward for delivering a
		/// hyperbridge-originated request from that module. Setting
		/// `amount = 0` removes the module from the allowlist.
		#[pallet::call_index(6)]
		#[pallet::weight(<T as frame_system::Config>::DbWeight::get().reads_writes(0, 1))]
		pub fn set_outbound_request_delivery_reward(
			origin: OriginFor<T>,
			module_id: BoundedVec<u8, ModuleIdBound>,
			amount: BalanceOf<T>,
		) -> DispatchResult {
			T::RelayerOrigin::ensure_origin(origin)?;
			OutboundRequestDeliveryReward::<T>::insert(&module_id, amount);
			Self::deposit_event(Event::OutboundRequestDeliveryRewardUpdated {
				module_id,
				new_reward: amount,
			});
			Ok(())
		}
```
