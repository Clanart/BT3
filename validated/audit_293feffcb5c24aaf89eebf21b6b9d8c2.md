### Title
Legitimate `EvmHost` migration permanently locks relayer consensus-delivery rewards due to unnamespaced replay-protection key - ([File: modules/pallets/relayer/src/outbound_consensus.rs])

### Summary
This is the direct structural analog of the `moveDao`/`Reserve.sol` bug: a source-of-truth address is updated in one place (`EvmHosts` in `pallet-ismp-host-executive`) while a dependent module's authorization/state-tracking data (`OutboundConsensusRotationsClaimed` in `pallet-ismp-relayer`) is never re-keyed to the new entity. The result is that a normal, legitimate operational action (redeploying/migrating an `EvmHost` contract for a chain) permanently and unrecoverably blocks legitimate relayer reward claims that happen to reuse a `set_id` already seen under the previous host contract.

### Finding Description
`pallet-ismp-host-executive::update_evm_hosts` lets governance repoint the registered `EvmHost` contract address for a `StateMachine`: [1](#0-0) 

`EvmHost.recordEpoch()` (restricted to the configured `handler`) stores relayer attribution per authority-set epoch in the contract's own `_epochs[set_id]` storage slot: [2](#0-1) 

`pallet-ismp-relayer::process_outbound_consensus_delivery_claim` looks up the *current* registered contract for the destination via `EvmHosts::<T>::get(destination)` to build the storage proof key and pay out the reward: [3](#0-2) 

Replay protection is a flat map keyed only by `(destination: StateMachine, set_id: u64)` — it carries no reference to which physical `EvmHost` contract instance actually emitted that epoch: [4](#0-3) [5](#0-4) 

Exactly as with `Reserve.sol`'s `DAO` field never being refreshed by `moveDao`, `EvmHosts` is the *only* place the host address lives on the Hyperbridge side; nothing re-derives or namespaces `OutboundConsensusRotationsClaimed` against the specific host contract instance. When a chain's `EvmHost` is migrated to a new address (redeploy, upgrade path, disaster recovery, etc. — a routine governance action, not an attack), the new contract's `_epochs` counter starts fresh from its own epoch numbering. Any `set_id` value that was already claimed against the *old* host is now permanently marked "claimed" globally for that `StateMachine`, even though the *new* host's epoch of the same numeric `set_id` is a distinct, legitimate rotation that was never paid.

### Impact Explanation
Legitimate relayer rewards for post-migration authority-set rotations become permanently unclaimable whenever their `set_id` collides with a `set_id` already claimed pre-migration on the same `StateMachine`. This is a straightforward loss/lock of protocol funds (relayer incentive payouts silently and permanently withheld from the rightful beneficiary) with no available remediation path other than a pallet-level force-fix, because the check `!OutboundConsensusRotationsClaimed::contains_key(destination, set_id)` unconditionally rejects the claim with `OutboundRotationAlreadyClaimed`.

### Likelihood Explanation
This does not require a malicious actor — it is triggered purely by the normal, expected `update_evm_hosts` governance workflow for chain/host migrations, mirroring how the original `moveDao` bug required only a normal DAO upgrade rather than any attacker action. Any chain that undergoes an `EvmHost` redeployment while low-numbered `set_id`s have already been claimed (which is virtually guaranteed for early epochs) will hit this collision.

### Recommendation
Namespace `OutboundConsensusRotationsClaimed` (and the underlying epoch attribution) by the specific `EvmHost` contract address, not just `(StateMachine, set_id)` — e.g. key on `(destination, evm_host_address, set_id)` — so that a migrated host's epoch numbering can never collide with the replay-protection state left behind by its predecessor. Alternatively, snapshot/carry-forward the last claimed `set_id` per host address, or require `update_evm_hosts` to explicitly reset/migrate the relevant relayer claim state for that `StateMachine` at the time of migration, similar to how `Reserve.sol` was recommended to call `setIncentiveAddresses` on every DAO upgrade.

### Proof of Concept
1. Governance registers `EvmHostV1` for `StateMachine::Evm(X)` via `update_evm_hosts`.
2. Over time, relayers successfully claim `OutboundConsensusDeliveryClaim`s for `set_id = 1, 2, 3` against `EvmHostV1._epochs`, each inserting `OutboundConsensusRotationsClaimed::<T>::insert(Evm(X), set_id, ())`.
3. Governance migrates the chain to a new `EvmHostV2` contract and calls `update_evm_hosts` to point `EvmHosts::<T>` at `EvmHostV2` (see `modules/pallets/host-executive/src/lib.rs:233-254`).
4. `EvmHostV2`'s handler calls `recordEpoch(1, relayerX)` for its own first authority-set rotation (a fresh, legitimate epoch on the new deployment).
5. A relayer submits a valid `OutboundConsensusDeliveryClaim` with `set_id = 1`, a correct state proof against `EvmHostV2`'s storage, and a valid signature.
6. `process_outbound_consensus_delivery_claim` rejects it with `Error::OutboundRotationAlreadyClaimed` because `OutboundConsensusRotationsClaimed::contains_key(Evm(X), 1)` is already `true` from step 2 — even though this specific reward for `EvmHostV2`'s epoch 1 was never paid. [4](#0-3)

### Citations

**File:** modules/pallets/host-executive/src/lib.rs (L233-254)
```rust
		pub fn update_evm_hosts(
			origin: OriginFor<T>,
			params: BTreeMap<StateMachine, H160>,
		) -> DispatchResult {
			T::HostExecutiveOrigin::ensure_origin(origin)?;

			for (state_machine, address) in params {
				let old = EvmHosts::<T>::get(&state_machine);
				EvmHosts::<T>::insert(state_machine.clone(), address);
				if let Some(old_address) = old {
					Self::deposit_event(Event::<T>::HostAddressUpdated {
						state_machine,
						old_address,
						new_address: address,
					});
				} else {
					Self::deposit_event(Event::<T>::HostAddressSet { state_machine, address });
				}
			}

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

**File:** modules/pallets/relayer/src/outbound_consensus.rs (L126-129)
```rust
		ensure!(
			!OutboundConsensusRotationsClaimed::<T>::contains_key(destination, set_id),
			Error::<T>::OutboundRotationAlreadyClaimed,
		);
```

**File:** modules/pallets/relayer/src/outbound_consensus.rs (L131-145)
```rust
		// EvmHost lookup. The per-chain host address is tracked by
		// `pallet-ismp-host-executive` in `EvmHosts`. Non-EVM destinations
		// are absent from that map and are rejected here, since the
		// attribution mechanism is EVM-specific.
		let evm_host = EvmHosts::<T>::get(destination).ok_or(Error::<T>::OutboundHostNotKnown)?;

		// 52-byte storage key the EVM state proof verifier expects:
		// `evm_host (20) || keccak256(set_id || EVM_HOST_EPOCHS_SLOT) (32)`.
		let slot_hash = evm_state_machine::utils::derive_unhashed_map_key::<<T as Config>::IsmpHost>(
			U256::from(set_id).to_big_endian().to_vec(),
			EVM_HOST_EPOCHS_SLOT,
		);
		let mut key = Vec::with_capacity(52);
		key.extend_from_slice(&evm_host.0);
		key.extend_from_slice(&slot_hash.0);
```

**File:** modules/pallets/relayer/src/outbound_consensus.rs (L186-188)
```rust
		.map_err(|_| Error::<T>::OutboundRewardTransferFailed)?;

		OutboundConsensusRotationsClaimed::<T>::insert(destination, set_id, ());
```
