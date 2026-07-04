### Title
Unverified Class Hash in `execute_replace_class` Allows Permanent Freezing of Contract Funds — (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

---

### Summary

The `execute_replace_class` syscall handler in the StarkNet OS accepts any arbitrary class hash without verifying that the hash corresponds to a previously declared class. This mirrors the TREC-2 pattern exactly: an assumption is made (that the caller supplies a valid, declared class hash) without an explicit enforcement check. A contract can replace its own class hash with an undeclared value, permanently corrupting its state and freezing any funds it holds.

---

### Finding Description

In `execute_replace_class`, the OS reads the requested new `class_hash` from the syscall request and immediately writes it into `contract_state_changes` without checking whether that hash has ever been declared on-chain:

```cairo
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

The TODO comment at line 898 is the codebase's own acknowledgment that this check is missing. The assumption is that callers will only supply declared class hashes — but this is never enforced.

When a subsequent transaction targets this contract, `execute_entry_point` performs:

```cairo
let (compiled_class_hash: felt) = dict_read{dict_ptr=contract_class_changes}(
    key=execution_context.class_hash
);
// ...
let (compiled_class_fact: CompiledClassFact*) = find_element(
    array_ptr=compiled_class_facts_bundle.compiled_class_facts,
    ...
    key=compiled_class_hash,
);
``` [2](#0-1) 

`find_element` asserts the key exists in the compiled class facts bundle. If the class hash is undeclared, `dict_read` on `contract_class_changes` returns 0 (the uninitialized default), and `find_element` fails to locate a compiled class with hash 0, causing an OS-level proof failure. The contract becomes permanently inaccessible.

---

### Impact Explanation

**Critical — Permanent freezing of funds.**

Any funds (ERC-20 tokens, ETH, or other assets) held by a contract whose class hash has been replaced with an undeclared value are permanently frozen. The contract cannot be called, cannot transfer funds out, and cannot be recovered. The state corruption is committed to the proven state update and is irreversible.

---

### Likelihood Explanation

**Medium-High.** The attack path requires only the ability to deploy a contract and submit a transaction — both are available to any unprivileged user. A realistic scenario:

1. Attacker deploys a contract that appears legitimate (e.g., a shared vault or token contract).
2. Users deposit funds into it.
3. Attacker submits a transaction that calls `replace_class` with an arbitrary, undeclared felt value as the class hash.
4. The OS processes this without any check, writing the invalid hash into state.
5. All subsequent calls to the contract fail at the OS level; funds are permanently frozen.

No privileged access, leaked keys, or operator cooperation is required.

---

### Recommendation

Add an explicit check in `execute_replace_class` that the supplied `class_hash` corresponds to a declared class. This can be done by verifying that `contract_class_changes` contains a non-zero entry for the given hash (i.e., the class was previously declared via a `declare` transaction), analogous to the check already enforced in `execute_declare_transaction`:

```cairo
// In execute_declare_transaction (existing pattern):
assert_not_zero(compiled_class_hash);
dict_update{dict_ptr=contract_class_changes}(
    key=[class_hash_ptr], prev_value=0, new_value=compiled_class_hash
);
``` [3](#0-2) 

For `replace_class`, the OS should perform a `dict_read` on `contract_class_changes` for the requested `class_hash` and assert the result is non-zero before allowing the replacement. The existing TODO at line 898 of `syscall_impls.cairo` already identifies this as a required fix.

---

### Proof of Concept

1. Declare a legitimate class `C` and deploy contract `V` (vault) using class `C`. Users deposit funds into `V`.
2. Attacker submits an invoke transaction targeting `V` (or a contract they control that calls `replace_class` on itself after receiving user deposits).
3. During execution, the contract calls the `replace_class` syscall with `class_hash = 0xdeadbeef` (any value not corresponding to a declared class).
4. `execute_replace_class` in `syscall_impls.cairo` writes `StateEntry(class_hash=0xdeadbeef, ...)` into `contract_state_changes` with no validation.
5. The state update is proven and committed on-chain.
6. Any future transaction targeting `V` reaches `execute_entry_point`, which calls `dict_read{dict_ptr=contract_class_changes}(key=0xdeadbeef)` → returns 0 → `find_element(..., key=0)` → assertion failure.
7. `V` is permanently inaccessible; all deposited funds are frozen. [4](#0-3) [5](#0-4)

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
