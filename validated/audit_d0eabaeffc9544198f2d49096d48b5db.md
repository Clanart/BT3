### Title
Missing Class Hash Validation in `execute_replace_class` Allows Breaking the Declared-Class Invariant — (`execution/syscall_impls.cairo`)

---

### Summary

The `execute_replace_class` syscall in the StarkNet OS does not validate that the replacement class hash is a declared class. Every other code path that sets a contract's class hash enforces this invariant, but `execute_replace_class` skips it — an explicit `TODO` in the source acknowledges the gap. An unprivileged contract can exploit this to set its class hash to an arbitrary undeclared value, causing `execute_entry_point` to panic when it later tries to look up the compiled class, making the block unprovable and halting the network.

---

### Finding Description

**The invariant:** Every contract's `class_hash` field must correspond to a class that has been declared (i.e., present in `contract_class_changes`). This is enforced at execution time in `execute_entry_point`: [1](#0-0) 

```cairo
let (compiled_class_hash: felt) = dict_read{dict_ptr=contract_class_changes}(
    key=execution_context.class_hash
);
...
let (compiled_class_fact: CompiledClassFact*) = find_element(
    array_ptr=compiled_class_facts_bundle.compiled_class_facts,
    elm_size=CompiledClassFact.SIZE,
    n_elms=compiled_class_facts_bundle.n_compiled_class_facts,
    key=compiled_class_hash,
);
```

If `compiled_class_hash` is `0` (the default for an undeclared class), `find_element` will panic — it is not a graceful revert, it is a Cairo assertion failure that makes the entire block unprovable.

**Where the invariant is enforced in other paths:**

- `execute_declare_transaction` explicitly validates the class hash via `finalize_class_hash()` and writes it into `contract_class_changes` with `prev_value=0` to prevent re-declaration: [2](#0-1) 

- `deploy_contract` asserts the contract is uninitialized before setting its class hash: [3](#0-2) 

**Where the invariant is missing — `execute_replace_class`:** [4](#0-3) 

```cairo
let class_hash = request.class_hash;

// TODO(Yoni, 1/1/2026): Check that there is a declared contract class with the given hash.
local state_entry: StateEntry*;
%{ GetContractAddressStateEntry %}

tempvar new_state_entry = new StateEntry(
    class_hash=class_hash, storage_ptr=state_entry.storage_ptr, nonce=state_entry.nonce
);

dict_update{dict_ptr=contract_state_changes}(
    key=contract_address,
    prev_value=cast(state_entry, felt),
    new_value=cast(new_state_entry, felt),
);
```

The `class_hash` from the request is written directly into `contract_state_changes` with **no check** that it exists in `contract_class_changes`. The `TODO` comment at line 898 explicitly acknowledges this missing validation.

---

### Impact Explanation

**Impact: High — Network not being able to confirm new transactions (total network shutdown).**

When a contract calls `replace_class(X)` where `X` is an undeclared class hash:

1. `contract_state_changes[contract_address].class_hash` is set to `X`.
2. Any subsequent call to that contract in the same block (or a future block before the state is corrected) reaches `execute_entry_point`.
3. `dict_read{dict_ptr=contract_class_changes}(key=X)` returns `0` (undeclared class → no entry).
4. `find_element(..., key=0)` searches `compiled_class_facts_bundle` for a compiled class with hash `0`. If absent, Cairo panics.
5. The OS execution aborts. The block cannot be proven. The network halts.

---

### Likelihood Explanation

- **Attacker-controlled entry path:** Any deployed contract can issue the `replace_class` syscall with an arbitrary `class_hash` value. No privileged role is required.
- **Trigger condition:** The attacker only needs to ensure a second transaction in the same block calls the modified contract. This is trivially achievable by the attacker themselves or by any other user interacting with the contract.
- **Sequencer gap:** The TODO comment in the OS code strongly implies the sequencer's off-chain execution also lacks this check (otherwise the OS check would be redundant). If the sequencer does not reject the transaction, it will be included in a block and the prover will fail.

---

### Recommendation

In `execute_replace_class`, before writing the new class hash to `contract_state_changes`, verify that the class hash is present in `contract_class_changes` (i.e., it has been declared). This mirrors the implicit requirement already enforced by `execute_entry_point`. Concretely, perform a `dict_read` on `contract_class_changes` with `key=class_hash` and assert the result is non-zero (a declared class always has a non-zero compiled class hash).

---

### Proof of Concept

```
1. Attacker deploys contract A (class hash C, which is declared).
2. Contract A's logic calls replace_class(X) where X is any undeclared felt value.
   - execute_replace_class writes X into contract_state_changes[A].class_hash.
   - No validation is performed. The syscall succeeds.
3. In the same block (or a later block), any transaction calls contract A.
4. execute_entry_point runs:
     compiled_class_hash = dict_read(contract_class_changes, key=X)
     // X was never declared → compiled_class_hash = 0
     find_element(..., key=0)
     // No compiled class with hash 0 exists → Cairo panic
5. OS execution aborts. Block is unprovable. Network halts.
``` [5](#0-4) [6](#0-5) [7](#0-6)

### Citations

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/execute_entry_point.cairo (L154-167)
```text
    let (compiled_class_hash: felt) = dict_read{dict_ptr=contract_class_changes}(
        key=execution_context.class_hash
    );

    // The key must be at offset 0.
    static_assert CompiledClassFact.hash == 0;
    let compiled_class_facts_bundle = block_context.os_global_context.compiled_class_facts_bundle;
    let (compiled_class_fact: CompiledClassFact*) = find_element(
        array_ptr=compiled_class_facts_bundle.compiled_class_facts,
        elm_size=CompiledClassFact.SIZE,
        n_elms=compiled_class_facts_bundle.n_compiled_class_facts,
        key=compiled_class_hash,
    );
    local compiled_class: CompiledClass* = compiled_class_fact.compiled_class;
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L738-743)
```text
        let expected_class_hash = finalize_class_hash(
            contract_class_component_hashes=contract_class_component_hashes
        );
        with_attr error_message("Invalid class hash pre-image.") {
            assert [class_hash_ptr] = expected_class_hash;
        }
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L814-819)
```text
    // Declare the class hash.
    // Note that prev_value=0 enforces that a class may be declared only once.
    assert_not_zero(compiled_class_hash);
    dict_update{dict_ptr=contract_class_changes}(
        key=[class_hash_ptr], prev_value=0, new_value=compiled_class_hash
    );
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/deploy_contract.cairo (L53-53)
```text
    assert state_entry.class_hash = UNINITIALIZED_CLASS_HASH;
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo (L877-916)
```text
// Replaces the class.
func execute_replace_class{
    range_check_ptr,
    syscall_ptr: felt*,
    contract_state_changes: DictAccess*,
    revert_log: RevertLogEntry*,
}(contract_address: felt) {
    alloc_locals;
    let request = cast(syscall_ptr + RequestHeader.SIZE, ReplaceClassRequest*);

    // Reduce gas.
    let success = reduce_syscall_gas_and_write_response_header(
        total_gas_cost=REPLACE_CLASS_GAS_COST, request_struct_size=ReplaceClassRequest.SIZE
    );
    if (success == FALSE) {
        // Not enough gas to execute the syscall.
        return ();
    }

    let class_hash = request.class_hash;

    // TODO(Yoni, 1/1/2026): Check that there is a declared contract class with the given hash.
    local state_entry: StateEntry*;
    %{ GetContractAddressStateEntry %}

    tempvar new_state_entry = new StateEntry(
        class_hash=class_hash, storage_ptr=state_entry.storage_ptr, nonce=state_entry.nonce
    );

    dict_update{dict_ptr=contract_state_changes}(
        key=contract_address,
        prev_value=cast(state_entry, felt),
        new_value=cast(new_state_entry, felt),
    );

    assert [revert_log] = RevertLogEntry(selector=CHANGE_CLASS_ENTRY, value=state_entry.class_hash);
    let revert_log = &revert_log[1];

    return ();
}
```
