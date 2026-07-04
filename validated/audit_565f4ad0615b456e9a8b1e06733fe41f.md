### Title
Missing Zero-Value Validation in `execute_replace_class` Allows Permanent Freezing of Contract Funds — (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

---

### Summary

The `execute_replace_class` syscall handler in the StarkNet OS does not validate that the caller-supplied `class_hash` is non-zero (i.e., not `UNINITIALIZED_CLASS_HASH = 0`) and does not verify that the class has been declared. This is directly analogous to the external report's `ecrecover`-returns-zero flaw: just as a missing zero-address check allowed operating on "unowned" NFTs, a missing zero-class-hash check here allows a contract to set its own class to the uninitialized sentinel value `0`, permanently bricking itself and freezing any funds it holds.

---

### Finding Description

In `execute_replace_class` (syscall_impls.cairo), the OS reads the new class hash directly from the syscall request and writes it into the contract state without any validation:

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
``` [1](#0-0) 

The `UNINITIALIZED_CLASS_HASH` sentinel is defined as `0`: [2](#0-1) 

When a contract calls `replace_class(class_hash=0)`, the OS writes `class_hash=0` into the contract's `StateEntry`. Subsequently, any call to this contract causes `execute_entry_point` to look up the compiled class for hash `0`:

```cairo
let (compiled_class_hash: felt) = dict_read{dict_ptr=contract_class_changes}(
    key=execution_context.class_hash   // = 0
);
// compiled_class_hash = 0 (never declared)
let (compiled_class_fact: CompiledClassFact*) = find_element(
    ...
    key=compiled_class_hash,           // = 0, not in bundle → panic
);
``` [3](#0-2) 

`find_element` panics if the key is absent, making the contract permanently unexecutable. The TODO comment in the OS source explicitly acknowledges the missing check.

---

### Impact Explanation

**Critical — Permanent freezing of funds.**

Any contract that calls `replace_class(0)` — whether intentionally by a malicious actor who controls the contract, or accidentally due to a bug in the contract — has its class hash set to `UNINITIALIZED_CLASS_HASH`. All subsequent calls to that contract fail at the OS level. Any ERC-20 balances, ETH, or other assets held in the contract's storage become permanently inaccessible. Because the state commitment is updated with `class_hash=0`, the freeze is irreversible on-chain.

---

### Likelihood Explanation

The `replace_class` syscall is callable by any Cairo 1 contract from within its own execution context. No privileged role is required. A contract author (or an attacker who can trigger a code path in a victim contract that calls `replace_class`) can supply `class_hash=0`. The OS Cairo code imposes no guard, and the explicit TODO comment confirms the check is absent from the production code path.

---

### Recommendation

Add an explicit non-zero check and a declared-class check inside `execute_replace_class`, immediately after reading `class_hash` from the request:

```cairo
let class_hash = request.class_hash;

// Reject the uninitialized sentinel.
with_attr error_message("replace_class: class_hash must not be zero.") {
    assert_not_zero(class_hash);
}

// Verify the class has been declared (compiled_class_hash != 0).
let (compiled_class_hash) = dict_read{dict_ptr=contract_class_changes}(key=class_hash);
with_attr error_message("replace_class: class_hash is not declared.") {
    assert_not_zero(compiled_class_hash);
}
```

This mirrors the fix recommended in the external report (adding a zero-address guard after `ecrecover`) and is consistent with the check already present in `execute_declare_transaction`: [4](#0-3) 

---

### Proof of Concept

1. Attacker deploys a contract `C` that holds user funds (e.g., an ERC-20 vault).
2. Attacker (or a code path in `C`) calls the `replace_class` syscall with `class_hash = 0`.
3. The OS `execute_replace_class` handler writes `StateEntry(class_hash=0, ...)` for contract `C` into `contract_state_changes` with no validation.
4. The state is committed; `C`'s class hash is now `0` on-chain.
5. Any subsequent transaction that calls `C` reaches `execute_entry_point`, which does `dict_read(key=0)` → `compiled_class_hash=0`, then `find_element(..., key=0)` → panic (class not in bundle).
6. All calls to `C` revert permanently. All funds stored in `C`'s storage are frozen with no recovery path. [5](#0-4) [6](#0-5) [2](#0-1)

### Citations

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo (L878-916)
```text
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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/state/commitment.cairo (L16-16)
```text
const UNINITIALIZED_CLASS_HASH = 0;
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/execute_entry_point.cairo (L153-167)
```text
    alloc_locals;
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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L816-819)
```text
    assert_not_zero(compiled_class_hash);
    dict_update{dict_ptr=contract_class_changes}(
        key=[class_hash_ptr], prev_value=0, new_value=compiled_class_hash
    );
```
