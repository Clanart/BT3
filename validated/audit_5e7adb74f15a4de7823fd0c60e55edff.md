### Title
Missing `arbitrum_consensus_type` binding check lets an update carry a mismatched consensus-proof variant - (File: modules/ismp/clients/ismp-arbitrum/src/lib.rs)

### Summary
`ArbitrumConsensusClient::verify_consensus` decodes the trusted `ConsensusState`, which stores an explicit `arbitrum_consensus_type: ArbitrumConsensusType` field meant to record whether a given `StateMachineId` is configured for `ArbitrumOrbit` or `ArbitrumBold` consensus, but the function never reads or checks that field. It instead dispatches purely on the `ArbitrumConsensusProof` variant carried in the *untrusted* `consensus_proof` bytes.

### Finding Description
The `ConsensusState` struct explicitly stores `arbitrum_consensus_type` [1](#0-0)  to bind a given `StateMachineId` to a single designated consensus mechanism. However, `verify_consensus` decodes the trusted state and the update, then matches solely on `proof` — the caller-supplied `ArbitrumConsensusProof` enum — never comparing it to `consensus_state.arbitrum_consensus_type`: [2](#0-1) 

Both branches (`ArbitrumOrbit` and `ArbitrumBold`) are reachable regardless of what `arbitrum_consensus_type` says, and both write into the same `state_machine_map`/`consensus_state.finalized_height` path: [3](#0-2) 

This confirms the exact scenario in the question: a `ConsensusState` configured with `arbitrum_consensus_type = ArbitrumOrbit` will still have `verify_arbitrum_bold` invoked (and vice versa) if the submitted `ArbitrumUpdate.proof` carries the other variant. The `state_machine_id`/`rollup_core_address` lookup is unaffected by the mismatch since it is keyed only by `StateMachineId`, not by consensus type, so the same rollup contract address is used for whichever verifier the caller chooses.

**Important caveat on exploitability:** both `verify_arbitrum_payload` and `verify_arbitrum_bold` independently perform full cryptographic verification against the *actual* L1 state root (`host.state_machine_commitment`) and real storage proofs against the configured `rollup_core_address` [4](#0-3) . `verify_arbitrum_bold` requires a valid entry under the `_assertions` storage slot (`ASSERTIONS_SLOT`) and fails with `Error::InvalidAssertion`/`Error::StateHashSlotMissing` if the slot proof does not resolve, and `verify_arbitrum_payload` similarly requires proof against the Orbit `_nodes` layout. So an attacker cannot forge L1 state; they can only pick which of the two independent verification paths is exercised against genuine on-chain data. Whether this is actually exploitable to accept "false" state therefore depends on whether the specific rollup-core contract instance ever has valid data under *both* mapping layouts simultaneously (e.g., during or after an Orbit→BoLD contract migration where legacy `_nodes` entries persist alongside new `_assertions` entries, or vice versa). I could not verify from the indexed code whether Hyperbridge's supported Arbitrum deployments have such an overlap window; this would need to be confirmed against the actual on-chain nitro-contracts deployment used.

### Impact Explanation
If a rollup-core contract instance genuinely has valid entries under both consensus mechanisms' storage layouts (e.g., mid-migration), an attacker could submit an `ArbitrumBold` proof against a chain that operators intended to remain pinned to `ArbitrumOrbit` (or vice versa), bypassing the intended binding between chain configuration and consensus mechanism, and potentially exploiting weaker/stronger challenge-period semantics of the "wrong" mechanism to get a state commitment accepted earlier or via a different challenge-window than the operator configured. This directly violates the stated pivot: "Consensus proofs, state proofs, challenge periods, and state commitments must never let false remote state become trusted," and the field is dead code, meaning any protection the field name suggests does not actually exist in this implementation.

### Likelihood Explanation
Low-to-medium. It requires no privileged access — any account permitted to submit ISMP consensus updates can choose the proof variant — but successful exploitation is gated on the target rollup contract instance actually exposing exploitable, valid storage-proof data under the "wrong" mechanism's layout, which is outside the module's control and not something the code enables by itself in a fresh, non-migrating deployment.

### Recommendation
In `verify_consensus`, before matching on `proof`, assert that the enum variant of `proof` matches `consensus_state.arbitrum_consensus_type` (e.g., reject with a new `ArbitrumError::ConsensusTypeMismatch` if `ArbitrumOrbit` proof is submitted against a `ArbitrumBold`-typed state or vice versa), so the trusted, previously-configured consensus mechanism — not attacker-supplied proof shape — determines which verifier runs.

### Proof of Concept
1. Create a `ConsensusState` with `arbitrum_consensus_type = ArbitrumConsensusType::ArbitrumOrbit` for a given `StateMachineId`, and register a `rollup_core_address` for it via `set_rollup_core_address` (admin-only, done once by the operator as normal setup).
2. Submit `ArbitrumUpdate { l1_height, proof: ArbitrumConsensusProof::ArbitrumBold(proof) }` through the normal (unprivileged) ISMP consensus-update path.
3. Observe that `verify_consensus` matches on `proof` [5](#0-4)  and dispatches to `verify_arbitrum_bold` without ever consulting `consensus_state.arbitrum_consensus_type`, confirming the type-binding invariant is not enforced in code, regardless of whether attacker-supplied storage proofs happen to resolve for that instance.

### Citations

**File:** modules/ismp/clients/ismp-arbitrum/src/lib.rs (L45-51)
```rust
#[derive(Encode, Decode, Debug, PartialEq, Eq, Clone)]
pub struct ConsensusState {
	pub finalized_height: u64,
	pub state_machine_id: StateMachineId,
	pub l1_state_machine_id: StateMachineId,
	pub arbitrum_consensus_type: ArbitrumConsensusType,
}
```

**File:** modules/ismp/clients/ismp-arbitrum/src/lib.rs (L109-219)
```rust
		let ArbitrumUpdate { l1_height, proof } =
			ArbitrumUpdate::decode(&mut &consensus_proof[..])
				.map_err(|_| ArbitrumError::DecodeArbitrumUpdate)?;

		let mut consensus_state = ConsensusState::decode(&mut &trusted_consensus_state[..])
			.map_err(|_| ArbitrumError::DecodeConsensusState)?;

		// The state machine being updated is fixed by the trusted consensus state, never
		// supplied by the (untrusted) update. This binds verifier-config selection to the
		// correct Arbitrum chain identity.
		let state_machine_id = consensus_state.state_machine_id;

		let l1_state_machine_height =
			StateMachineHeight { id: consensus_state.l1_state_machine_id, height: l1_height };

		let l1_state_commitment = host.state_machine_commitment(l1_state_machine_height)?;
		let state_root = l1_state_commitment.state_root;

		let mut state_machine_map: BTreeMap<StateMachineId, Vec<StateCommitmentHeight>> =
			BTreeMap::new();

		if let Some(rollup_core_address) =
			Pallet::<T>::state_machines_rollup_core_addresses(state_machine_id)
		{
			match proof {
				ArbitrumConsensusProof::ArbitrumOrbit(proof) => {
					// Derive the unified claim hash and refuse blacklisted entries before the
					// heavy proof verification.
					let state_hash = get_state_hash::<H>(
						proof.global_state,
						proof.machine_status,
						proof.inbox_max_count,
					);
					let claim = orbit_claim_hash::<H>(state_hash, proof.node_number);
					if <T as pallet::Config>::FishermanBlacklist::is_arbitrum_claim_blacklisted(
						state_machine_id,
						claim,
					) {
						return Err(ArbitrumError::ClaimBlacklisted(claim).into());
					}

					let state = verify_arbitrum_payload::<H>(
						proof,
						state_root,
						rollup_core_address,
						consensus_state_id.clone(),
					)?;

					let state_commitment_height = StateCommitmentHeight {
						commitment: state.commitment,
						height: state.height.height,
					};

					let mut state_commitment_vec: Vec<StateCommitmentHeight> = Vec::new();
					state_commitment_vec.push(state_commitment_height);
					state_machine_map.insert(
						StateMachineId {
							state_id: consensus_state.state_machine_id.state_id,
							consensus_state_id: consensus_state
								.l1_state_machine_id
								.consensus_state_id,
						},
						state_commitment_vec,
					);

					consensus_state.finalized_height = state.height.height;
				},
				ArbitrumConsensusProof::ArbitrumBold(proof) => {
					// BoLD assertions use the on-chain `assertionHash` directly as the claim key.
					let assertion_hash = compute_assertion_hash(
						proof.previous_assertion_hash,
						proof.after_state.hash(),
						proof.sequencer_batch_acc,
					);
					if <T as pallet::Config>::FishermanBlacklist::is_arbitrum_claim_blacklisted(
						state_machine_id,
						assertion_hash,
					) {
						return Err(ArbitrumError::ClaimBlacklisted(assertion_hash).into());
					}

					let state = verify_arbitrum_bold::<H>(
						proof,
						state_root,
						rollup_core_address,
						consensus_state_id.clone(),
					)?;

					let state_commitment_height = StateCommitmentHeight {
						commitment: state.commitment,
						height: state.height.height,
					};

					let mut state_commitment_vec: Vec<StateCommitmentHeight> = Vec::new();
					state_commitment_vec.push(state_commitment_height);
					state_machine_map.insert(
						StateMachineId {
							state_id: consensus_state.state_machine_id.state_id,
							consensus_state_id: consensus_state
								.l1_state_machine_id
								.consensus_state_id,
						},
						state_commitment_vec,
					);

					consensus_state.finalized_height = state.height.height;
				},
			}
		}

		Ok((consensus_state.encode(), state_machine_map))
```

**File:** modules/ismp/clients/arbitrum/src/lib.rs (L301-340)
```rust
pub fn verify_arbitrum_bold<H: Keccak256 + Send + Sync>(
	payload: ArbitrumBoldProof,
	root: H256,
	rollup_core_address: H160,
	consensus_state_id: ConsensusStateId,
) -> Result<IntermediateState, Error> {
	let storage_root =
		get_contract_account::<H>(payload.contract_proof, &rollup_core_address.0, root)?
			.storage_root
			.0
			.into();

	let header: Header = payload.arbitrum_header.as_ref().into();
	if &payload.after_state.global_state.send_root[..] != &payload.arbitrum_header.extra_data {
		Err(Error::HeaderExtraDataMismatch)?
	}

	let block_number = payload.arbitrum_header.number.low_u64();
	let timestamp = payload.arbitrum_header.timestamp;
	let state_root = payload.arbitrum_header.state_root.0.into();

	let header_hash = header.hash::<H>();
	if payload.after_state.global_state.block_hash != header_hash {
		Err(Error::HeaderHashMismatch)?
	}

	let assertion_hash = compute_assertion_hash(
		payload.previous_assertion_hash,
		payload.after_state.hash(),
		payload.sequencer_batch_acc,
	);

	let assertion_hash_key = derive_map_key::<H>(assertion_hash.0.to_vec(), ASSERTIONS_SLOT);

	// Only valid assertions nodes are inserted in the rollup storage
	// A Some() value from the proof asserts that this assertion is valid and exists in storage
	// https://github.com/OffchainLabs/nitro-contracts/blob/94999b3e2d3b4b7f8e771cc458b9eb229620dd8f/src/rollup/RollupCore.sol#L542

	get_value_from_proof::<H>(assertion_hash_key.0.to_vec(), storage_root, payload.storage_proof)?
		.ok_or(Error::InvalidAssertion)?;
```
