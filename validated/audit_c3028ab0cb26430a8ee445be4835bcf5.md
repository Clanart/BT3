### Title
Missing Declared-Class Validation in `execute_replace_class` Allows Permanent Freezing of Contract Funds — (`File: crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

---

### Summary

The `execute_replace_class` syscall handler in the StarkNet OS does not verify that the supplied `class_hash` corresponds to a previously declared class. This is the direct analog of M-13: just as `BeaconProxyDeployer.deploy()` checked only for a zero address instead of verifying bytecode existence, `execute_replace_class` checks only that gas is sufficient and never checks whether the new class hash actually exists in the declared-class trie. The OS accepts and commits the state transition unconditionally, leaving the contract permanently non-executable if the hash is undeclared.

---

### Finding Description

In `execute_replace_class` (the new-syscall path), after deducting gas, the function reads `class_hash` from the request and immediately writes it into `contract_state_changes` with no existence check:

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

The function signature does not include `contract_class_changes` as an implicit argument, so it structurally cannot perform the lookup:

```cairo
func execute_replace_class{
    range_check_ptr,
    syscall_ptr: felt*,
    contract_state_changes: DictAccess*,
    revert_log: RevertLogEntry*,
}(contract_address: felt) {
``` [2](#0-1) 

The same omission exists in the deprecated path (`deprecated_execute_syscalls.cairo`), where `execute_replace_class` also lacks `contract_class_changes` and performs no existence check: [3](#0-2) 

By contrast, `execute_declare_transaction` correctly enforces that a class hash is a valid Sierra hash pre-image before writing it to `contract_class_changes`: [4](#0-3) 

The `replace_class` syscall is dispatched from `execute_syscalls` without any pre-validation of the class hash: [5](#0-4) 

---

### Impact Explanation

**Critical — Permanent freezing of funds.**

Once a contract's class hash is set to an undeclared value and the block is proven, the state commitment reflects a contract whose class does not exist in the class trie. Every subsequent call to that contract — including any entry point that would transfer or withdraw funds — will fail at class lookup. Because the state root is already committed on-chain, the funds held by that contract are permanently inaccessible. There is no recovery path: the OS will not accept a transaction that re-declares the missing class under the same hash (the `dict_update` in `execute_declare_transaction` enforces `prev_value=0`, i.e., a class may be declared only once), and the contract itself cannot execute any logic to self-rescue.

---

### Likelihood Explanation

Any contract that exposes an upgrade path where the new class hash is derived from user-supplied input (e.g., a DAO governance contract, a proxy with an admin-controlled upgrade function, or any contract that passes calldata directly to `replace_class`) is exploitable by an unprivileged transaction sender. The attacker only needs to submit a transaction that causes the target contract to call `replace_class` with an arbitrary felt value that has never been declared. No privileged key or operator cooperation is required; the OS will accept and prove the resulting state transition.

---

### Recommendation

Add `contract_class_changes: DictAccess*` as an implicit argument to `execute_replace_class` (both the new-syscall and deprecated variants) and perform a `dict_read` on `contract_class_changes` keyed by `class_hash` before updating `contract_state_changes`. Assert that the returned compiled class hash is non-zero (i.e., the class has been declared). This mirrors the invariant already enforced by `execute_declare_transaction`, which uses `prev_value=0` to guarantee a class is written only once and only with a valid pre-image.

---

### Proof of Concept

1. Attacker deploys contract `A` holding STRK/ETH, whose logic includes:
   ```
   replace_class(class_hash=0xdeadbeef)  // 0xdeadbeef never declared
   ```
2. Attacker submits an invoke transaction calling that entry point.
3. The OS dispatches `execute_replace_class` → gas check passes → `class_hash = 0xdeadbeef` is written into `contract_state_changes` for contract `A` with no lookup into `contract_class_changes`.
4. The block is proven and the new state root — containing `A.class_hash = 0xdeadbeef` — is committed on L1.
5. Any subsequent call to contract `A` fails: the OS cannot find a compiled class for `0xdeadbeef`.
6. All funds in contract `A` are permanently frozen.

### Citations

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo (L877-884)
```text
// Replaces the class.
func execute_replace_class{
    range_check_ptr,
    syscall_ptr: felt*,
    contract_state_changes: DictAccess*,
    revert_log: RevertLogEntry*,
}(contract_address: felt) {
    alloc_locals;
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo (L896-913)
```text
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
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/deprecated_execute_syscalls.cairo (L307-329)
```text
func execute_replace_class{contract_state_changes: DictAccess*, revert_log: RevertLogEntry*}(
    contract_address, syscall_ptr: ReplaceClass*
) {
    alloc_locals;
    let class_hash = syscall_ptr.class_hash;

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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L738-743)
```text
        let expected_class_hash = finalize_class_hash(
            contract_class_component_hashes=contract_class_component_hashes
        );
        with_attr error_message("Invalid class hash pre-image.") {
            assert [class_hash_ptr] = expected_class_hash;
        }
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/execute_syscalls.cairo (L195-203)
```text
    if (selector == REPLACE_CLASS_SELECTOR) {
        execute_replace_class(contract_address=execution_context.execution_info.contract_address);
        %{ OsLoggerExitSyscall %}
        return execute_syscalls(
            block_context=block_context,
            execution_context=execution_context,
            syscall_ptr_end=syscall_ptr_end,
        );
    }
```
