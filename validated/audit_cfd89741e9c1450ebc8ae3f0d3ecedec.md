## Analysis

The strongest local analog to DLT-08 is in the Pharos consensus client, which is structurally identical to the reported bug class: signature verification never binds the signed message to a chain/state-machine identity. [1](#0-0) [2](#0-1) 

`VerifierState` (the "trusted state" object passed into `verify_pharos_block`) has no `chain_id` field at all: [3](#0-2) . The `chain_id` only exists one layer up, in the ISMP-facing `ConsensusState`, and is simply copied through unchanged by the ISMP client wrapper without ever being checked against anything inside the proof: [4](#0-3) .

The BLS signature check signs only `block_proof_hash` (the header hash) with a fixed, chain-agnostic domain separation tag `PHAROS_BLS_DST`: [5](#0-4) [6](#0-5) . Nowhere in `verify_pharos_block` — which checks staleness, block hash, epoch proof, validator membership, stake threshold, and BLS signature — is the state machine's `chain_id` incorporated into the signed message or compared against the header/proof.

### Title
Pharos consensus verifier never binds signed block proofs to a chain identifier, enabling cross-deployment header replay when validator sets overlap - (File: modules/consensus/pharos/verifier/src/lib.rs)

### Summary
`verify_pharos_block` and the underlying `VerifierState`/`BlockProof` types carry no chain identifier. The BLS aggregate signature is verified purely over the header/block hash with a constant DST; the ISMP wrapper (`PharosClient::verify_consensus`) never checks the update against `consensus_state.chain_id` before accepting it. Any two Pharos-derived consensus-client instances (e.g. mainnet vs. Atlantic testnet `ConsensusStateId`s, or any future Pharos-based deployment reusing the same validator BLS keys) can have proofs valid under one instance's trusted validator set accepted by the other's `verify_consensus`, because chain identity is never part of what is cryptographically verified.

### Finding Description
`ConsensusState::chain_id` in `modules/ismp/clients/pharos/src/lib.rs` is purely a bookkeeping field used to tag the resulting `StateMachineId` — it is carried through `verify_consensus` unmodified and never passed into `verify_pharos_block`, nor compared against the incoming `VerifierStateUpdate`. The actual cryptographic check, `verify_bls_signature`, signs only `block_proof_hash` (the header hash) using an aggregate of the trusted validator set's BLS keys and a fixed DST string. There is no chain-specific tag mixed into the signed payload and no assertion anywhere in the call chain (`verify_pharos_block` → `verify_block_signature` → `verify_validator_membership`/`verify_stake_threshold`/`verify_bls_signature`) that the header or proof originates from the specific chain the trusted state represents.

This is the exact broken invariant from DLT-08: signature and voting-power checks pass, but nothing forces the signed data to be scoped to the intended chain. If a validator operator set is shared or overlaps between two Pharos-based deployments configured on Hyperbridge (each with its own `ConsensusStateId`/`chain_id`, e.g. mainnet `688600` and testnet `688689`), a header/`BlockProof` that is valid and supermajority-signed for chain A will also pass `verify_pharos_block` when submitted through chain B's `PharosClient::verify_consensus`, since the verifier has no way to detect the mismatch.

### Impact Explanation
If exploitable, this allows false remote state to become trusted for a state machine it was never produced by — directly violating the "consensus proofs...must never let false remote state become trusted" pivot. Because `verify_consensus`'s output becomes the new `StateCommitment` (`state_root`, `timestamp`) stored via `update_client`/`store_state_machine_commitment`, an accepted cross-deployment header would let an attacker forge the state root that all downstream ISMP request/response/timeout proof verification trusts for that `StateMachineId`, enabling unauthorized message execution or fund movement gated on that state root.

### Likelihood Explanation
This requires the two Pharos-derived validator sets to genuinely overlap (shared BLS keys across deployments/environments) and epoch/height bookkeeping to align well enough for `verify_pharos_block`'s epoch-transition logic to accept the update — a real-world precondition that may or may not hold depending on how Polytope/Pharos provisions validator keys across mainnet and testnet. The missing binding itself, however, is unconditionally present in the code today: no code path anywhere ties the signed message or verifier state to a chain identifier, so the only thing standing between "signed under any other Pharos-derived deployment" and "accepted" is whether key material happens to be shared — exactly the gap DLT-08 describes.

### Recommendation
- Add a `chain_id` field to `VerifierState`/`VerifierStateUpdate` and require it to match the consensus client's configured `chain_id` before calling `verify_block_signature`.
- Mix the chain id into the signed message (e.g., `message = chain_id || block_proof_hash`) or into the BLS domain separation tag, so signatures are non-transferable across deployments even if validator keys are reused.
- Add a test that constructs a validator set shared between two `chain_id`s and asserts that a `BlockProof` valid for chain A is rejected when verified against chain B's trusted state.

### Proof of Concept
1. Deploy two Pharos-based consensus clients on Hyperbridge, `ConsensusStateId` "PHAR" pointing at `chain_id = 688600` (mainnet) and another pointing at `chain_id = 688689` (testnet), where the active validator BLS key set overlaps (e.g. same operators run both networks with the same keys, or a future Pharos-based chain forks with the same initial validator set).
2. Obtain a legitimately produced `VerifierStateUpdate` (header + `BlockProof` with valid aggregate BLS signature over 2/3+ stake) for chain A at some height/epoch that is compatible with chain B's trusted `finalized_block_number`/`current_epoch` bookkeeping.
3. Submit this update as the `proof` argument to `PharosClient::verify_consensus` for chain B's `ConsensusStateId`, along with chain B's `trusted_consensus_state`.
4. Because `verify_pharos_block` never checks `chain_id`, and the BLS signature check only validates the header hash against the (shared) validator set, verification succeeds and `consensus_state.chain_id` (chain B's, e.g. `688689`) is stamped onto a `StateCommitment` built from chain A's header — a false state root is now trusted as chain B's canonical state via `handlers::consensus::update_client` ( [7](#0-6) ).

### Citations

**File:** modules/consensus/pharos/verifier/src/lib.rs (L34-35)
```rust
/// Domain Separation Tag for Pharos BLS signatures.
pub const PHAROS_BLS_DST: &str = "BLS_SIG_BLS12381G2_XMD:SHA-256_SSWU_RO_POP_";
```

**File:** modules/consensus/pharos/verifier/src/lib.rs (L45-66)
```rust
pub fn verify_pharos_block<C: Config, H: Keccak256 + Send + Sync>(
	trusted_state: VerifierState,
	update: VerifierStateUpdate,
) -> Result<VerifierState, Error> {
	let update_block_number = update.block_number();
	let current_block_number = trusted_state.finalized_block_number;

	if update_block_number <= current_block_number {
		return Err(Error::StaleUpdate {
			current: current_block_number,
			update: update_block_number,
		});
	}

	let computed_hash = Header::from(&update.header).hash::<H>();

	if computed_hash != update.block_proof.block_proof_hash {
		return Err(Error::BlockProofHashMismatch {
			computed: computed_hash,
			provided: update.block_proof.block_proof_hash,
		});
	}
```

**File:** modules/consensus/pharos/verifier/src/lib.rs (L180-207)
```rust
fn verify_bls_signature(
	participants: &[BlsPublicKey],
	block_proof: &BlockProof,
	block_proof_hash: H256,
) -> Result<(), Error> {
	if participants.is_empty() {
		return Err(Error::NoParticipants);
	}

	let aggregate_pubkey =
		bls_utils::aggregate_public_keys(participants).map_err(Error::BlsError)?;

	// The message signed is the block_proof_hash
	let message = block_proof_hash.as_bytes().to_vec();

	let is_valid = bls::verify(
		&aggregate_pubkey,
		&message,
		&block_proof.aggregate_signature,
		&PHAROS_BLS_DST.as_bytes().to_vec(),
	);

	if !is_valid {
		return Err(Error::InvalidSignature);
	}

	Ok(())
}
```

**File:** modules/consensus/pharos/primitives/src/types.rs (L233-248)
```rust
impl VerifierState {
	/// Create a new verifier state with initial trusted state
	pub fn new(
		initial_validator_set: ValidatorSet,
		initial_block_number: u64,
		initial_hash: H256,
	) -> Self {
		let epoch = initial_validator_set.epoch;
		Self {
			current_validator_set: initial_validator_set,
			finalized_block_number: initial_block_number,
			finalized_hash: initial_hash,
			current_epoch: epoch,
		}
	}
}
```

**File:** modules/ismp/clients/pharos/src/lib.rs (L104-148)
```rust
	) -> Result<(Vec<u8>, ismp::consensus::VerifiedCommitments), Error> {
		let update = VerifierStateUpdate::decode(&mut &proof[..])
			.map_err(|e| Error::AnyHow(anyhow::anyhow!("{:?}", e).into()))?;

		let consensus_state =
			ConsensusState::decode(&mut &trusted_consensus_state[..]).map_err(|e| {
				Error::AnyHow(
					anyhow::anyhow!("Cannot decode trusted consensus state: {:?}", e).into(),
				)
			})?;

		let trusted_state: VerifierState = consensus_state.clone().into();

		let new_state = verify_pharos_block::<C, H>(trusted_state, update.clone())
			.map_err(|e| Error::AnyHow(anyhow::Error::from(e).into()))?;

		let state_commitment = StateCommitmentHeight {
			commitment: StateCommitment {
				timestamp: update.header.timestamp,
				overlay_root: None,
				state_root: update.header.state_root,
			},
			height: new_state.finalized_block_number,
		};

		let new_consensus_state = ConsensusState {
			current_validators: new_state.current_validator_set,
			finalized_height: new_state.finalized_block_number,
			finalized_hash: new_state.finalized_hash,
			current_epoch: new_state.current_epoch,
			chain_id: consensus_state.chain_id,
		};

		let mut state_machine_map: BTreeMap<StateMachineId, Vec<StateCommitmentHeight>> =
			BTreeMap::new();
		state_machine_map.insert(
			StateMachineId {
				state_id: StateMachine::Evm(new_consensus_state.chain_id),
				consensus_state_id,
			},
			vec![state_commitment],
		);

		Ok((new_consensus_state.encode(), state_machine_map))
	}
```

**File:** modules/ismp/core/src/handlers/consensus.rs (L41-49)
```rust
	let (new_state, intermediate_states) = consensus_client.verify_consensus(
		host,
		msg.consensus_state_id,
		trusted_state,
		msg.consensus_proof,
	)?;
	host.store_consensus_state(msg.consensus_state_id, new_state)?;
	let timestamp = host.timestamp();
	host.store_consensus_update_time(msg.consensus_state_id, timestamp)?;
```
