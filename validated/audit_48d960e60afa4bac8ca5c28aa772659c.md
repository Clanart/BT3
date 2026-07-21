### Title
`VirtualOsOutput` proof_facts do not bind to the submitting transaction — proof replay across arbitrary transactions accepted - (File: `crates/starknet_os/src/io/virtual_os_output.rs`)

### Summary

The virtual OS output that forms the `proof_facts` field of an Invoke V3 transaction contains no transaction-identifying information (no transaction hash, sender, calldata, nonce, or state diff). Both the Rust-side `validate_proof_facts` and the Cairo-side `check_proof_facts` only verify block-level properties. A valid `(proof_facts, proof)` pair produced for one transaction can therefore be copied verbatim into any other transaction and will pass every sequencer check, allowing an attacker to claim client-side-proving benefits without ever running the prover.

### Finding Description

The virtual OS output layout is documented explicitly in `virtual_os_output.cairo`:

```
[output_version, base_block_number, base_block_hash, starknet_os_config_hash,
 n_l2_to_l1_messages, message_hash_0, message_hash_1, ...]
No state diff, data availability, or state roots are included.
```

The Rust struct mirrors this exactly:

```rust
pub struct VirtualOsOutput {
    pub version: Felt,
    pub base_block_number: BlockNumber,
    pub base_block_hash: StarkHash,
    pub starknet_os_config_hash: StarkHash,
    pub messages_to_l1_hashes: Vec<StarkHash>,
}
```

The transaction hash, sender address, calldata, and nonce are absent from the proof output. [1](#0-0) 

The `SnosProofFacts` parsed from the raw `proof_facts` field captures only five fields — `proof_version`, `program_hash`, `block_number`, `block_hash`, `config_hash` — with the remainder (`..`) being the L2-to-L1 message hashes, none of which identify the transaction. [2](#0-1) 

`validate_proof_facts` in the blockifier validates exactly those five block-level properties and nothing else: [3](#0-2) 

The Cairo `check_proof_facts` enforces the same five checks and returns without inspecting any transaction-specific data: [4](#0-3) 

Although `proof_facts` are hashed into the transaction hash when non-empty, that only means two transactions with the same `proof_facts` have different hashes — it does not prevent Bob from constructing a new transaction that carries Alice's `proof_facts` verbatim: [5](#0-4) 

The proof verifier (`verify_proof`) checks that the proof is cryptographically valid for the given `proof_facts`. Because `proof_facts` contain no transaction-identifying data, a proof generated for Alice's transaction is equally valid for Bob's transaction that carries the same `proof_facts`. The verifier has no way to distinguish the two. [6](#0-5) 

The virtual OS output version contract itself states: "Exactly 1 transaction per block, which must be INVOKE_FUNCTION" and "No proof facts" (recursive proof facts are blocked), but it makes no commitment to the identity of that transaction in the output. [7](#0-6) 

### Impact Explanation

An attacker (Bob) can:

1. Observe a valid `(proof_facts, proof)` pair from Alice's transaction T_A — either from the mempool, from the proof manager, or from an on-chain transaction.
2. Construct T_B with arbitrary calldata/sender/nonce and copy Alice's `proof_facts` and `proof` into T_B.
3. Submit T_B. The gateway calls `verify_proof(proof_facts, proof)` — passes, because the proof is cryptographically valid for those `proof_facts`. `validate_proof_facts` checks block hash / block number / program hash / config hash — all pass.
4. T_B is sequenced. The blockifier calls `check_proof_facts` — passes (only block-level checks).
5. T_B executes Bob's calldata. Bob's transaction is accepted with Alice's proof attestation.

For the privacy-pool / client-side-proving use case this is directly exploitable: the proof attests that *some* transaction was executed correctly on a specific base block, but it says nothing about *which* transaction. Bob bypasses the proof-based screening entirely without running the prover. The sequencer accepts an Invoke V3 transaction whose `proof_facts` bind to a completely different execution, violating the invariant that proof_facts prove the current transaction's execution.

This matches the **High** impact: *Transaction conversion or signature/hash logic binds the wrong signer, hash, type, or executable payload.*

### Likelihood Explanation

Exploitation requires only that an attacker observe one valid `(proof_facts, proof)` pair from any previously submitted client-side-proving transaction. Transactions are public; the proof manager stores proofs indexed by `proof_facts`. Any network participant can perform this attack with no special privileges and no cryptographic work.

### Recommendation

Include the transaction hash (or at minimum the Poseidon hash of `sender_address ‖ nonce ‖ calldata`) in the virtual OS output so that `proof_facts` cryptographically bind to the specific transaction. The `VIRTUAL_SNOS0` version contract in `virtual_os_output.cairo` must be bumped, and both `validate_proof_facts` (Rust) and `check_proof_facts` (Cairo) must verify the new binding field against the transaction being executed.

### Proof of Concept

```
1. Alice runs the virtual OS prover on her Invoke V3 transaction T_A
   (sender=S_A, nonce=N_A, calldata=C_A) and obtains (proof_facts=PF_A, proof=P_A).
   Alice submits T_A.

2. Bob observes PF_A and P_A (from the mempool or proof manager).

3. Bob constructs T_B:
     sender=S_B, nonce=N_B, calldata=C_B   (arbitrary, different from Alice's)
     proof_facts = PF_A                     (copied from Alice)
     proof       = P_A                      (copied from Alice)

4. Bob submits T_B.
   - Gateway: verify_proof(PF_A, P_A) → OK  (proof is valid for PF_A)
   - Gateway: validate_proof_facts(PF_A) → OK  (block hash/number/program/config all valid)
   - Sequencer: T_B is included in a block.
   - Blockifier: check_proof_facts(PF_A, ...) → OK  (only block-level checks)
   - T_B executes with Bob's calldata C_B.

5. Result: Bob's transaction carries Alice's proof attestation.
   The sequencer accepted a transaction whose proof_facts prove a different
   transaction's execution, violating the proof-binding invariant.
```

### Citations

**File:** crates/starknet_os/src/io/virtual_os_output.rs (L17-28)
```rust
pub struct VirtualOsOutput {
    /// The output version (should be `VIRTUAL_SNOS0`).
    pub version: Felt,
    /// The base block number.
    pub base_block_number: BlockNumber,
    /// The base block hash.
    pub base_block_hash: StarkHash,
    /// The hash of the Starknet OS config.
    pub starknet_os_config_hash: StarkHash,
    /// Array of hashes, one per message from L2 to L1.
    pub messages_to_l1_hashes: Vec<StarkHash>,
}
```

**File:** crates/starknet_api/src/transaction/fields.rs (L759-791)
```rust
        let [program_hash, output_version, block_number_felt, block_hash, config_hash, ..] =
            snos_fields
        else {
            return Err(StarknetApiError::InvalidProofFacts(format!(
                "SNOS proof facts is too small with {} fields",
                proof_facts.0.len()
            )));
        };

        // TODO(Yoni): reuse VirtualOsOutput parsing.
        let expected_version = VIRTUAL_OS_OUTPUT_VERSION;
        if *output_version != expected_version {
            return Err(StarknetApiError::InvalidProofFacts(format!(
                "Expected SNOS proof facts version to be {} (VIRTUAL_OS_OUTPUT_VERSION), but got \
                 {}",
                expected_version, output_version
            )));
        }

        let block_number = BlockNumber((*block_number_felt).try_into().map_err(|_| {
            StarknetApiError::InvalidProofFacts(format!(
                "Block number field is not a valid u64: {}",
                block_number_felt
            ))
        })?);

        Ok(ProofFactsVariant::Snos(SnosProofFacts {
            proof_version,
            program_hash: *program_hash,
            block_number,
            block_hash: BlockHash(*block_hash),
            config_hash: *config_hash,
        }))
```

**File:** crates/blockifier/src/transaction/account_transaction.rs (L291-351)
```rust
    fn validate_proof_facts(
        &self,
        block_context: &BlockContext,
        state: &mut dyn State,
    ) -> TransactionPreValidationResult<()> {
        // Only Invoke V3 transactions can carry proof facts.
        let Transaction::Invoke(invoke_tx) = &self.tx else {
            return Ok(());
        };
        if invoke_tx.version() < TransactionVersion::THREE {
            return Ok(());
        }

        // Parse proof facts.
        let proof_facts = invoke_tx.proof_facts();
        let snos_proof_facts = match ProofFactsVariant::try_from(&proof_facts)
            .map_err(|e| TransactionPreValidationError::InvalidProofFacts(e.to_string()))?
        {
            ProofFactsVariant::Empty => return Ok(()),
            ProofFactsVariant::Snos(snos_proof_facts) => snos_proof_facts,
        };
        let os_constants = &block_context.versioned_constants.os_constants;

        if !os_constants.allowed_proof_versions.contains(&snos_proof_facts.proof_version.as_felt())
        {
            return Err(TransactionPreValidationError::InvalidProofFacts(format!(
                "Proof version {} is not allowed under this protocol version.",
                snos_proof_facts.proof_version
            )));
        }

        // Validate the program hash.
        let allowed = &os_constants.allowed_virtual_os_program_hashes;
        if !allowed.contains(&snos_proof_facts.program_hash) {
            return Err(TransactionPreValidationError::InvalidProofFacts(format!(
                "Virtual OS program hash {} is not allowed",
                snos_proof_facts.program_hash
            )));
        }

        // Validate the block hash and block number.
        let proof_block_hash = snos_proof_facts.block_hash.0;
        let proof_block_number = snos_proof_facts.block_number.0;
        Self::validate_proof_block_number(
            proof_block_number,
            block_context.block_info.block_number,
        )?;
        Self::validate_proof_block_hash(proof_block_hash, proof_block_number, os_constants, state)?;

        // Validate the config hash.
        let virtual_os_config_hash = block_context.virtual_os_config_hash();
        let proof_config_hash = snos_proof_facts.config_hash;
        if virtual_os_config_hash != proof_config_hash {
            return Err(TransactionPreValidationError::InvalidProofFacts(format!(
                "Virtual OS config hash mismatch. Computed virtual OS config hash: \
                 {virtual_os_config_hash}, expected virtual OS config hash: {proof_config_hash}."
            )));
        }

        Ok(())
    }
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/execution_constraints.cairo (L36-84)
```text
func check_proof_facts{range_check_ptr, contract_state_changes: DictAccess*}(
    proof_facts_size: felt,
    proof_facts: felt*,
    current_block_number: felt,
    virtual_os_config_hash: felt,
) {
    if (proof_facts_size == 0) {
        return ();
    }

    assert_le(ProofHeader.SIZE + VirtualOsOutputHeader.SIZE, proof_facts_size);

    // Validate the proof header.
    static_assert ProofHeader.SIZE == 3;
    let proof_header = cast(proof_facts, ProofHeader*);
    assert proof_header.proof_variant = VIRTUAL_SNOS;
    assert is_program_hash_allowed(proof_header.program_hash) = TRUE;
    // Proof version may be V0 (legacy) or V1 (current).
    with_attr error_message("Unsupported proof version") {
        assert proof_header.proof_version = PROOF_VERSION_V1;
    }

    // Validate the virtual OS output header.
    let os_output_header = cast(&proof_facts[ProofHeader.SIZE], VirtualOsOutputHeader*);

    with_attr error_message("Virtual OS output version is not supported") {
        assert os_output_header.output_version = VIRTUAL_OS_OUTPUT_VERSION;
    }

    // Validate that the proof facts block number is not too recent.
    // (This is a sanity check - the following non-zero check ensures that the block hash is
    // not trivial).
    assert_nn_le(
        os_output_header.base_block_number, current_block_number - STORED_BLOCK_HASH_BUFFER
    );
    // Not all block hashes are stored in the contract; Make sure the requested one is not trivial.
    assert_not_zero(os_output_header.base_block_hash);

    // validate that the proof facts block hash is the true hash of the proof facts block number.
    read_block_hash_from_storage(
        block_number=os_output_header.base_block_number,
        expected_block_hash=os_output_header.base_block_hash,
    );

    // validate that the proof facts config hash is the true hash of the OS config.
    assert os_output_header.starknet_os_config_hash = virtual_os_config_hash;

    return ();
}
```

**File:** crates/starknet_api/src/transaction_hash.rs (L399-404)
```rust
    if !transaction.proof_facts().0.is_empty() {
        let proof_facts_hash =
            HashChain::new().chain_iter(transaction.proof_facts().0.iter()).get_poseidon_hash();
        hash_chain = hash_chain.chain(&proof_facts_hash);
    }
    Ok(TransactionHash(hash_chain.get_poseidon_hash()))
```

**File:** crates/starknet_transaction_prover/src/proving/virtual_snos_prover.rs (L336-345)
```rust
    let prover_output = prove(runner_output.cairo_pie, precomputes).await?;
    // Convert program output to proof facts using VIRTUAL_SNOS variant marker.
    let proof_facts = prover_output.program_output.try_into_proof_facts(VIRTUAL_SNOS)?;

    Ok(ProveTransactionResult {
        proof: prover_output.proof,
        proof_facts,
        l2_to_l1_messages: runner_output.l2_to_l1_messages,
        additional_data: None,
    })
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/virtual_os_output.cairo (L9-50)
```text
// === VIRTUAL_SNOS0 Version Contract ===
//
// This version string is a commitment to the exact behavior and output format of the virtual OS.
// Any change to the following guarantees MUST be accompanied by a version bump.
//
// 1. Output format:
//    The output is a flat array of felts with the following layout:
//      [output_version, base_block_number, base_block_hash, starknet_os_config_hash,
//       n_l2_to_l1_messages, message_hash_0, message_hash_1, ...]
//    - output_version: the VIRTUAL_OS_OUTPUT_VERSION constant.
//    - base_block_number / base_block_hash: the block this run is based on. The hash is
//      computed (proven) by the OS from the block info and the initial state root.
//    - starknet_os_config_hash: Poseidon hash of the Starknet OS config.
//    - n_l2_to_l1_messages: count of L2-to-L1 message hashes that follow.
//    - Each message hash is Poseidon([from_address, to_address, payload_size, ...payload]).
//    No state diff, data availability, or state roots are included.
//
// 2. Single block, single transaction:
//    - Exactly 1 block is processed (asserted in process_os_output).
//    - Exactly 1 transaction per block, which must be INVOKE_FUNCTION (asserted in
//      execute_transactions_inner).
//
// 3. Blocked syscalls:
//    The following syscalls are NOT available in virtual OS mode and will cause a Cairo error:
//      - Deploy
//      - GetBlockHash
//      - ReplaceClass
//      - Keccak
//      - MetaTxV0
//
// 4. Cairo 1 only:
//    Only Sierra (Cairo 1) contracts are supported. Deprecated (Cairo 0) entry points are
//    unreachable.
//
// 5. No proof facts:
//    The virtual OS does not support recursive proof facts (proof_facts_size must be 0).
//
// 6. Block info semantics:
//     get_execution_info returns the **base (previous) block** info.
//
// Changes to ANY of the above MUST trigger a version bump.
const VIRTUAL_OS_OUTPUT_VERSION = 'VIRTUAL_SNOS0';
```
