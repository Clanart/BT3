### Title
Missing Class Hash Existence Validation in `execute_replace_class` Allows Permanent Freezing of Contract Funds — (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

---

### Summary

The `execute_replace_class` syscall implementation in the StarkNet OS accepts any arbitrary felt value as a new class hash without verifying that a declared, compiled class exists for that hash. This is an acknowledged missing check (marked with a `TODO` in the source). An unprivileged contract deployer can exploit this to permanently freeze funds held by a contract by replacing its class hash with a non-existent value, rendering the contract permanently uncallable.

---

### Finding Description

In `syscall_impls.cairo`, the `execute_replace_class` function processes the `REPLACE_CLASS` syscall:

```cairo
func execute_replace_class{
    range_check_ptr,
    syscall_ptr: felt*,
    contract_state_changes: DictAccess*,
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
    ...
}
```

The function unconditionally writes `class_hash` (attacker-controlled) into `contract_state_changes` with no check that this hash corresponds to any entry in `contract_class_changes` (the declared class registry). The `TODO` comment explicitly acknowledges this missing guard.

Contrast this with `execute_declare_transaction`, which enforces `prev_value=0` to guarantee a class is declared only once and that `compiled_class_hash` is non-zero. No equivalent guard exists in `execute_replace_class`.

After the state is committed, any future call to the affected contract will attempt to look up the compiled class for the garbage hash, fail to find it, and revert — but the state update (the class hash change) is already finalized on-chain. The revert log mechanism only rolls back state changes within a single transaction; it cannot undo a successfully committed `replace_class` from a prior transaction.

---

### Impact Explanation

**Permanent freezing of funds (Critical).**

Once a contract's class hash is replaced with a non-existent hash and the block is finalized:
- All future calls to that contract will fail at class lookup.
- Any ERC-20 balances, ETH, or other assets stored in the contract's storage are permanently inaccessible.
- There is no recovery path: the OS has no mechanism to "un-replace" a class hash, and the contract cannot execute any function (including a self-repair) because its class no longer resolves.

---

### Likelihood Explanation

The attack path is reachable by any unprivileged user who can deploy a contract:

1. Attacker deploys a contract (`MaliciousVault`) that accepts user deposits.
2. Users deposit funds into `MaliciousVault`.
3. Attacker calls a function in `MaliciousVault` that internally invokes the `replace_class` syscall with an arbitrary non-existent felt value as the new class hash.
4. The OS processes the syscall, writes the garbage class hash into `contract_state_changes`, and commits the block.
5. All subsequent calls to `MaliciousVault` fail. User funds are permanently frozen.

No privileged role, leaked key, or external dependency is required. The attacker only needs to deploy a contract — a standard, permissionless operation on StarkNet.

---

### Recommendation

Before writing the new class hash into `contract_state_changes`, verify that the hash exists in `contract_class_changes` (i.e., it was declared in the current or a prior block). Specifically, perform a `dict_read` on `contract_class_changes` for `class_hash` and assert the result is non-zero (i.e., a compiled class hash is registered for it). This is exactly what the existing `TODO` comment calls for.

---

### Proof of Concept

**Root cause location:** [1](#0-0) 

The `TODO` at line 898 explicitly acknowledges the missing check: [2](#0-1) 

**Contrast with the class declaration path**, which enforces `prev_value=0` (class declared only once) and `assert_not_zero(compiled_class_hash)`: [3](#0-2) 

**The syscall dispatch** that routes `REPLACE_CLASS_SELECTOR` to `execute_replace_class` with no pre-check: [4](#0-3) 

**Attack flow:**

1. Attacker deploys a contract containing logic that calls `replace_class(0xdeadbeef)` (any undeclared felt).
2. Users deposit funds into the contract.
3. Attacker invokes the malicious function. The OS executes `execute_replace_class`, writes `class_hash=0xdeadbeef` into `contract_state_changes` with no validation, and commits the block.
4. All future calls to the contract fail at class resolution. Funds are permanently frozen.

The `StateEntry` struct that gets corrupted: [5](#0-4)

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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L815-819)
```text
    // Note that prev_value=0 enforces that a class may be declared only once.
    assert_not_zero(compiled_class_hash);
    dict_update{dict_ptr=contract_class_changes}(
        key=[class_hash_ptr], prev_value=0, new_value=compiled_class_hash
    );
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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/state/commitment.cairo (L25-29)
```text
struct StateEntry {
    class_hash: felt,
    storage_ptr: DictAccess*,
    nonce: felt,
}
```
