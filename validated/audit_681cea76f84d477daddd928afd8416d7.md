### Title
Missing Lower-Bound Guard on `current_block_number` in `check_proof_facts` Causes Felt Underflow and Network Halt - (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/execution_constraints.cairo`)

---

### Summary

`check_proof_facts` computes `current_block_number - STORED_BLOCK_HASH_BUFFER` without first verifying that `current_block_number >= STORED_BLOCK_HASH_BUFFER`. When `current_block_number < 10` (the first 10 blocks of any new StarkNet network), the subtraction wraps around in Cairo's prime-field arithmetic to a value near PRIME (~2^252), causing the subsequent `assert_nn_le` to fail unconditionally. Any invoke transaction carrying non-empty `proof_facts` included in one of those early blocks makes the block unprovable, halting the network.

---

### Finding Description

`check_proof_facts` in `execution_constraints.cairo` validates that a proof-facts block number is old enough to have a stored hash:

```cairo
assert_nn_le(
    os_output_header.base_block_number, current_block_number - STORED_BLOCK_HASH_BUFFER
);
``` [1](#0-0) 

`STORED_BLOCK_HASH_BUFFER = 10`. [2](#0-1) 

In Cairo, all arithmetic is modular over the Stark prime P ≈ 2^251.6. When `current_block_number = k < 10`, the expression `k - 10` evaluates to `P - (10 - k)`, a value far above 2^128. `assert_nn_le(a, b)` internally calls `assert_le(a, b)`, which range-checks `b - a < 2^128`. Because `b ≈ P >> 2^128`, the range check fails unconditionally, raising a Cairo assertion error and aborting OS execution.

By contrast, the analogous subtraction in `write_block_number_to_block_hash_mapping` is correctly guarded:

```cairo
tempvar old_block_number = block_context.block_info_for_execute.block_number -
    STORED_BLOCK_HASH_BUFFER;
let is_old_block_number_non_negative = is_nn(old_block_number);
if (is_old_block_number_non_negative == FALSE) {
    return ();
}
``` [3](#0-2) 

No equivalent guard exists in `check_proof_facts`.

The function is reached from `execute_invoke_function_transaction` only when `proof_facts_size != 0`:

```cairo
check_proof_facts(
    proof_facts_size=proof_facts_size,
    proof_facts=proof_facts,
    current_block_number=block_context.block_info_for_execute.block_number,
    virtual_os_config_hash=block_context.os_global_context.virtual_os_config_hash,
);
``` [4](#0-3) 

The early-exit guard only skips the function when `proof_facts_size == 0`:

```cairo
if (proof_facts_size == 0) {
    return ();
}
``` [5](#0-4) 

An attacker submitting an invoke transaction with any non-zero `proof_facts` payload during blocks 0–9 triggers the abort path.

---

### Impact Explanation

The OS Cairo program is the prover-side component. An assertion failure inside it means no valid STARK proof can be generated for the block. The block cannot be finalized on L1. If the sequencer has already committed to the block's state (published the block header), it cannot simply discard the block; the network stalls on that block number. This matches the allowed impact: **High — network not being able to confirm new transactions (total network shutdown)**.

---

### Likelihood Explanation

The window is the first 10 blocks of any new StarkNet network deployment (mainnet re-genesis, new appchain, testnet reset). An attacker only needs to submit one syntactically valid invoke transaction with a non-empty `proof_facts` array. The sequencer's mempool validation checks transaction validity (signature, nonce, gas), not whether `proof_facts` is safe to include at the current block height. If the sequencer includes the transaction, proof generation fails. The sequencer may have no recovery path if it has already published the block.

---

### Recommendation

Add the same `is_nn` guard used in `write_block_number_to_block_hash_mapping` before the subtraction in `check_proof_facts`:

```cairo
// Guard: if the network is too young, no valid proof facts can exist.
let is_sufficient_blocks = is_nn(current_block_number - STORED_BLOCK_HASH_BUFFER);
if (is_sufficient_blocks == FALSE) {
    // Reject the transaction: proof facts require at least STORED_BLOCK_HASH_BUFFER blocks.
    // (Handle as a transaction-level failure, not an OS abort.)
    ...
    return ();
}
assert_nn_le(
    os_output_header.base_block_number, current_block_number - STORED_BLOCK_HASH_BUFFER
);
```

This mirrors the pattern already used correctly in `write_block_number_to_block_hash_mapping`. [3](#0-2) 

---

### Proof of Concept

1. Deploy a fresh StarkNet network (or reset a testnet). Current block number = 5.
2. Craft an invoke transaction (version 3) with `proof_facts` set to any non-empty byte array (e.g., a single zero felt). `proof_facts_size = 1`.
3. Submit the transaction. The sequencer validates signature, nonce, and gas — all pass.
4. The sequencer includes the transaction in block 5.
5. The OS begins proving block 5. It calls `execute_invoke_function_transaction`, which calls `check_proof_facts(proof_facts_size=1, ..., current_block_number=5, ...)`.
6. Inside `check_proof_facts`, `proof_facts_size != 0` so the early return is skipped.
7. The OS evaluates `current_block_number - STORED_BLOCK_HASH_BUFFER = 5 - 10`. In felt arithmetic this is `P - 5 ≈ 2^251`.
8. `assert_nn_le(base_block_number, P - 5)` calls `assert_le`, which range-checks `(P - 5) - base_block_number < 2^128`. Since `P - 5 >> 2^128`, the range check fails.
9. The OS aborts. No proof is generated. Block 5 cannot be finalized. The network halts. [6](#0-5)

### Citations

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/execution_constraints.cairo (L34-82)
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
    let proof_header = cast(proof_facts, ProofHeader*);
    assert is_program_hash_allowed(proof_header.program_hash) = TRUE;
    // Proof version and variant are for future compatibility.
    assert [proof_header] = ProofHeader(
        proof_version=PROOF_VERSION,
        proof_variant=VIRTUAL_SNOS,
        program_hash=proof_header.program_hash,
    );

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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/constants.cairo (L64-65)
```text
// The block number -> block hash mapping is written for the current block number minus this number.
const STORED_BLOCK_HASH_BUFFER = 10;
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/os_utils.cairo (L52-58)
```text
    tempvar old_block_number = block_context.block_info_for_execute.block_number -
        STORED_BLOCK_HASH_BUFFER;
    let is_old_block_number_non_negative = is_nn(old_block_number);
    if (is_old_block_number_non_negative == FALSE) {
        // Not enough blocks in the system - nothing to write.
        return ();
    }
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L313-318)
```text
    check_proof_facts(
        proof_facts_size=proof_facts_size,
        proof_facts=proof_facts,
        current_block_number=block_context.block_info_for_execute.block_number,
        virtual_os_config_hash=block_context.os_global_context.virtual_os_config_hash,
    );
```
