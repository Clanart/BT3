## Analysis

The ELFi bug's core invariant: **a rate/multiplier-dependent accrual must be settled (checkpointed) before the rate is allowed to change; otherwise the entire un-settled span gets paid out at whichever rate happens to be in effect at settlement time, rather than the rate that actually applied during each sub-period.**

The closest local analog in Hyperbridge is in `pallet-ismp-relayer`'s outbound-delivery reward claims. Both `claim_outbound_request_delivery_reward` and `claim_outbound_consensus_delivery_reward` read the *current* reward rate at claim time rather than a rate snapshotted at delivery time, and a delivery receipt can sit unclaimed indefinitely with no expiry — creating the exact same "rate changes without settling the pending accrual first" arbitrage window as the ELFi bug.

### Title
Outbound delivery rewards are priced at claim-time rather than delivery-time, letting any relayer snipe a rate increase on stale, already-delivered receipts - (File: `modules/pallets/relayer/src/outbound_request.rs`)

### Summary
`process_outbound_request_delivery_claim` and `process_outbound_consensus_delivery_claim` verify that a delivery *happened* (via state proof / `EvmHost._epochs` slot), then pay out `OutboundRequestDeliveryReward::<T>::get(&module_id)` / `OutboundConsensusDeliveryReward::<T>::get(destination)` — the reward value in storage **at the moment of the claim call**, not at the moment the delivery was proven on the destination chain. [1](#0-0) [2](#0-1) 

Because the only replay guard is a one-time idempotency flag (`OutboundRequestsClaimed` / `OutboundConsensusRotationsClaimed`) with **no deadline or delivery-time binding**, a delivery from any point in the past can be claimed at any point in the future: [3](#0-2) [4](#0-3) 

Governance can legitimately raise a module's or chain's reward at any time via `set_outbound_consensus_delivery_reward` (analogous `set` extrinsic exists for request rewards): [5](#0-4) 

### Finding Description
This is structurally identical to the ELFi bug: a payout is a function of `(accrued span, current rate)` instead of `(accrued span, rate-at-accrual)`. In ELFi, `position.leverage` and `initialMargin` are mutated by `_executeAddMargin` without first settling `updateBorrowingFee`, so the whole historical span gets billed at the *new* leverage. Here, the reward rate (`OutboundRequestDeliveryReward` / `OutboundConsensusDeliveryReward`) can be changed by governance at any time, but every **unclaimed** past delivery — no matter how old — is still payable, and it is paid at whatever rate is in storage when the claim finally executes, not the rate that was in effect when the relayer actually performed the delivery.

Any relayer (unprivileged, no special role beyond being the address that delivered the message/proof — which is itself checked cryptographically against the on-chain receipt) can:
1. Deliver a message/consensus-proof to a destination when the configured reward is low or zero, without claiming.
2. Wait for governance to raise the reward for that `module_id` / destination (a routine, expected operational action).
3. Submit the claim afterward and receive the *new*, higher reward for work performed under the *old* rate.

The existing guards — proof verification, signer/module allowlist checks, and the one-time claimed flag — only prevent *forging* a delivery or *double-claiming* the same delivery. None of them bind the payout amount to the rate that was in force when the delivery actually happened, so they do not stop this timing arbitrage.

### Impact Explanation
Each individual claim only pays the currently-configured reward, which is bounded by governance's intent for the *current* rate — but the protocol has no mechanism to distinguish "reward earned under the old schedule" from "reward earned under the new schedule." A relayer that anticipates or observes a reward increase can accumulate a backlog of cheap-to-produce deliveries/proofs and mass-claim them the moment the rate rises, extracting treasury funds at a rate far exceeding what was budgeted when those deliveries were incentivized. This directly drains `T::TreasuryPalletId`'s balance (paid via `Currency::transfer` from treasury to `payee_account` in both claim paths) beyond the amount the protocol intended to pay for that historical work — a fund-loss vector on protocol treasury, not merely a griefing issue.

### Likelihood Explanation
Any account holding a private key that delivered (or produced/co-signed) a mandatory consensus proof or hyperbridge-originated request can trigger this; no privileged role, malicious peer, or compromised relayer/prover is required — only patience and public visibility of the `OutboundConsensusDeliveryRewardUpdated` / reward-update events, which are broadcast on-chain. Governance raising rewards over the network's life is a normal, expected operational event, so the window for exploitation recurs naturally.

### Recommendation
Bind the reward amount to the rate in effect at the time the delivery was proven, not the time of claim:
- Snapshot the applicable reward rate into the on-chain receipt/commitment metadata at the point the delivery/rotation is first recorded (`RequestCommitments` claimed-flag insertion / `EvmHost.recordEpoch`), and have the claim extrinsics read that snapshot instead of the live `OutboundRequestDeliveryReward` / `OutboundConsensusDeliveryReward` storage.
- Alternatively, impose a claim deadline (e.g., N blocks after delivery) so a rate change cannot retroactively apply to deliveries made long before the change, forcing claims to be settled promptly at the rate that was current when the work was done — mirroring the ELFi recommendation to "update fees in a timely manner" rather than let stale state accrue under a changed rate.

### Proof of Concept
1. Governance sets `OutboundRequestDeliveryReward[module_id] = 0` (or a low value) for a module — the relayer is not incentivized to claim, but the delivery still happens (or nothing is delivered yet if reward is 0 and allowlist check `reward > 0` blocks it — in that case the analogous consensus-reward path, which has no zero-reward gate on delivery itself, i.e. the `_epochs` slot gets written on delivery regardless of the currently configured reward).
2. Relayer delivers a mandatory consensus proof to an EVM destination; `EvmHost.recordEpoch(set_id, relayer)` records it on-chain regardless of the reward configured at that time (delivery is unconditional; only the *claim* checks the reward value). [6](#0-5) 

3. Relayer does not call `claim_outbound_consensus_delivery_reward` yet.
4. Governance later raises `OutboundConsensusDeliveryReward[destination]` from low/zero to a large value via `set_outbound_consensus_delivery_reward`.
5. Relayer now submits `claim_outbound_consensus_delivery_reward` for the old `set_id` rotation delivered in step 2; `OutboundConsensusRotationsClaimed` has no entry yet (first claim), the state proof against the already-stored `EvmHost._epochs[set_id]` slot still validates, and the pallet pays out the *new, higher* reward from treasury: [7](#0-6) 

6. The relayer collects a reward far larger than what governance intended to pay for that specific historical delivery, funded entirely by treasury, with no way for the protocol to detect or prevent the mismatch.

### Citations

**File:** modules/pallets/relayer/src/outbound_request.rs (L138-141)
```rust
		ensure!(
			!OutboundRequestsClaimed::<T>::contains_key(commitment),
			Error::<T>::OutboundRequestAlreadyClaimed,
		);
```

**File:** modules/pallets/relayer/src/outbound_request.rs (L143-150)
```rust
		let module_id: BoundedVec<u8, ModuleIdBound> = request
			.from
			.clone()
			.try_into()
			.map_err(|_| Error::<T>::OutboundRequestModuleIdTooLong)?;
		let reward = OutboundRequestDeliveryReward::<T>::get(&module_id);
		ensure!(reward > BalanceOf::<T>::default(), Error::<T>::OutboundRequestNoRewardConfigured);

```

**File:** modules/pallets/relayer/src/outbound_consensus.rs (L126-129)
```rust
		ensure!(
			!OutboundConsensusRotationsClaimed::<T>::contains_key(destination, set_id),
			Error::<T>::OutboundRotationAlreadyClaimed,
		);
```

**File:** modules/pallets/relayer/src/outbound_consensus.rs (L174-196)
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

		OutboundConsensusRotationsClaimed::<T>::insert(destination, set_id, ());

		Self::deposit_event(Event::OutboundConsensusDeliveryRewarded {
			state_machine: destination,
			set_id,
			relayer: payee_account,
			amount: reward,
		});

```

**File:** modules/pallets/relayer/src/lib.rs (L399-415)
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
```

**File:** evm/src/core/EvmHost.sol (L670-681)
```text
    /**
     * @dev Record the relayer that first submitted a consensus proof for a new authority set epoch.
     * Only callable by the configured handler. Stale or duplicate epoch IDs are ignored.
     * @param authoritySetId the new authority set / epoch ID
     * @param relayer the relayer that delivered the consensus proof
     */
    function recordEpoch(uint256 authoritySetId, address relayer) external restrict(_hostParams.handler) {
        if (authoritySetId <= _currentEpoch) return;
        _currentEpoch = authoritySetId;
        _epochs[authoritySetId] = relayer;
        emit NewEpoch({authoritySetId: authoritySetId, relayer: relayer});
    }
```
