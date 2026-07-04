### Title
Missing Class Existence Validation in `execute_replace_class` Allows Permanent Freezing of Contract Funds - (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

### Summary
The `execute_replace_class` syscall in the StarkNet OS updates a contract's class hash in the state without verifying that the replacement class hash corresponds to a previously declared contract class. This is an exact structural analog to the reported vulnerability: a state-transition is committed without validating that the referenced resource actually exists, breaking a protocol invariant and enabling permanent freezing of funds.

### Finding Description
In `syscall_impls.cairo`, the `execute_replace_class` function reads the requested class hash from the syscall request and immediately writes it into `contract_state_changes` without any check that the class hash is present in `contract_class_changes` (i.e., that it was previously declared):

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

The inline `TODO` comment explicitly acknowledges the missing check. The OS unconditionally commits the new class hash to state. The protocol invariant that **every contract's class hash must reference a declared class** is not enforced at the OS level. [1](#0-0) 

### Impact Explanation
Once a contract's class hash is set to an undeclared value:

1. The contract's state entry in `contract_state_changes` references a class hash that has no corresponding entry in `contract_class_changes`.
2. Any future transaction targeting this contract will require the OS to look up the class by that hash. Since the class does not exist, the OS cannot execute the contract's entry points.
3. The sequencer will be unable to include any transaction that calls into this contract, because execution simulation will fail at class lookup.
4. All funds (tokens, balances, NFTs) held in the contract's storage become permanently inaccessible — **permanent freezing of funds**.

This matches the allowed critical impact: **Permanent freezing of funds**.

### Likelihood Explanation
The attack path requires no privileged access:

1. An attacker deploys a contract (e.g., a token vault or escrow) and attracts user deposits.
2. The attacker calls `replace_class` from within their contract, passing an arbitrary felt value (e.g., `1`) that has never been declared.
3. The OS executes the syscall, passes gas checks, and commits the invalid class hash to state — no revert occurs.
4. The contract is now permanently bricked. All deposited user funds are frozen.

The entry point is a standard user-callable syscall (`replace_class`) reachable by any contract deployer or unprivileged transaction sender. No operator privilege or key leak is required.

### Recommendation
Before committing the new class hash to `contract_state_changes`, the OS must verify that the class hash exists in `contract_class_changes`. Concretely, perform a `dict_read` on `contract_class_changes` keyed by `request.class_hash` and assert the returned value is non-zero (i.e., a valid compiled class hash was previously declared). This mirrors the check already performed implicitly during `execute_declare_transaction` via `dict_update` with `prev_value=0`. [2](#0-1) 

### Proof of Concept
1. Attacker deploys `VaultContract` which accepts ERC-20 deposits from users and stores balances in its own storage.
2. Users deposit 100,000 USDC into `VaultContract`. Funds are held in the contract's storage.
3. Attacker calls an external function on `VaultContract` that internally invokes the `replace_class` syscall with `class_hash = 0x1` (an arbitrary, never-declared felt).
4. The OS executes `execute_replace_class`: gas is deducted, the `dict_update` on `contract_state_changes` succeeds, and the transaction is included in the block with no revert.
5. The proven state now contains `VaultContract.class_hash = 0x1`, which has no entry in `contract_class_changes`.
6. Any subsequent transaction invoking `VaultContract` fails at class lookup in the OS. The sequencer cannot include such transactions.
7. The 100,000 USDC in `VaultContract`'s storage is permanently frozen — no withdrawal function can ever be executed again.

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
