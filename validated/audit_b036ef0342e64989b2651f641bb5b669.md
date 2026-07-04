### Title
Missing Validation of New Class Hash in `execute_replace_class` Enables Permanent Freezing of Contract Funds — (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

---

### Summary

The `execute_replace_class` function in the StarkNet OS does not validate that the new class hash supplied to the `replace_class` syscall corresponds to a previously declared contract class. Any contract can replace its own class hash with an arbitrary, undeclared felt value. Once replaced, the contract becomes permanently inaccessible because the OS execution engine cannot resolve the class, freezing all funds held within it. The missing check is explicitly acknowledged in the source with a `TODO` comment.

---

### Finding Description

In `syscall_impls.cairo`, `execute_replace_class` (lines 878–916) reads the new class hash from the syscall request and writes it directly into `contract_state_changes` without verifying that the hash corresponds to a declared class:

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

After the replacement, any subsequent call to the contract reaches `execute_entry_point`, which performs:

1. A `dict_read` on `contract_class_changes` keyed by the (now-invalid) class hash — returning 0 or an uninitialized value.
2. A `find_element` lookup in `compiled_class_facts_bundle` for that compiled class hash. [2](#0-1) 

`find_element` asserts the element exists. If the class hash is not present in the compiled class facts, the assertion fails. The contract is permanently inaccessible — no upgrade, no recovery, no withdrawal.

The analog to the external report is direct:

| External Report | This Codebase |
|---|---|
| `setUnstakeCoolDown` accepts any `uint64` with no upper-bound check | `execute_replace_class` accepts any `felt` class hash with no existence check |
| Retroactively freezes stakers' funds | Permanently freezes all funds held in the affected contract |
| Missing input validation on a critical state-transition parameter | Missing input validation on a critical state-transition parameter |

---

### Impact Explanation

**Critical — Permanent freezing of funds.**

Any contract holding user funds (token contracts, vaults, bridges, multisigs) whose class hash is replaced with an undeclared value becomes permanently bricked. All assets stored in that contract's storage are irrecoverable: the contract cannot be called, cannot be upgraded through its own interface, and the OS provides no recovery path. The state entry persists with the invalid class hash across all future blocks.

---

### Likelihood Explanation

**Medium-High.**

- The attack requires no privileged role. Any contract deployer — an unprivileged protocol participant — can deploy a contract that calls `replace_class` with an arbitrary felt.
- The missing check is explicitly flagged in the production source code with a `TODO` comment dated `1/1/2026`, confirming the developers are aware it is absent.
- A malicious deployer can create a contract that appears legitimate, attract user deposits, and then trigger `replace_class` with an invalid hash, permanently freezing deposited funds.
- Alternatively, any legitimate contract with an insufficient access-control guard on an upgrade path is exploitable by an external attacker via the same syscall.

---

### Recommendation

Before writing the new class hash to `contract_state_changes` in `execute_replace_class`, validate that the hash is declared by checking that `contract_class_changes` contains a non-zero compiled class hash for it:

```cairo
// Validate that the new class hash is declared.
let (compiled_class_hash) = dict_read{dict_ptr=contract_class_changes}(key=class_hash);
with_attr error_message("Class hash is not declared.") {
    assert_not_zero(compiled_class_hash);
}
```

This mirrors the validation already performed implicitly in `execute_entry_point` and makes the OS the authoritative enforcement point, regardless of what individual contracts do.

---

### Proof of Concept

1. **Deploy** a contract `MaliciousVault` that:
   - Exposes a `deposit()` function accepting STRK.
   - Exposes an `owner_freeze()` function that calls the `replace_class` syscall with `class_hash = 0xdeadbeef` (an undeclared hash).

2. **Attract deposits**: Users call `deposit()`, transferring funds into `MaliciousVault`. The contract state now holds user balances.

3. **Trigger freeze**: The deployer calls `owner_freeze()`. The OS executes `execute_replace_class`:
   - `class_hash = 0xdeadbeef` is written to `contract_state_changes` for `MaliciousVault`.
   - No validation is performed.

4. **Permanent inaccessibility**: Any subsequent transaction targeting `MaliciousVault` reaches `execute_entry_point`:
   - `dict_read(contract_class_changes, key=0xdeadbeef)` returns 0 (undeclared).
   - `find_element(..., key=0)` fails — no compiled class with hash 0 exists.
   - The call cannot be executed; the transaction reverts.

5. **Result**: All user funds stored in `MaliciousVault`'s storage are permanently frozen. No withdrawal, no upgrade, no recovery is possible at the protocol level. [3](#0-2) [4](#0-3)

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
