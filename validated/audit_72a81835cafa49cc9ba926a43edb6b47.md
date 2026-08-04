Found a real, locally-provable analog: `EvmHost.sol`'s state-commitment veto path hardcodes the rollback height instead of restoring the true previous verified height, unlike its Substrate counterpart.

### Title
Vetoing the latest EVM state commitment rolls back to a hardcoded height 1 instead of the last honest commitment, re-opening the entire committed range for resubmission — (File: `evm/src/core/EvmHost.sol`)

### Summary
`deleteStateMachineCommitmentInternal` in `EvmHost.sol` resets `_latestStateMachineHeight[stateMachineId]` to the literal constant `1` whenever the vetoed height happens to be the current latest, instead of restoring it to the last known-good height (as the Substrate ISMP host correctly does via `PreviousStateMachineHeight`). This throws away all information about which heights were previously honestly finalized, and re-opens every height between `1` and the vetoed height for re-acceptance by `handleConsensus`.

### Finding Description
`EvmHost.sol` stores only a single "latest height" pointer per state machine, with no equivalent of the Substrate pallet's `PreviousStateMachineHeight`: [1](#0-0) 

```solidity
function deleteStateMachineCommitmentInternal(StateMachineHeight memory height, address fisherman) internal {
    StateCommitment memory stateCommitment = _stateCommitments[height.stateMachineId][height.height];
    delete _stateCommitments[height.stateMachineId][height.height];
    delete _stateCommitmentsUpdateTime[height.stateMachineId][height.height];
    // technically any state commitment can be vetoed, safety check that it's the latest before resetting it.
    if (_latestStateMachineHeight[height.stateMachineId] == height.height) {
        _latestStateMachineHeight[height.stateMachineId] = 1;
    }
    ...
}
```

Compare this to the Substrate pallet, which explicitly preserves and restores the real previous height on veto: [2](#0-1) 

The consensus intake gate in `handleConsensus` only accepts a commitment if it is strictly greater than the stored latest height: [3](#0-2) 

```solidity
uint256 latestHeight = host.latestStateMachineHeight(intermediate.stateMachineId);
if (latestHeight != 0 && intermediate.height > latestHeight) {
    ...
    host.storeStateMachineCommitment(stateMachineHeight, intermediate.commitment);
}
```

Since a veto of the current latest height collapses `_latestStateMachineHeight` to `1` (rather than to the true previous honestly-finalized height, e.g. H_{n-1}), every intermediate height between `1` and the vetoed height becomes acceptable again by this gate — including heights that were never fraudulent and whose commitments/receipts have already been consumed by relayers (fee accumulation, request/response delivery, timeouts). This is directly analogous to the reported bug class: a state-restoring code path (`_pendingLtv` restore on unfreeze / `_latestStateMachineHeight` restore on veto) is corrupted because the implementation does not correctly track "the value to restore to," collapsing to a wrong constant instead of the actual prior state.

Anyone permitted to call `deleteStateMachineCommitment` (the configured `handler`, reached via `pallet-fishermen`'s permissionless-to-report-but-collator-gated `veto_state_commitment` on the Substrate side, or directly through the handler role on EVM deployments) can trigger this collapse merely by vetoing the current tip height — no compromised relayer, prover, or governance action is required beyond the intended veto mechanism itself.

### Impact Explanation
This corrupts the single value (`_latestStateMachineHeight[stateMachineId]`) that gates whether a resubmitted/re-verified height's `StateCommitment` can overwrite state and be trusted for downstream request/response/timeout proof verification. Reopening the acceptance window down to height `1` means:
- A relayer can resubmit an old, already-superseded (but honest and previously valid) height's commitment as if it were new, potentially re-triggering `RequestPayments`/receipt bookkeeping tied to that height a second time in flows that assume monotonic height progression.
- Any height in the now-reopened range can have its commitment silently replaced, undermining the "false remote state must never become trusted" invariant, since the replacement commitment for an old height did not go through a fresh challenge-period-aware ordering check against the actually-latest honest height.

This falls under the "false proof/state acceptance" and "logic attack" categories of the bounty scope, since the corrupted commitment ordering gate can cause the host to accept/re-accept state at heights it should have rejected.

### Likelihood Explanation
Medium. It requires only a single veto of the current-latest state commitment — a standard, expected part of the fisherman/challenge-period flow (see `deleteStateMachineCommitment`/`veto_state_commitment` calls throughout `modules/pallets/fishermen/src/lib.rs` and the byzantine handlers in `tesseract/messaging/substrate/src/byzantine.rs`, which routinely call `veto_state_commitment` on any state-root mismatch or missing header). No malicious relayer, prover, or governance actor is needed — a normal fisherman correctly vetoing a genuinely bad latest height, on the EVM host, unconditionally destroys the honest history pointer as a side effect.

### Recommendation
Track the true previous verified height per state machine on `EvmHost.sol` (mirroring `PreviousStateMachineHeight` in the pallet) and restore `_latestStateMachineHeight[stateMachineId]` to that value on veto instead of hardcoding `1`. `storeStateMachineCommitment` should record the outgoing "current latest" before overwriting it, exactly as `store_latest_commitment_height` does on Substrate.

### Proof of Concept
1. Consensus client advances state machine `S` honestly through heights `H1 < H2 < H3`, each via `handleConsensus` → `storeStateMachineCommitment`, so `_latestStateMachineHeight[S] == H3`.
2. A fisherman (or the configured `handler`) detects a genuine fault at `H3` and calls `deleteStateMachineCommitment(height=H3, fisherman)`.
3. `_latestStateMachineHeight[S] == H3` matches the vetoed height, so it is reset to `1`, discarding the fact that `H2` (and `H1`) were valid, finalized heights.
4. A subsequent `handleConsensus` proof can now supply any `intermediate.height` in `(1, H3)` — including a stale or attacker-influenced height equal to or below `H2` — and it passes the `intermediate.height > latestHeight` check (`latestHeight == 1`), letting `storeStateMachineCommitment` overwrite an already-settled height's commitment outside of the intended monotonic progression the pallet-side implementation enforces via `PreviousStateMachineHeight`.

### Citations

**File:** evm/src/core/EvmHost.sol (L711-732)
```text
    /**
     * @dev Delete the state commitment at given state height.
     */
    function deleteStateMachineCommitmentInternal(StateMachineHeight memory height, address fisherman) internal {
        StateCommitment memory stateCommitment = _stateCommitments[height.stateMachineId][height.height];
        delete _stateCommitments[height.stateMachineId][height.height];
        delete _stateCommitmentsUpdateTime[height.stateMachineId][height.height];
        // technically any state commitment can be vetoed, safety check that it's the latest before resetting it.
        if (_latestStateMachineHeight[height.stateMachineId] == height.height) {
            _latestStateMachineHeight[height.stateMachineId] = 1;
        }

        // track the fisherman responsible for rewards on hyperbridge through state proofs
        _vetoes[height.stateMachineId][height.height] = fisherman;

        emit StateCommitmentVetoed({
            stateMachineId: this.stateMachineId(_hostParams.hyperbridge, height.stateMachineId),
            stateCommitment: stateCommitment,
            height: height.height,
            fisherman: fisherman
        });
    }
```

**File:** modules/pallets/ismp/src/host.rs (L209-234)
```rust
		// technically any state commitment can be vetoed,
		// safety check that it's the latest before resetting it.
		if let Some(latest) = LatestStateMachineHeight::<T>::get(height.id) {
			if latest == height.height {
				// Reset back to the initial height to allow for honest updates
				let prev_height =
					PreviousStateMachineHeight::<T>::get(height.id).ok_or_else(|| {
						Error::Custom("Previous state machine height should exist".to_string())
					})?;
				LatestStateMachineHeight::<T>::insert(height.id, prev_height);
			}
		}
		Ok(())
	}

	fn freeze_consensus_client(&self, client: ConsensusStateId) -> Result<(), Error> {
		FrozenConsensusClients::<T>::insert(client, true);
		Ok(())
	}

	fn store_latest_commitment_height(&self, height: StateMachineHeight) -> Result<(), Error> {
		let previous_height = LatestStateMachineHeight::<T>::get(height.id).unwrap_or_default();
		PreviousStateMachineHeight::<T>::insert(height.id, previous_height);
		LatestStateMachineHeight::<T>::insert(height.id, height.height);
		Ok(())
	}
```

**File:** evm/src/core/HandlerV2.sol (L155-164)
```text
        uint256 intermediatesLen = intermediates.length;
        for (uint256 i = 0; i < intermediatesLen; i++) {
            IntermediateState memory intermediate = intermediates[i];
            uint256 latestHeight = host.latestStateMachineHeight(intermediate.stateMachineId);
            if (latestHeight != 0 && intermediate.height > latestHeight) {
                StateMachineHeight memory stateMachineHeight =
                    StateMachineHeight({stateMachineId: intermediate.stateMachineId, height: intermediate.height});
                host.storeStateMachineCommitment(stateMachineHeight, intermediate.commitment);
            }
        }
```
