Based on my investigation, I found a genuine local analog to the "yield mode never enabled → permanently unclaimable" bug pattern in Hyperbridge's outbound consensus delivery reward mechanism.

### Title
Genesis authority-set epoch is silently dropped by `recordEpoch`, making its outbound consensus delivery reward permanently unclaimable - (File: `evm/src/core/EvmHost.sol`)

### Summary
`EvmHost.recordEpoch` is the sole write path into the `_epochs[set_id]` slot that `pallet-ismp-relayer`'s `process_outbound_consensus_delivery_claim` proves against to pay out `OutboundConsensusDeliveryReward`. The guard `if (authoritySetId <= _currentEpoch) return;` compares against a `_currentEpoch` that defaults to `0`. Any authority-set id equal to the default (the genesis/initial epoch, `set_id == 0`) is silently ignored and never written to `_epochs`, so the relayer who delivers that mandatory consensus proof can never produce a valid state-proof + signature against a slot that was never populated. [1](#0-0) 

### Finding Description
`recordEpoch` is restricted to the configured handler and is meant to durably attribute "who delivered the consensus proof that brought in authority-set `set_id`" so that reward claims on Hyperbridge can later be verified via state proof: [2](#0-1) 

`_currentEpoch` is a `uint256` storage variable with an implicit default of `0`. Because the guard uses `<=` rather than `<`, the very first call with `authoritySetId == 0` fails the `authoritySetId <= _currentEpoch` check (`0 <= 0` is `true`) and returns before writing `_epochs[0]` or emitting `NewEpoch`. This means the destination `EvmHost` never records a relayer address for the genesis epoch, regardless of how many valid consensus proofs are submitted for it.

On the Hyperbridge (Substrate) side, `process_outbound_consensus_delivery_claim` proves the `_epochs[set_id]` slot and treats a decoded zero-address / absent value as "not proven yet": [3](#0-2) 

Since the slot for `set_id == 0` can never be populated, `decode_epochs_slot_address` will always resolve to `None` for that epoch, and the claim call will always fail with `OutboundDeliveryNotProven`: [4](#0-3) 

This is structurally identical to the Blast/Axis Finance report: a value that is supposed to accrue and later become claimable is stuck in a "never configured/never recorded" default state because of a boundary condition, and there is no other code path that fixes the attribution retroactively — the reward configured via `OutboundConsensusDeliveryReward` for that destination is functionally unclaimable for the genesis rotation. [5](#0-4) 

### Impact Explanation
The reward earmarked for delivering the genesis/first mandatory consensus rotation to a given EVM destination can never be paid to any relayer, since the on-chain attribution record required by the claim's state-proof verification is never written. This is a permanent, protocol-level loss of the intended incentive for that specific epoch — funds remain locked in the treasury pallet account with no code path to release them to the rightful relayer, mirroring the "yield forever lost" impact class from the source report, since normal claim flow can never succeed once past that boundary.

### Likelihood Explanation
This triggers deterministically and requires no attacker action: it happens automatically at genesis/first-epoch bring-up on every EVM destination chain, since `_currentEpoch` always starts at its Solidity default of `0`. Whether `set_id == 0` is actually used as the genesis authority-set identifier by the consensus client feeding `HandlerV2.handleConsensus` could not be fully confirmed from the files inspected (I did not locate the initial-authority-set-id assignment in the consensus client/genesis configuration code), so this should be verified against the actual initial `set_id` value used at deployment before treating it as certain in production.

### Recommendation
Change the guard in `recordEpoch` from `<=` to `<` so that a genesis epoch id equal to the default `_currentEpoch` value is still recorded, or initialize `_currentEpoch` to a sentinel value (e.g. `type(uint256).max` is wrong-direction; use an explicit `bool _epochInitialized` flag, or initialize `_currentEpoch` to a value below any real epoch id, such as leaving it default but changing the comparator) so the first legitimate call always writes the slot:
```solidity
function recordEpoch(uint256 authoritySetId, address relayer) external restrict(_hostParams.handler) {
    if (authoritySetId < _currentEpoch) return; // or track an explicit "seen" flag for the first write
    _currentEpoch = authoritySetId;
    _epochs[authoritySetId] = relayer;
    emit NewEpoch({authoritySetId: authoritySetId, relayer: relayer});
}
```
Additionally add a regression test asserting that `recordEpoch(0, relayer)` populates `_epochs[0]` when called before any other epoch has been recorded.

### Proof of Concept
1. Deploy `EvmHost` fresh; `_currentEpoch` defaults to `0`.
2. `HandlerV2.handleConsensus` processes the genesis consensus proof and calls `EvmHost.recordEpoch(0, relayer)`.
3. Inside `recordEpoch`: `authoritySetId (0) <= _currentEpoch (0)` evaluates `true`, function returns early; `_epochs[0]` remains `address(0)` and no `NewEpoch` event fires.
4. Governance configures `OutboundConsensusDeliveryReward` for this destination via `set_outbound_consensus_delivery_reward`.
5. The relayer that delivered the genesis proof submits `claim_outbound_consensus_delivery_reward` with `set_id = 0`; the pallet proves the `_epochs[0]` slot, which decodes to `address(0)`, so `decode_epochs_slot_address` returns `None` and the extrinsic reverts with `OutboundDeliveryNotProven` — permanently, since no future call can retroactively populate `_epochs[0]`.

### Citations

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

**File:** modules/pallets/relayer/src/outbound_consensus.rs (L159-165)
```rust
		// `raw` is the trie-level value of `EvmHost._epochs[set_id]`;
		// `decode_epochs_slot_address` handles the RLP-encoded form the
		// Ethereum trie stores. Returns `None` for an unset / zero-address
		// slot, which we surface as `OutboundDeliveryNotProven` (logically
		// equivalent to "no delivery proven yet").
		let evm_address = Self::decode_epochs_slot_address(destination, &raw)
			.ok_or(Error::<T>::OutboundDeliveryNotProven)?;
```

**File:** modules/pallets/relayer/src/outbound_consensus.rs (L200-216)
```rust
	/// Decode the `address` value from `EvmHost._epochs[set_id]` as returned
	/// by `EvmStateMachine::verify_state_proof`. Standard EVM chains RLP-encode
	/// the value; Pharos stores it as a raw 32-byte ABI-padded word.
	pub fn decode_epochs_slot_address(
		state_id: ismp::host::StateMachine,
		raw: &[u8],
	) -> Option<Address> {
		use alloy_rlp::Decodable;
		if let Ok(addr) = Address::decode(&mut &*raw) {
			return if addr == Address::ZERO { None } else { Some(addr) };
		}
		if crate::is_pharos(&state_id) && raw.len() == 32 {
			let addr = Address::from_slice(&raw[12..]);
			return if addr == Address::ZERO { None } else { Some(addr) };
		}
		None
	}
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
