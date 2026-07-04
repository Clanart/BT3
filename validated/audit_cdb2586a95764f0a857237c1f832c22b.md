### Title
Arithmetic Underflow in `check_proof_facts` Causes OS Execution Failure When `current_block_number < STORED_BLOCK_HASH_BUFFER` — (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/execution_constraints.cairo`)

---

### Summary

In `execution_constraints.cairo`, the function `check_proof_facts` performs the subtraction `current_block_number - STORED_BLOCK_HASH_BUFFER` without first verifying that `current_block_number >= STORED_BLOCK_HASH_BUFFER`. When the network is young (block number below the buffer threshold), this subtraction wraps around in the felt field to a value near the prime `P ≈ 2^251`. The subsequent `assert_nn_le` range-check then fails at the VM level — not as a transaction revert, but as an OS-level proof generation failure — halting the entire block from being proven.

---

### Finding Description

In `check_proof_facts` (line 66–68):

```cairo
assert_nn_le(
    os_output_header.base_block_number, current_block_number - STORED_BLOCK_HASH_BUFFER
);
```

`assert_nn_le(a, b)` expands to:
1. `assert_nn(a)` — writes `a` to the range-check segment, verifying `a ∈ [0, 2^128)`.
2. `assert_le(a, b)` → `assert_nn(b - a)` — writes `b - a` to the range-check segment, verifying `b - a ∈ [0, 2^128)`.

When `current_block_number < STORED_BLOCK_HASH_BUFFER`, the felt subtraction wraps around:

```
b = current_block_number - STORED_BLOCK_HASH_BUFFER
  = P - (STORED_BLOCK_HASH_BUFFER - current_block_number)   // ≈ 2^251
```

Then `b - a ≈ P - STORED_BLOCK_HASH_BUFFER - base_block_number`, which is also near `P`. Since `P >> 2^128`, the range-check builtin rejects this value. This is **not** a transaction-level revert — it is a Cairo VM assertion failure that aborts the entire OS execution, making the block unprovable.

Compare this with `write_block_number_to_block_hash_mapping` in `os_utils.cairo` (lines 52–57), which performs the **identical subtraction** but correctly guards it:

```cairo
tempvar old_block_number = block_context.block_info_for_execute.block_number -
    STORED_BLOCK_HASH_BUFFER;
let is_old_block_number_non_negative = is_nn(old_block_number);
if (is_old_block_number_non_negative == FALSE) {
    return ();
}
```

`check_proof_facts` has **no such guard**.

---

### Impact Explanation

When the OS execution aborts, the block cannot be proven. The sequencer must re-build the block excluding the offending transaction. If an attacker continuously submits crafted transactions with non-empty `proof_facts` during the vulnerable window, every block that includes one of these transactions will fail to prove, preventing the network from confirming any new transactions.

**Impact: High — Network not being able to confirm new transactions (total network shutdown).**

---

### Likelihood Explanation

The attack is reachable by any unprivileged transaction sender:

1. The `proof_facts` field is part of the invoke transaction calldata, fully controlled by the sender.
2. The checks before the vulnerable line require only publicly known constants (`PROOF_VERSION`, `VIRTUAL_SNOS`, `ALLOWED_VIRTUAL_OS_PROGRAM_HASHES_0`, `VIRTUAL_OS_OUTPUT_VERSION`) — all readable from the deployed contract or source.
3. The sequencer executes transactions via the blockifier (Rust), which does not run the Cairo OS. The blockifier may not replicate the OS-level `assert_nn_le` check, so the transaction passes blockifier validation and is included in the block.
4. The vulnerability window is the first `STORED_BLOCK_HASH_BUFFER` blocks of the network — a predictable and exploitable period.

---

### Recommendation

Mirror the guard used in `write_block_number_to_block_hash_mapping`. Before performing the subtraction, verify that `current_block_number >= STORED_BLOCK_HASH_BUFFER`:

```cairo
// In check_proof_facts, before assert_nn_le:
let is_block_old_enough = is_nn(current_block_number - STORED_BLOCK_HASH_BUFFER);
if (is_block_old_enough == FALSE) {
    // Proof facts cannot reference a block hash that hasn't been stored yet.
    // Reject any transaction that supplies proof_facts during early blocks.
    assert proof_facts_size = 0;
    return ();
}
assert_nn_le(
    os_output_header.base_block_number, current_block_number - STORED_BLOCK_HASH_BUFFER
);
```

Alternatively, add an explicit pre-check:

```cairo
assert_nn_le(STORED_BLOCK_HASH_BUFFER, current_block_number);
assert_nn_le(
    os_output_header.base_block_number, current_block_number - STORED_BLOCK_HASH_BUFFER
);
```

---

### Proof of Concept

**Attacker steps (during early blocks, `current_block_number < STORED_BLOCK_HASH_BUFFER`):**

1. Read the public constants from the deployed OS: `PROOF_VERSION`, `VIRTUAL_SNOS`, `ALLOWED_VIRTUAL_OS_PROGRAM_HASHES_0`, `VIRTUAL_OS_OUTPUT_VERSION`.
2. Craft an invoke transaction with `proof_facts` set to a byte sequence that satisfies:
   - `proof_facts_size >= ProofHeader.SIZE + VirtualOsOutputHeader.SIZE`
   - `proof_header.proof_version = PROOF_VERSION`
   - `proof_header.proof_variant = VIRTUAL_SNOS`
   - `proof_header.program_hash = ALLOWED_VIRTUAL_OS_PROGRAM_HASHES_0`
   - `os_output_header.output_version = VIRTUAL_OS_OUTPUT_VERSION`
   - `os_output_header.base_block_number = 0` (any small value)
   - `os_output_header.base_block_hash = 1` (any non-zero value)
3. Submit the transaction. The blockifier accepts it (no OS-level range-check validation).
4. The sequencer includes it in a block.
5. During proof generation, the OS reaches:
   ```cairo
   // current_block_number = e.g. 5, STORED_BLOCK_HASH_BUFFER = e.g. 10
   // b = 5 - 10