### Title
Missing Class Hash Existence Validation in `execute_replace_class` Allows Permanent Freezing of Contract Funds - (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

---

### Summary

The `execute_replace_class` syscall implementation in the StarkNet OS does not verify that the replacement class hash corresponds to a previously declared class. An attacker who can trigger `replace_class` with an arbitrary felt value (e.g., through a contract with a permissionless or insufficiently guarded upgrade function) can permanently replace a contract's class with a non-existent hash, bricking the contract and freezing all funds it holds.

---

### Finding Description

In `syscall_impls.cairo`, the `execute_replace_class` function processes the `replace_class` syscall. After deducting gas, it reads the current state entry and writes a new `StateEntry` with the caller-supplied `class_hash` directly into `contract_state_changes` — with no check that the supplied hash corresponds to any declared class:

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
```

The TODO comment at line 898 explicitly acknowledges this missing validation. The `contract_class_changes` dictionary — which tracks declared class hashes — is not consulted at all. Any felt value is accepted as a valid replacement class hash. [1](#0-0) 

The syscall dispatcher in `execute_syscalls.cairo` routes `REPLACE_CLASS_SELECTOR` directly to this function: [2](#0-1) 

---

### Impact Explanation

**Critical — Permanent freezing of funds.**

Once a contract's class hash is replaced with an undeclared value, every subsequent call to that contract will fail at class resolution time (the OS/VM cannot find the class bytecode for the hash). The contract becomes permanently inoperable. Any ERC-20 balances, ETH-equivalent tokens, or other assets stored in the contract's storage are irrecoverably frozen, as no function — including withdrawal — can ever execute again.

---

### Likelihood Explanation

**Medium.** The attack requires the ability to trigger `replace_class` on a target contract with an attacker-controlled hash. This is realistic in two scenarios:

1. **Permissionless upgrade functions**: A contract exposes an upgrade entrypoint that accepts a new class hash from any caller (or from a role the attacker controls). The OS provides no backstop validation, so passing an undeclared hash succeeds at the protocol level.
2. **Malicious deployer**: A contract deployer deploys a contract that calls `replace_class` with an invalid hash (e.g., in a callable function), then lures users to deposit funds before triggering the replacement. The OS accepts the invalid hash, permanently freezing deposited funds.

Both paths are reachable by an unprivileged transaction sender or contract deployer — no privileged operator access is required.

---

### Recommendation

Inside `execute_replace_class`, before writing the new `StateEntry`, verify that `class_hash` exists in `contract_class_changes` (or in the pre-existing declared class set). Concretely, perform a `dict_read` on `contract_class_changes` for `class_hash` and assert the result is non-zero (i.e., a compiled class hash has been registered for it). This is exactly what the existing TODO comment at line 898 calls for and what the `execute_declare_transaction` path enforces via `dict_update` with `prev_value=0`. [3](#0-2) 

---

### Proof of Concept

1. Attacker deploys Contract A (e.g., a token vault) that exposes:
   ```
   fn upgrade(new_class_hash: felt252) {
       replace_class_syscall(new_class_hash);
   }
   ```
2. Users deposit funds into Contract A.
3. Attacker calls `upgrade(0xdeadbeef)` — an arbitrary undeclared felt.
4. The OS executes `execute_replace_class`:
   - Gas is deducted.
   - `class_hash = 0xdeadbeef` is written into `contract_state_changes` for Contract A's address.
   - **No check against `contract_class_changes` is performed.**
5. The state transition is accepted and proven.
6. All subsequent calls to Contract A fail at class resolution — the contract is permanently bricked.
7. All deposited funds are permanently frozen. [4](#0-3)

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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L815-819)
```text
    // Note that prev_value=0 enforces that a class may be declared only once.
    assert_not_zero(compiled_class_hash);
    dict_update{dict_ptr=contract_class_changes}(
        key=[class_hash_ptr], prev_value=0, new_value=compiled_class_hash
    );
```
