### Title
Missing Declared Class Hash Validation in `execute_replace_class` Enables Permanent Contract Freezing - (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

---

### Summary

The `execute_replace_class` function in `syscall_impls.cairo` does not validate that the new class hash supplied to the `replace_class` syscall corresponds to a previously declared class. This allows any contract to replace its own class hash with an arbitrary undeclared value, creating an unresolvable state where the contract becomes permanently unexecutable and any funds it holds are permanently frozen.

---

### Finding Description

In `execute_replace_class`, the OS processes the `replace_class` syscall by directly updating the contract's class hash in `contract_state_changes` without verifying that the new class hash is a declared class. The code itself contains a TODO comment explicitly acknowledging this missing check:

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

When a contract calls `replace_class(undeclared_hash)`, the OS commits `class_hash = undeclared_hash` to the contract's state entry. In any subsequent block, when a transaction attempts to call this contract, `execute_entry_point` reads the class hash from state and performs:

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

Because `undeclared_hash` was never declared, `dict_read` on `contract_class_changes` returns the default value `0` (the dict is initialized via `dict_new()` with default 0). [3](#0-2) 

`find_element` then searches for a compiled class with hash `0`. Since no such compiled class exists for an honest prover, `find_element` fails, making the entire block unprovable. The sequencer is forced to exclude all calls to the affected contract from every future block, permanently freezing it.

This is the direct analog of the external report's circular dependency: just as `RemoteOwner` and `RngAuctionRelayerRemoteOwner` could each be left in an unresolvable initialization state, a contract whose class hash is replaced with an undeclared value is left in an unresolvable execution state — it can never be called again.

---

### Impact Explanation

**Critical — Permanent freezing of funds.**

Any funds held in a contract whose class hash has been replaced with an undeclared hash become permanently inaccessible. No future block can include a call to that contract without becoming unprovable. The contract is effectively bricked at the protocol level, with no recovery path.

---

### Likelihood Explanation

An unprivileged transaction sender can trigger this against any upgradeable contract that exposes a `replace_class` call with user-controlled input (e.g., `fn upgrade(new_class_hash: ClassHash) { replace_class_syscall(new_class_hash); }`). This pattern is common in DeFi protocols. The attacker simply calls the upgrade function with an undeclared class hash. No privileged access, leaked key, or operator cooperation is required — only the ability to submit a transaction to a vulnerable contract.

---

### Recommendation

Implement the missing validation inside `execute_replace_class` before updating the contract state. Specifically, verify that `request.class_hash` exists in `contract_class_changes` (i.e., it was declared in the current or a prior block). If the class hash is not declared, write a failure response and return without updating the state, consistent with how other invalid syscall arguments are handled.

---

### Proof of Concept

1. Deploy contract A with an upgrade entry point:
   ```
   fn upgrade(new_class_hash: ClassHash) {
       replace_class_syscall(new_class_hash);
   }
   ```
2. Deposit funds into contract A (or have other users do so).
3. Submit a transaction calling `upgrade(0xdeadbeef)` where `0xdeadbeef` is not a declared class hash. The OS processes this via `execute_replace_class` without validation and commits `class_hash = 0xdeadbeef` to contract A's state entry. [4](#0-3) 
4. In any subsequent block, any transaction calling contract A causes `execute_entry_point` to:
   - Read `class_hash = 0xdeadbeef` from `contract_state_changes`.
   - Call `dict_read(contract_class_changes, 0xdeadbeef)` → returns `0` (undeclared).
   - Call `find_element(compiled_class_facts, 0)` → **fails** (no compiled class with hash 0 exists for an honest prover).
   - The block is unprovable; the sequencer must drop all calls to contract A.
5. Funds in contract A are permanently frozen with no recovery mechanism.

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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/os.cairo (L269-275)
```text
    %{ InitializeClassHashes %}
    // A dictionary from class hash to compiled class hash (Casm).
    let (contract_class_changes: DictAccess*) = dict_new();

    return (
        contract_state_changes=contract_state_changes, contract_class_changes=contract_class_changes
    );
```
