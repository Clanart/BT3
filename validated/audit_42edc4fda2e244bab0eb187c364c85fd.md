### Title
Missing Class Hash Validation in `execute_replace_class` Allows Permanent Freezing of Funds — (File: `execution/syscall_impls.cairo`)

---

### Summary

The `execute_replace_class` syscall handler in the StarkNet OS Cairo program does not verify that the caller-supplied class hash corresponds to a declared contract class. This is the direct analog of the reported "arbitrary pool contract" vulnerability: just as the Lend contract accepted an arbitrary `_pool` address without checking `accreditedAddresses`, the OS accepts an arbitrary `class_hash` in `replace_class` without checking `contract_class_changes`. A contract deployer can exploit this to permanently corrupt a contract's class hash, making the contract permanently unexecutable and freezing any funds held within it.

---

### Finding Description

In `execute_replace_class` (`syscall_impls.cairo`, lines 877–916), the OS reads `request.class_hash` directly from the syscall request and writes it into `contract_state_changes` with no validation that the hash corresponds to a declared class:

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

The TODO comment at line 898 is an explicit acknowledgment by the developers that this check is absent. [2](#0-1) 

The downstream consequence is in `execute_entry_point`. When the now-corrupted contract is later called, the OS performs:

```cairo
let (compiled_class_hash: felt) = dict_read{dict_ptr=contract_class_changes}(
    key=execution_context.class_hash   // ← the attacker-supplied, undeclared hash
);
// compiled_class_hash == 0 (default for undeclared key)

let (compiled_class_fact: CompiledClassFact*) = find_element(
    array_ptr=compiled_class_facts_bundle.compiled_class_facts,
    elm_size=CompiledClassFact.SIZE,
    n_elms=compiled_class_facts_bundle.n_compiled_class_facts,
    key=compiled_class_hash,   // ← find_element(key=0) → hard assertion failure
);
``` [3](#0-2) 

`dict_read` on an undeclared key returns 0 (Cairo dict default). `find_element` is not a soft search — it asserts the element exists. If no compiled class with hash 0 is present in the facts bundle, the OS proof for any block containing a call to the corrupted contract is permanently invalid. The corrupted state is committed on-chain and cannot be repaired because the contract itself is unexecutable.

---

### Impact Explanation

**Critical — Permanent freezing of funds.**

Once a contract's class hash is set to an undeclared value via `replace_class`, the contract is permanently unexecutable at the OS proof level. Any funds (tokens, ETH equivalents) held in that contract's storage are irrecoverably frozen. There is no recovery path: the contract cannot be called to self-repair, and the committed state cannot be rolled back without a protocol-level intervention.

---

### Likelihood Explanation

**High.** The attack requires only the ability to deploy a contract — an action available to any unprivileged user. The attacker:

1. Deploys a contract (e.g., a vault, escrow, or liquidity pool) that accepts user deposits.
2. Embeds a call to `replace_class(0)` (or any undeclared felt value) in a privileged or time-delayed function.
3. After users deposit funds, triggers the `replace_class` call.
4. The OS commits the corrupted class hash to state. The contract is permanently unexecutable.

No privileged role, leaked key, or operator cooperation is required. The entry path is a standard user-submitted transaction.

---

### Recommendation

In `execute_replace_class`, before updating `contract_state_changes`, verify that the requested `class_hash` is present in `contract_class_changes` (i.e., has been declared). The check should mirror how `execute_entry_point` resolves class hashes:

```cairo
// Verify the class hash is declared.
let (compiled_class_hash) = dict_read{dict_ptr=contract_class_changes}(key=class_hash);
with_attr error_message("Class hash is not declared.") {
    assert_not_zero(compiled_class_hash);
}
```

This directly resolves the acknowledged TODO at line 898 of `syscall_impls.cairo`. [4](#0-3) 

---

### Proof of Concept

**Step 1 — Attacker deploys a malicious vault contract** that accepts ETH/token deposits and contains a backdoor function:

```cairo
// MaliciousVault (pseudocode)
@external
func deposit(amount: felt) { ... }  // users deposit funds

@external
func backdoor() {
    // Call replace_class with an undeclared hash (e.g., 0x1337)
    replace_class(class_hash=0x1337);
}
```

**Step 2 — Users deposit funds** into the vault, trusting its interface.

**Step 3 — Attacker calls `backdoor()`**, which issues the `replace_class(0x1337)` syscall.

**Step 4 — OS processes the syscall** via `execute_replace_class`. No validation is performed. `contract_state_changes[vault_address].class_hash` is set to `0x1337`. The state is committed on-chain.

**Step 5 — Any future call to the vault** causes `execute_entry_point` to call `dict_read{dict_ptr=contract_class_changes}(key=0x1337)`, returning 0. `find_element(key=0)` fails. The OS proof for that block is invalid. The vault is permanently unexecutable.

**Step 6 — All user funds are permanently frozen.** The vault cannot be called to withdraw, transfer, or recover any assets.

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
