### Title
Missing u64 Validation on `request_block_number` Causes Unresolvable OS Proof Failure — (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

---

### Summary

`execute_get_block_hash` reads `request.block_number` directly from the user-supplied syscall request and performs arithmetic on it under the assumption it is a u64. No Cairo assertion enforces this bound. When a user supplies a felt value near the field prime P (e.g., `P − 5`), the arithmetic `request_block_number + STORED_BLOCK_HASH_BUFFER` wraps modulo P to a tiny value, making every branch of the range-check logic produce an unsatisfiable assertion. The prover cannot generate a valid OS proof for any block containing such a transaction, causing a permanent network halt.

---

### Finding Description

In `execute_get_block_hash`, the code reads the block number from the syscall request and immediately uses it in arithmetic:

```cairo
let request_block_number = request.block_number;
let current_block_number = block_context.block_info_for_execute.block_number;

// A block number is a u64. STORED_BLOCK_HASH_BUFFER is 10.
// The following computations will not overflow.
local is_block_number_in_block_hash_buffer;
%{ IsBlockNumberInBlockHashBuffer %}
if (is_block_number_in_block_hash_buffer != FALSE) {
    assert_lt(current_block_number, request_block_number + STORED_BLOCK_HASH_BUFFER);
    ...
    return ();
}
assert_nn_le(request_block_number, current_block_number - STORED_BLOCK_HASH_BUFFER);
```

The comment "A block number is a u64" is documentation only — there is no `assert_nn_le(request_block_number, 2**64 - 1)` or equivalent assertion anywhere in this function. [1](#0-0) 

`request.block_number` is a Cairo `felt`, so a user-controlled contract can pass any value in `[0, P−1]`. Consider `request_block_number = P − 5`:

**Branch 1 — hint = TRUE (prover sets `is_block_number_in_block_hash_buffer ≠ 0`):**

```
request_block_number + STORED_BLOCK_HASH_BUFFER
  = (P − 5) + 10  ≡  5  (mod P)
```

`assert_lt(current_block_number, 5)` fails for any `current_block_number ≥ 5`, which is true for every block after genesis.

**Branch 2 — hint = FALSE (prover sets `is_block_number_in_block_hash_buffer = 0`):**

```
assert_nn_le(P − 5, current_block_number − 10)
```

`assert_nn_le` uses the range-check builtin, which requires its first argument to be in `[0, 2^128)`. `P − 5 ≈ 2^251` violates this, so the assertion fails unconditionally.

In both branches the assertion is unsatisfiable. The prover cannot produce a valid execution trace for any block that includes this transaction. [2](#0-1) 

The root cause is the same vulnerability class as the external report: an arithmetic operation that silently assumes its operand is within a bounded range (u64) but never enforces that bound, producing incorrect or unsatisfiable results at edge-case inputs.

---

### Impact Explanation

**High — Network not being able to confirm new transactions (total network shutdown).**

When the OS proof fails, the block cannot be submitted to L1. The sequencer must re-sequence the block, but if it cannot identify and exclude the offending transaction (or if the off-chain executor does not pre-validate `block_number` as a u64), the network stalls. Even a single such transaction included in a block is sufficient to make that block unprovable.

---

### Likelihood Explanation

Any unprivileged user can deploy a contract containing:

```cairo
get_block_hash(P - 5)  // or any felt ≥ 2^64
```

and submit a transaction calling it. The off-chain executor runs the contract and receives a normal syscall-error response (the contract does not revert). The sequencer has no protocol-level reason to reject the transaction. The proof failure only manifests during OS proof generation, after the block has been sequenced.

The Cairo OS code itself carries no u64 guard on `request_block_number`, so the off-chain executor is the only line of defense — and it may rely on the same undocumented assumption.

---

### Recommendation

Add an explicit u64 range check on `request_block_number` at the top of `execute_get_block_hash`, before any arithmetic is performed:

```cairo
assert_nn_le(request_block_number, 2 ** 64 - 1);
```

This mirrors the pattern already used for `max_amount` in `pack_resource_bounds`:

```cairo
assert_nn_le(resource_bounds.max_amount, 2 ** 64 - 1);
``` [3](#0-2) 

---

### Proof of Concept

1. Deploy a Cairo contract with an entry point that calls `get_block_hash(P - 5)` where `P` is the Cairo field prime (`0x800000000000011000000000000000000000000000000000000000000000001`).
2. Submit an invoke transaction calling that entry point.
3. The sequencer's off-chain executor runs the contract; the syscall returns an error response; the transaction does not revert; the sequencer includes it in a block.
4. The prover attempts to generate the OS proof for the block.
5. Inside `execute_get_block_hash`:
   - `request_block_number = P − 5`
   - Prover sets hint `is_block_number_in_block_hash_buffer = TRUE`
   - `assert_lt(current_block_number, (P−5)+10 mod P)` = `assert_lt(current_block_number, 5)` — **fails** for any `current_block_number ≥ 5`
   - Alternatively, prover sets hint = FALSE: `assert_nn_le(P−5, ...)` — **fails** because `P−5 > 2^128`
6. The OS proof cannot be generated. The block is permanently stuck. The network cannot confirm new transactions. [4](#0-3)

### Citations

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo (L720-753)
```text
// Gets the block hash of the block at given block number.
func execute_get_block_hash{
    range_check_ptr, syscall_ptr: felt*, contract_state_changes: DictAccess*
}(block_context: BlockContext*) {
    alloc_locals;
    let request = cast(syscall_ptr + RequestHeader.SIZE, GetBlockHashRequest*);

    // Reduce gas.
    let (success, remaining_gas) = reduce_syscall_base_gas(
        specific_base_gas_cost=GET_BLOCK_HASH_GAS_COST, request_struct_size=GetBlockHashRequest.SIZE
    );
    if (success == FALSE) {
        // Not enough gas to execute the syscall; in that case, 'reduce_syscall_base_gas' already
        // wrote the response objects and advanced the syscall pointer.
        return ();
    }

    // Handle out of range block number.
    let request_block_number = request.block_number;
    let current_block_number = block_context.block_info_for_execute.block_number;

    // A block number is a u64. STORED_BLOCK_HASH_BUFFER is 10.
    // The following computations will not overflow.
    local is_block_number_in_block_hash_buffer;
    %{ IsBlockNumberInBlockHashBuffer %}
    if (is_block_number_in_block_hash_buffer != FALSE) {
        assert_lt(current_block_number, request_block_number + STORED_BLOCK_HASH_BUFFER);
        write_failure_response(
            remaining_gas=remaining_gas, failure_felt=ERROR_BLOCK_NUMBER_OUT_OF_RANGE
        );
        return ();
    }

    assert_nn_le(request_block_number, current_block_number - STORED_BLOCK_HASH_BUFFER);
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/transaction_hash/transaction_hash.cairo (L104-104)
```text
    assert_nn_le(resource_bounds.max_amount, 2 ** 64 - 1);
```
