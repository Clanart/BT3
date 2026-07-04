### Title
Arithmetic Underflow in `check_proof_facts()` Causes OS Proof Failure During Early Blocks - (File: `execution/execution_constraints.cairo`)

---

### Summary

`check_proof_facts()` in `execution_constraints.cairo` performs an unchecked subtraction `current_block_number - STORED_BLOCK_HASH_BUFFER` before passing the result to `assert_nn_le`. When `current_block_number < STORED_BLOCK_HASH_BUFFER` (i.e., during the first 10 blocks), this subtraction wraps around in the Cairo prime field, producing a value near the prime (~2²⁵¹). The subsequent `assert_nn_le` call then fails because the range-check builtin rejects values ≥ 2¹²⁸, making the OS proof unprovable. Any unprivileged invoke transaction sender who includes `proof_facts_size > 0` during this window can trigger this failure.

---

### Finding Description

In `execution_constraints.cairo`, `check_proof_facts` validates that a proof fact's `base_block_number` is not too recent:

```cairo
assert_nn_le(
    os_output_header.base_block_number, current_block_number - STORED_BLOCK_HASH_BUFFER
);
``` [1](#0-0) 

`STORED_BLOCK_HASH_BUFFER` is the constant `10`: [2](#0-1) 

When `current_block_number < 10`, the expression `current_block_number - STORED_BLOCK_HASH_BUFFER` underflows in the Cairo prime field, producing a value of approximately `P - (10 - current_block_number)` where `P` is the Stark prime (~2²⁵¹). Cairo's `assert_nn_le(a, b)` internally calls `assert_nn(b - a)`, which uses the range-check builtin that only accepts values in `[0, 2¹²⁸)`. A value near the prime fails this check, making the OS proof invalid.

The same subtraction pattern using `STORED_BLOCK_HASH_BUFFER` is handled **correctly** in `os_utils.cairo`, where `is_nn` is used to guard against underflow before proceeding:

```cairo
tempvar old_block_number = block_context.block_info_for_execute.block_number -
    STORED_BLOCK_HASH_BUFFER;
let is_old_block_number_non_negative = is_nn(old_block_number);
if (is_old_block_number_non_negative == FALSE) {
    return ();
}
``` [3](#0-2) 

`execution_constraints.cairo` has no such guard.

`check_proof_facts` is called from `execute_invoke_function_transaction` in `transaction_impls.cairo`, where `proof_facts_size` is loaded directly from the transaction data (hint `TxProofFacts`): [4](#0-3) 

It is then passed to `check_proof_facts` at: [5](#0-4) 

The function short-circuits only when `proof_facts_size == 0`: [6](#0-5) 

Any invoke transaction with `proof_facts_size > 0` bypasses this guard and reaches the vulnerable subtraction.

---

### Impact Explanation

When the OS proof fails, the block cannot be finalized on L1. The sequencer cannot produce a valid STARK proof for any block containing such a transaction. This constitutes a **network halt**: no new transactions can be confirmed until the block is discarded or the network is restarted. Impact: **High — Network not being able to confirm new transactions (total network shutdown)**.

---

### Likelihood Explanation

The exploitable window is the first 10 blocks of any network deployment (mainnet, testnet, or re-genesis). During this window, any unprivileged user who submits an invoke transaction with `proof_facts_size > 0` (a valid, user-controlled field) triggers the bug. No special privileges, leaked keys, or operator cooperation are required. The attacker only needs to know the network is in its first 10 blocks and submit a standard invoke transaction with a non-zero `proof_facts` array.

---

### Recommendation

Mirror the safe pattern from `os_utils.cairo`. Before calling `assert_nn_le`, guard the subtraction with an explicit underflow check:

```cairo
// Validate that the proof facts block number is not too recent.
if (current_block_number < STORED_BLOCK_HASH_BUFFER) {
    // Block number too low; no stored hashes exist yet. Reject any proof facts.
    assert 1 = 0;  // or: with_attr error_message(...) { assert ... }
    return ();
}
assert_nn_le(
    os_output_header.base_block_number, current_block_number - STORED_BLOCK_HASH_BUFFER
);
```

Alternatively, use `is_nn` analogously to `os_utils.cairo`:

```cairo
tempvar safe_upper = current_block_number - STORED_BLOCK_HASH_BUFFER;
let is_non_negative = is_nn(safe_upper);
with_attr error_message("Block number too low for proof facts.") {
    assert is_non_negative = TRUE;
}
assert_nn_le(os_output_header.base_block_number, safe_upper);
```

---

### Proof of Concept

**Setup:** Network genesis, `current_block_number = 5`, `STORED_BLOCK_HASH_BUFFER = 10`.

1. Attacker submits an invoke transaction (V3) with `proof_facts_size = ProofHeader.SIZE + VirtualOsOutputHeader.SIZE` and any valid-looking `proof_facts` array (e.g., with a recognized `program_hash`).
2. The sequencer includes this transaction in block 5 and runs the OS to produce a proof.
3. The OS calls `execute_invoke_function_transaction` → `check_proof_facts(proof_facts_size=N, ..., current_block_number=5, ...)`.
4. `proof_facts_size != 0`, so the early-return is skipped.
5. The OS reaches: `assert_nn_le(os_output_header.base_block_number, 5 - 10)`.
6. `5 - 10` in the Cairo field = `P - 5` ≈ 2²⁵¹, a value far outside `[0, 2¹²⁸)`.
7. `assert_nn_le` internally calls `assert_nn(P - 5 - base_block_number)` which fails the range-check constraint.
8. The OS proof is invalid; the block cannot be finalized on L1.
9. The network halts until the block is dropped and the issue is resolved.

### Citations

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/execution_constraints.cairo (L40-42)
```text
    if (proof_facts_size == 0) {
        return ();
    }
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/execution_constraints.cairo (L66-68)
```text
    assert_nn_le(
        os_output_header.base_block_number, current_block_number - STORED_BLOCK_HASH_BUFFER
    );
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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L279-281)
```text
    local proof_facts_size;
    local proof_facts: felt*;
    %{ TxProofFacts %}
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
