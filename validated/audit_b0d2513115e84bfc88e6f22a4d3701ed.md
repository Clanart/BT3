### Title
Missing Declared Class Validation in `execute_replace_class` Enables Permanent Contract Freezing — (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

---

### Summary

The `execute_replace_class` syscall handler in the StarkNet OS does not validate that the caller-supplied `class_hash` corresponds to a previously declared class. This is structurally analogous to the reported H-2 bug: a privileged operation (class replacement) accepts an attacker-controlled identifier (class hash) without verifying that the protocol actually owns/controls the referenced resource. The result is that any contract can permanently freeze itself — and all funds it holds — by replacing its class with an undeclared hash, with no OS-level protection preventing it.

---

### Finding Description

In `syscall_impls.cairo`, `execute_replace_class` (lines 878–916) accepts the caller-supplied `request.class_hash` and writes it directly into `contract_state_changes` without checking whether that hash has been declared:

```cairo
func execute_replace_class{...}(contract_address: felt) {
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
    ...
}
```

The TODO comment at line 898 explicitly acknowledges the missing check. [1](#0-0) 

After the syscall succeeds, the contract's class hash in state is permanently set to the undeclared value. In any subsequent block, when `execute_entry_point` is called for this contract, it reads the class hash and calls `dict_read` on `contract_class_changes` to obtain the compiled class hash:

```cairo
let (compiled_class_hash: felt) = dict_read{dict_ptr=contract_class_changes}(
    key=execution_context.class_hash
);
```

Since the class was never declared, `compiled_class_hash` resolves to `0`. The OS then calls `find_element` to locate a `CompiledClassFact` with hash `0`:

```cairo
let (compiled_class_fact: CompiledClassFact*) = find_element(
    array_ptr=compiled_class_facts_bundle.compiled_class_facts,
    elm_size=CompiledClassFact.SIZE,
    n_elms=compiled_class_facts_bundle.n_compiled_class_facts,
    key=compiled_class_hash,
);
```

`find_element` is a hard assertion — it panics if the key is not found. No legitimate `CompiledClassFact` with hash `0` exists. Every future attempt to call the contract causes the OS to abort block execution for that transaction. The contract is permanently unexecutable. [2](#0-1) 

---

### Impact Explanation

**Critical — Permanent freezing of funds.**

Any funds held in a contract whose class hash has been replaced with an undeclared value become permanently inaccessible. The contract cannot execute any entry point (withdraw, transfer, etc.) because the OS cannot resolve the class. The state change is committed to the Merkle tree and cannot be undone without a protocol-level intervention. This matches the allowed impact: *Critical. Permanent freezing of funds.*

---

### Likelihood Explanation

The `replace_class` syscall is available to every Cairo contract. The attack surface includes:

1. **Malicious contract pattern**: An attacker deploys a contract that appears to be a legitimate vault or escrow. Users deposit funds. The attacker then calls `replace_class` with an arbitrary undeclared hash. The vault is permanently frozen and user funds are lost.

2. **Buggy upgrade path**: A legitimate upgradeable contract with insufficient input validation on its upgrade function can be tricked by an unprivileged caller into passing an undeclared class hash to `replace_class`. The OS provides no safety net.

The OS is the final security boundary. Without this validation, any contract — regardless of its own logic — can be permanently frozen at the OS level. No privileged role is required; any contract deployer or user who can invoke `replace_class` (directly or indirectly) can trigger this.

---

### Recommendation

Before writing the new class hash to `contract_state_changes`, verify that the hash is declared by checking `contract_class_changes`:

```cairo
// Verify the class is declared.
let (compiled_class_hash) = dict_read{dict_ptr=contract_class_changes}(key=class_hash);
with_attr error_message("Class hash is not declared.") {
    assert_not_zero(compiled_class_hash);
}
```

This mirrors the check already performed implicitly in `execute_entry_point` but makes it an explicit, enforced precondition in `execute_replace_class`, preventing the state from ever being set to an unresolvable class hash.

---

### Proof of Concept

1. Attacker deploys contract `VaultTrap` with class hash `C_valid` (declared). Users deposit 1000 ETH.
2. `VaultTrap` exposes an `upgrade(new_class: felt)` entry point that calls `replace_class(new_class)` with no access control or validation.
3. Attacker calls `upgrade(0xdeadbeef)` where `0xdeadbeef` is not a declared class hash.
4. The OS executes `execute_replace_class`: gas is deducted, the response header is written with `failure_flag=0`, and `contract_state_changes[VaultTrap_address].class_hash` is set to `0xdeadbeef`. [3](#0-2) 
5. The block is finalized. The Merkle state now records `VaultTrap.class_hash = 0xdeadbeef`.
6. In any subsequent block, any call to `VaultTrap` reaches `execute_entry_point`, which calls `dict_read(key=0xdeadbeef)` → returns `0`, then `find_element(..., key=0)` → panics. [4](#0-3) 
7. The 1000 ETH is permanently frozen. No withdrawal, transfer, or recovery is possible.

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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/execute_entry_point.cairo (L154-177)
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
    let (success, compiled_class_entry_point: CompiledClassEntryPoint*) = get_entry_point(
        compiled_class=compiled_class, execution_context=execution_context
    );

    if (success == 0) {
        %{ ExitCall %}
        let (retdata: felt*) = alloc();
        assert retdata[0] = ERROR_ENTRY_POINT_NOT_FOUND;
        return (is_reverted=1, retdata_size=1, retdata=retdata);
    }
```
