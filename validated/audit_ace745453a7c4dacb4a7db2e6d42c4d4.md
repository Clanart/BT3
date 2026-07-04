### Title
Missing Declared Class Hash Validation in `execute_replace_class` Enables Permanent Freezing of Contract Funds — (File: `execution/syscall_impls.cairo`)

---

### Summary

The `execute_replace_class` function in `syscall_impls.cairo` processes the `replace_class` syscall without verifying that the supplied class hash corresponds to a previously declared contract class. An unprivileged attacker who can trigger a target contract's upgrade path with attacker-controlled input can replace that contract's class with an arbitrary, undeclared hash, rendering the contract permanently uncallable and freezing all funds held within it.

---

### Finding Description

In `execute_replace_class` (lines 877–916 of `syscall_impls.cairo`), the OS reads the new class hash from the syscall request and writes it directly into `contract_state_changes` with no validation that the hash corresponds to a declared class:

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

The inline `TODO` comment explicitly acknowledges the missing check. The only guard present is a gas check (`reduce_syscall_gas_and_write_response_header`); there is no lookup into `contract_class_changes` to confirm the hash is declared. [1](#0-0) 

The `replace_class` syscall updates `contract_state_changes` (the per-address state, which stores the class hash), not `contract_class_changes` (the declared-class registry). When the contract is subsequently called, the OS resolves the class hash from `contract_state_changes` and looks up the compiled class in the compiled class facts bundle. If the hash is absent from that bundle, execution fails unconditionally and permanently. [2](#0-1) 

---

### Impact Explanation

Once a contract's class hash is set to an undeclared value, every subsequent call to that contract will fail at class resolution time. There is no recovery path: `replace_class` itself requires the contract to be callable, and no other OS mechanism can reset the class hash. Any ERC-20 tokens, ETH, or other assets held in the contract's storage are permanently inaccessible. This matches the **Critical — Permanent freezing of funds** impact category.

---

### Likelihood Explanation

The attack requires the attacker to cause a target contract to invoke `replace_class` with an attacker-supplied hash. This is realistic for any upgradeable contract pattern where the new class hash is passed as a caller-controlled argument (a common pattern in DeFi protocols). The OS is the intended last line of defense for this invariant — the TODO comment confirms the check was planned but omitted. An unprivileged user submitting a normal invoke transaction is the entry point; no privileged role is required.

---

### Recommendation

Before writing the new class hash into `contract_state_changes`, verify that `request.class_hash` exists in `contract_class_changes` (i.e., it has been declared via a prior declare transaction). This is the check already identified in the TODO comment and should be implemented as:

```cairo
// Verify the class hash is declared.
let (compiled_class_hash) = dict_read{dict_ptr=contract_class_changes}(key=class_hash);
assert_not_zero(compiled_class_hash);
```

---

### Proof of Concept

1. Attacker identifies (or deploys) an upgradeable contract `Vault` that holds user funds and exposes:
   ```
   fn upgrade(new_class_hash: felt252) {
       replace_class_syscall(new_class_hash);
   }
   ```
2. Attacker submits an invoke transaction calling `Vault.upgrade(0xdeadbeef)` where `0xdeadbeef` is not a declared class hash.
3. The sequencer includes the transaction. The OS executes `execute_replace_class`:
   - Gas check passes.
   - `class_hash = 0xdeadbeef` is written to `contract_state_changes[Vault_address]` with no further validation. [3](#0-2) 
4. The proof is generated and accepted on L1.
5. Any subsequent call to `Vault` causes the OS to look up `0xdeadbeef` in the compiled class facts bundle, find nothing, and revert.
6. All funds in `Vault` are permanently frozen with no recovery mechanism.

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
