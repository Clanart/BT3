### Title
Missing Declared-Class Validation in `execute_replace_class` Enables Permanent Freezing of Contract Funds — (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

---

### Summary

The `execute_replace_class` syscall handler in the StarkNet OS accepts any arbitrary felt as the new class hash without verifying it corresponds to a previously declared class. Because the function signature omits the `contract_class_changes` implicit argument, no on-chain validation is possible. Any contract (deployed by an unprivileged user) can replace its own class hash with an undeclared value, permanently making the contract inaccessible and freezing all funds it holds.

---

### Finding Description

`execute_replace_class` in `syscall_impls.cairo` reads `request.class_hash` and writes it directly into `contract_state_changes` with no check that the hash exists in the declared-class registry:

```cairo
func execute_replace_class{
    range_check_ptr,
    syscall_ptr: felt*,
    contract_state_changes: DictAccess*,   // ← only state changes, NOT class changes
    revert_log: RevertLogEntry*,
}(contract_address: felt) {
    ...
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

The function signature does not include `contract_class_changes: DictAccess*`, which is the dictionary that maps class hashes to compiled class hashes. Without it, the OS cannot look up whether the supplied hash is declared. The developer-inserted TODO at line 898 explicitly acknowledges this gap.

Once the state entry is updated with an undeclared class hash, every future call to that contract reaches `execute_entry_point`, which performs:

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

`find_element` will fail to locate the undeclared hash, causing every call to the contract to revert with `ERROR_ENTRY_POINT_NOT_FOUND`. There is no recovery path: the class hash stored in state cannot be changed back because the contract itself is now uncallable.

---

### Impact Explanation

**Critical — Permanent freezing of funds.**

Any ERC-20 balance, ETH, or other asset held inside a contract whose class hash has been replaced with an undeclared value is permanently inaccessible. No transfer, withdrawal, or upgrade function can be invoked because every entry-point lookup fails. The state change is committed to the Merkle trie and cannot be reversed by any protocol mechanism.

---

### Likelihood Explanation

**Medium.**

The entry path is fully reachable by an unprivileged user:

- StarkNet is permissionless; anyone can deploy a contract.
- The `replace_class` syscall is a standard Cairo 1 syscall callable by any contract on itself.
- A malicious deployer can lure users into depositing funds, then invoke a function that calls `replace_class` with an arbitrary undeclared felt.
- A buggy contract (e.g., one that reads the new class hash from user-supplied calldata) can trigger this accidentally.

No privileged role, leaked key, or operator cooperation is required.

---

### Recommendation

Add `contract_class_changes: DictAccess*` as an implicit argument to `execute_replace_class` and perform a `dict_read` to confirm the requested class hash has a non-zero compiled class hash entry before committing the state update. Reject the syscall (write a failure response) if the class hash is undeclared. This is exactly what the existing TODO comment calls for.

---

### Proof of Concept

1. **Deploy** a contract `Vault` at address `A` that accepts user token deposits and exposes a public `freeze()` function containing `replace_class(0xdeadbeef)` where `0xdeadbeef` is never declared.
2. **Users deposit** tokens into `Vault`; the contract's storage now holds balances.
3. **Attacker calls** `freeze()`. The OS executes `execute_replace_class`:
   - `request.class_hash = 0xdeadbeef`
   - No validation is performed (missing `contract_class_changes` argument).
   - `dict_update` writes `class_hash=0xdeadbeef` into `contract_state_changes` for address `A`.
4. **State is committed** to the Patricia trie with the undeclared class hash.
5. **Any subsequent call** to `Vault` reaches `execute_entry_point`:
   - `dict_read(contract_class_changes, key=0xdeadbeef)` returns 0 (undeclared).
   - `find_element(..., key=0)` fails to locate a compiled class fact.
   - Every call reverts with `ERROR_ENTRY_POINT_NOT_FOUND`.
6. **All deposited tokens are permanently frozen** — no withdrawal, transfer, or upgrade is possible.

**Root cause lines:** [1](#0-0) 

**Missing validation acknowledged by TODO:** [2](#0-1) 

**Downstream failure point in `execute_entry_point`:** [3](#0-2)

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
