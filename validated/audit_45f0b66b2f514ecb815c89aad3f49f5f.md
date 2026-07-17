### Title
Unverified Contract Code Bytes Injected into Witness `base_state` Without Hash Commitment Check — (`File: chain/client/src/stateless_validation/partial_witness/partial_witness_tracker.rs`)

### Summary

Before `ProtocolFeature::SignedContractCodeResponse` is enabled, any unprivileged peer can send a `ContractCodeResponse::V1` for a valid `ChunkProductionKey`. The received contract bytes are accepted and injected directly into the chunk validator's witness `base_state` (`PartialState::TrieValues`) without ever verifying that `hash(bytes) == requested CodeHash`. This breaks the hash-binding commitment between the `CodeHash` in the `ContractCodeRequest` and the bytes stored in the witness reconstruction cache, causing the witness validation to fail and preventing the validator from producing a chunk endorsement.

### Finding Description

The stateless validation flow for contract code distribution works as follows:

1. A chunk validator receives `ChunkContractAccesses` containing `CodeHash` values for contracts accessed during chunk application.
2. The validator sends a `ContractCodeRequest` for missing contracts, keyed by those `CodeHash` values.
3. The validator receives a `ContractCodeResponse` and calls `handle_contract_code_response`.

In `handle_contract_code_response`: [1](#0-0) 

The validation gate is `validate_contract_code_response`: [2](#0-1) 

The signature check is **conditional** on `ProtocolFeature::SignedContractCodeResponse`. Before this feature is enabled, only epoch/height/shard relevance is checked — no signature, no identity binding. Any peer can send a `ContractCodeResponse::V1` for any currently-active `ChunkProductionKey`.

After passing this gate, the bytes are decompressed and stored:

```rust
let contracts = response.decompress_contracts()?;
self.partial_witness_tracker.store_accessed_contract_codes(key, contracts)
```

In `store_accessed_contract_codes` → `process_update` → `CacheEntry::update`, the bytes are stored as `AccessedContractsState::Received(codes)` with no check that `hash(code.0) == any_requested_CodeHash`. [3](#0-2) 

When enough witness parts are collected and `try_finalize` fires, the stored bytes are **directly injected** into the witness's committed state: [4](#0-3) 

The `accessed_contracts` bytes are appended to `PartialState::TrieValues` in `witness.main_state_transition().base_state` — the exact trie-node set used by `validate_chunk_state_witness` to replay the state transition and verify the post-state root. [5](#0-4) 

### Impact Explanation

An attacker who knows the current `ChunkProductionKey` (public information: `epoch_id`, `shard_id`, `height_created`) can send a `ContractCodeResponse::V1` with arbitrary bytes. These bytes are injected into the validator's witness `base_state`. When witness validation runs, the state root check fails because the injected bytes do not correspond to the expected trie nodes, causing the validator to not produce a chunk endorsement. The exact corrupted value is the `PartialState::TrieValues` vector inside `witness.main_state_transition().base_state`.

### Likelihood Explanation

The attack is reachable by any network peer before `ProtocolFeature::SignedContractCodeResponse` is activated. The attacker only needs to know the `ChunkProductionKey` (publicly observable from block headers) and send a well-formed `ContractCodeResponse::V1` message. No stake, no privileged role, no prior authentication is required.

### Recommendation

1. **Verify hash commitment on receipt**: After decompressing the contract bytes in `handle_contract_code_response`, verify that `hash(bytes) == requested_CodeHash` for each entry before calling `store_accessed_contract_codes`. The validator already knows which hashes it requested (stored in `AccessedContractsState::Requested { contract_hashes, .. }`); use that set to validate each received `CodeBytes`.

2. **Enforce `SignedContractCodeResponse` unconditionally**: Remove the conditional branch in `validate_contract_code_response` and require the V2 signed variant at all protocol versions, or gate the entire `ContractCodeResponse` handler on the feature flag being active.

### Proof of Concept

1. Observe a live `ChunkProductionKey` `(epoch_id, shard_id, height_created)` from any block header.
2. Craft a `ContractCodeResponse::V1` with `next_chunk = <observed key>` and `compressed_contracts` containing arbitrary bytes (e.g., a single zero-length entry).
3. Send this message to a chunk validator node over the P2P network.
4. `validate_contract_code_response` passes (relevance check succeeds, no signature required pre-feature).
5. `decompress_contracts()` returns the arbitrary bytes.
6. `store_accessed_contract_codes` stores them in `AccessedContractsState::Received`.
7. When witness parts complete, `try_finalize` injects the arbitrary bytes into `witness.main_state_transition().base_state` via `values.extend(...)`.
8. `validate_chunk_state_witness` fails the state-root check; the validator does not produce a chunk endorsement for that chunk. [2](#0-1) [4](#0-3) [6](#0-5)

### Citations

**File:** chain/client/src/stateless_validation/partial_witness/partial_witness_actor.rs (L1211-1223)
```rust
    fn handle_contract_code_response(&self, response: ContractCodeResponse) -> Result<(), Error> {
        if !validate_contract_code_response(
            self.epoch_manager.as_ref(),
            &response,
            self.runtime.store(),
        )?
        .is_relevant()
        {
            return Ok(());
        }
        let key = response.chunk_production_key().clone();
        let contracts = response.decompress_contracts()?;
        self.partial_witness_tracker.store_accessed_contract_codes(key, contracts)
```

**File:** chain/client/src/stateless_validation/validate.rs (L369-382)
```rust
pub fn validate_contract_code_response(
    epoch_manager: &dyn EpochManagerAdapter,
    response: &ContractCodeResponse,
    store: &Store,
) -> Result<ChunkRelevance, Error> {
    let key = response.chunk_production_key();
    require_relevant!(validate_chunk_relevant(epoch_manager, key, store)?);
    let protocol_version = epoch_manager.get_epoch_protocol_version(&key.epoch_id)?;
    if ProtocolFeature::SignedContractCodeResponse.enabled(protocol_version) {
        validate_witness_contract_code_response_signature(epoch_manager, response)?;
    }

    Ok(ChunkRelevance::Relevant)
}
```

**File:** chain/client/src/stateless_validation/partial_witness/partial_witness_tracker.rs (L126-141)
```rust
    pub fn update(
        &mut self,
        update: CacheUpdate,
    ) -> Option<(DecodePartialWitnessResult, Vec<CodeBytes>)> {
        match update {
            CacheUpdate::WitnessPart(partial_witness, encoder) => {
                self.process_witness_part(*partial_witness, encoder);
            }
            CacheUpdate::AccessedContractHashes(code_hashes) => {
                self.set_requested_contracts(code_hashes);
            }
            CacheUpdate::AccessedContractCodes(contract_codes) => {
                self.set_received_contracts(contract_codes);
            }
        }
        self.try_finalize()
```

**File:** chain/client/src/stateless_validation/partial_witness/partial_witness_tracker.rs (L492-495)
```rust
            // Merge accessed contracts into the main transition's partial state.
            let PartialState::TrieValues(values) =
                &mut witness.mut_main_state_transition().base_state;
            values.extend(accessed_contracts.into_iter().map(|code| code.0.into()));
```

**File:** chain/chain/src/stateless_validation/chunk_validation.rs (L782-822)
```rust
pub fn validate_chunk_state_witness(
    state_witness: ChunkStateWitness,
    pre_validation_output: PreValidationOutput,
    epoch_manager: &dyn EpochManagerAdapter,
    runtime_adapter: &dyn RuntimeAdapter,
    main_state_transition_cache: &MainStateTransitionCache,
    store: Store,
    save_witness_if_invalid: bool,
    rs: Arc<ReedSolomon>,
) -> Result<(), Error> {
    // Avoid cloning the witness if possible
    if !save_witness_if_invalid {
        return validate_chunk_state_witness_impl(
            state_witness,
            pre_validation_output,
            epoch_manager,
            runtime_adapter,
            main_state_transition_cache,
            rs,
        );
    }

    let result = validate_chunk_state_witness_impl(
        state_witness.clone(),
        pre_validation_output,
        epoch_manager,
        runtime_adapter,
        main_state_transition_cache,
        rs,
    );
    if result.is_err() {
        if let Err(storage_err) = save_invalid_chunk_state_witness(store, &state_witness) {
            tracing::error!(
                target: "stateless_validation",
                ?storage_err,
                "failed to store invalid state witness"
            );
        }
    }
    result
}
```

**File:** core/primitives/src/stateless_validation/contract_distribution.rs (L372-421)
```rust
pub enum ContractCodeResponse {
    V1(ContractCodeResponseV1) = 0,
    V2(ContractCodeResponseV2) = 1,
}

impl ContractCodeResponse {
    pub fn encode(
        next_chunk: ChunkProductionKey,
        contracts: &Vec<CodeBytes>,
        signer: &ValidatorSigner,
        protocol_version: ProtocolVersion,
    ) -> std::io::Result<Self> {
        if ProtocolFeature::SignedContractCodeResponse.enabled(protocol_version) {
            ContractCodeResponseV2::encode(next_chunk, contracts, signer).map(Self::V2)
        } else {
            ContractCodeResponseV1::encode(next_chunk, contracts).map(Self::V1)
        }
    }

    pub fn chunk_production_key(&self) -> &ChunkProductionKey {
        match self {
            Self::V1(v1) => &v1.next_chunk,
            Self::V2(v2) => &v2.inner.next_chunk,
        }
    }

    /// Account that produced this response. Available only for signed variants.
    pub fn responder(&self) -> Option<&AccountId> {
        match self {
            Self::V1(_) => None,
            Self::V2(v2) => Some(&v2.inner.responder),
        }
    }

    pub fn decompress_contracts(&self) -> std::io::Result<Vec<CodeBytes>> {
        let compressed_contracts = match self {
            Self::V1(v1) => &v1.compressed_contracts,
            Self::V2(v2) => &v2.inner.compressed_contracts,
        };
        compressed_contracts.decode().map(|(data, _size)| data)
    }

    /// Verifies the signature for signed variants. Returns `false` for
    /// unsigned variants since there is nothing to verify.
    pub fn verify_signature(&self, public_key: &PublicKey) -> bool {
        match self {
            Self::V1(_) => false,
            Self::V2(v2) => v2.verify_signature(public_key),
        }
    }
```
