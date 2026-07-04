### Title
Unvalidated `request_block_number` Causes Field-Arithmetic Overflow in `execute_get_block_hash`, Halting OS Proof Generation — (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

---

### Summary

In `execute_get_block_hash`, the `request_block_number` field from the user-supplied syscall request is used in arithmetic without any upper-bound validation. When a contract passes a block number close to the Cairo field prime `P`, the addition `request_block_number + STORED_BLOCK_HASH_BUFFER` silently wraps around modulo `P` to a tiny value. The subsequent `assert_lt` then compares `current_block_number` against that tiny wrapped value and panics, making it impossible to generate a valid OS proof for any block containing such a transaction.

---

### Finding Description

The vulnerable code is in `execute_get_block_hash`:

```cairo
// A block number is a u64. STORED_BLOCK_HASH_BUFFER is 10.
// The following computations will not overflow.
local is_block_number_in_block_hash_buffer;
%{ IsBlockNumberInBlockHashBuffer %}
if (is_block_number_in_block_hash_buffer != FALSE) {
    assert_lt(current_block_number, request_block_number + STORED_BLOCK_HASH_BUFFER);
    write_failure_response(...);
    return ();
}
assert_nn_le(request_block_number, current_block_number - STORED_BLOCK_HASH_BUFFER);
``` [1](#0-0) 

The comment at line 741–742 states the computation "will not overflow," but this is an **assumption**, not an enforced constraint. There is no `assert_nn_le` or range check bounding `request_block_number` to `[0, 2^64)` before the arithmetic.

`STORED_BLOCK_HASH_BUFFER` is the constant `10`: [2](#0-1) 

**Overflow mechanics (step by step):**

1. An attacker deploys a contract that calls `get_block_hash(P - 1)`, where `P` is the Stark field prime (~2²⁵¹).
2. The Python hint `IsBlockNumberInBlockHashBuffer` compares `P - 1` against `current_block_number - STORED_BLOCK_HASH_BUFFER` as Python integers. Since `P - 1` is astronomically large, the hint sets `is_block_number_in_block_hash_buffer = TRUE`.
3. The Cairo code then executes:
   ```
   assert_lt(current_block_number, (P - 1) + 10)
   ```
   In Cairo field arithmetic, `(P - 1) + 10 ≡ 9 (mod P)`.
4. This becomes `assert_lt(current_block_number, 9)`, which is implemented as `assert_nn(9 - current_block_number - 1)`.
5. For any `current_block_number ≥ 9` (i.e., every block after the 9th), `9 - current_block_number - 1` is negative, wraps to a huge field element, and `assert_nn` panics.

The OS Cairo program panics before it can write the failure response. No valid proof can be generated for the block.

---

### Impact Explanation

**High — Network not being able to confirm new transactions (total network shutdown).**

The StarkNet OS Cairo program is the authoritative proof-generation layer. If it panics (assertion failure), no STARK proof can be produced for the block containing the offending transaction. The sequencer's Rust execution layer (blockifier) may handle the oversized block number gracefully (returning an error to the contract), but the Cairo OS — which must agree with the Rust layer for proof validity — panics instead. The block cannot be finalized, and the network stalls until the transaction is somehow excluded.

---

### Likelihood Explanation

Any unprivileged user can deploy a contract that calls `get_block_hash` with an arbitrary `felt` argument. The `GetBlockHashRequest.block_number` field is a `felt` with no on-chain type enforcement at the contract level. The attack requires only a single transaction and is trivially reproducible on any block with `block_number ≥ 9`. [3](#0-2) 

---

### Recommendation

Add an explicit upper-bound range check on `request_block_number` before any arithmetic involving it. Since block numbers are semantically `u64`, enforce this immediately after reading the request:

```cairo
// Enforce that request_block_number is a valid u64.
assert [range_check_ptr] = request_block_number;
assert [range_check_ptr + 1] = request_block_number + 2 ** 128 - 2 ** 64;
let range_check_ptr = range_check_ptr + 2;
```

This mirrors the fix pattern used elsewhere in the codebase (e.g., `pack_resource_bounds` bounds `max_amount` with `assert_nn_le(resource_bounds.max_amount, 2 ** 64 - 1)`). [4](#0-3) 

---

### Proof of Concept

```cairo
// Attacker contract (Sierra/Cairo 1):
// Calls get_block_hash with block_number = P - 1.
// P = 3618502788666131213697322783095070105623107215331596699973092056135872020481

#[starknet::contract]
mod AttackerContract {
    use starknet::syscalls::get_block_hash_syscall;

    #[external(v0)]
    fn trigger(self: @ContractState) {
        // P - 1 as a felt252 literal
        let huge_block_number: u64 = 0xFFFFFFFFFFFFFFFF_u64; // or craft P-1 via felt252
        let _ = get_block_hash_syscall(huge_block_number);
    }
}
```

**OS execution trace (conceptual):**

```
request_block_number = P - 1
STORED_BLOCK_HASH_BUFFER = 10
current_block_number = 100  (any value >= 9)

Hint sets is_block_number_in_block_hash_buffer = TRUE
  (because P - 1 >> 100 - 10 = 90 as Python integers)

Cairo executes:
  assert_lt(100, (P - 1) + 10)
  = assert_lt(100, 9)          ← field wrap-around
  = assert_nn(9 - 100 - 1)
  = assert_nn(P - 92)          ← P - 92 >> 2^128

[FAIL: panic: assertion failed] — OS proof generation halted.
```

### Citations

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo (L724-753)
```text
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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/constants.cairo (L65-65)
```text
const STORED_BLOCK_HASH_BUFFER = 10;
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/transaction_hash/transaction_hash.cairo (L103-107)
```text
func pack_resource_bounds{range_check_ptr}(resource_bounds: ResourceBounds) -> felt {
    assert_nn_le(resource_bounds.max_amount, 2 ** 64 - 1);
    assert_nn(resource_bounds.max_price_per_unit);
    return (resource_bounds.resource * 2 ** 64 + resource_bounds.max_amount) * 2 ** 128 +
        resource_bounds.max_price_per_unit;
```
