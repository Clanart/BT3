### Title
Unchecked Subtraction in `check_proof_facts` Causes OS Proof Failure When Block Number < `STORED_BLOCK_HASH_BUFFER` — (`execution/execution_constraints.cairo`)

---

### Summary

`check_proof_facts` performs `current_block_number - STORED_BLOCK_HASH_BUFFER` without first verifying that `current_block_number >= STORED_BLOCK_HASH_BUFFER`. In Cairo's prime-field arithmetic, this subtraction silently wraps to a huge value near the field prime when the block number is small. The subsequent `assert_nn_le` then fails because the wrapped value is not in `[0, 2^128)`, causing the OS proof to be invalid and the network to halt for any block containing an invoke transaction with non-empty proof facts.

---

### Finding Description

In `execution_constraints.cairo`, `check_proof_facts` contains:

```cairo
assert_nn_le(
    os_output_header.base_block_number, current_block_number - STORED_BLOCK_HASH_BUFFER
);
``` [1](#0-0) 

`STORED_BLOCK_HASH_BUFFER` is the constant `10`: [2](#0-1) 

When `current_block_number < 10`, the expression `current_block_number - STORED_BLOCK_HASH_BUFFER` underflows in the field, producing a value near the Stark prime (≈ 2^251). `assert_nn_le` then fails because its second argument is not in the valid range `[0, 2^128)`, making the entire OS proof invalid.

The sibling function `write_block_number_to_block_hash_mapping` in `os_utils.cairo` performs the **identical subtraction** but correctly guards it with `is_nn` first:

```cairo
tempvar old_block_number = block_context.block_info_for_execute.block_number -
    STORED_BLOCK_HASH_BUFFER;
let is_old_block_number_non_negative = is_nn(old_block_number);
if (is_old_block_number_non_negative == FALSE) {
    return ();
}
``` [3](#0-2) 

`check_proof_facts` has no such guard. This is the same vulnerability class as the external report: an arithmetic assumption that `A >= B` is always true, but a reachable condition makes `A < B`, causing a field-arithmetic underflow that halts the system.

`check_proof_facts` is called from `execute_invoke_function_transaction` for every invoke transaction that carries non-empty proof facts: [4](#0-3) 

---

### Impact Explanation

If the OS proof fails, the sequencer cannot submit a valid proof to L1, and the block cannot be confirmed. Any block at height `< 10` that contains an invoke transaction with `proof_facts_size > 0` will produce an unprovable OS execution, causing a **total network shutdown** for that block height. The sequencer has no in-protocol fallback: the OS Cairo program itself asserts and halts.

**Impact: High — Network not being able to confirm new transactions (total network shutdown).**

---

### Likelihood Explanation

The window is the first 10 blocks of the network's life (`block_number` in `{0, 1, …, 9}`). An unprivileged user only needs to submit a standard invoke transaction with a non-empty `proof_facts` field. The sequencer has no OS-level guard preventing inclusion of such a transaction during early blocks. The attacker-controlled entry path is:

1. Attacker submits an invoke transaction with `proof_facts_size > 0`.
2. Sequencer includes it in a block where `current_block_number < 10`.
3. OS calls `check_proof_facts` → subtraction underflows → `assert_nn_le` fails → proof invalid → block unconfirmable.

Likelihood is low on a mature network (past block 10), but **critical at network genesis** and during any chain reset or regenesis scenario.

---

### Recommendation

Mirror the guard already present in `write_block_number_to_block_hash_mapping`. Before performing the subtraction, check that `current_block_number >= STORED_BLOCK_HASH_BUFFER` and return early (or reject the transaction) if not:

```cairo
func check_proof_facts{range_check_ptr, contract_state_changes: DictAccess*}(
    proof_facts_size: felt,
    proof_facts: felt*,
    current_block_number: felt,
    virtual_os_config_hash: felt,
) {
    if (proof_facts_size == 0) {
        return ();
    }

    // Guard: proof facts require a sufficiently old block to exist.
    let is_block_old_enough = is_nn(current_block_number - STORED_BLOCK_HASH_BUFFER);
    if (is_block_old_enough == FALSE) {
        // Cannot have valid proof facts this early in the chain.
        assert proof_facts_size = 0;  // or revert with an appropriate error
        return ();
    }

    // ... rest of existing logic ...
    assert_nn_le(
        os_output_header.base_block_number, current_block_number - STORED_BLOCK_HASH_BUFFER
    );
```

---

### Proof of Concept

**Setup**: Deploy a StarkNet network from genesis (block 0). Submit an invoke transaction at block 5 with `proof_facts` set to any non-empty valid-looking byte sequence (e.g., a `ProofHeader` + `VirtualOsOutputHeader` with `base_block_number = 0`).

**Execution trace**:

```
execute_invoke_function_transaction(block_context)
  → check_proof_facts(
        proof_facts_size=<non-zero>,
        proof_facts=<attacker data>,
        current_block_number=5,          // < STORED_BLOCK_HASH_BUFFER (10)
        virtual_os_config_hash=...
    )
  → assert_nn_le(
        os_output_header.base_block_number,
        5 - 10                           // = P - 5 in field arithmetic (≈ 2^251)
    )
  → ASSERTION FAILURE: P - 5 is not in [0, 2^128)
  → OS proof invalid → block unconfirmable → network halt
```

**Contrast**: `write_block_number_to_block_hash_mapping` at the same block number correctly detects `is_nn(5 - 10) == FALSE` and returns early without halting. [5](#0-4)

### Citations

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/execution_constraints.cairo (L40-68)
```text
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
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/constants.cairo (L65-65)
```text
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
