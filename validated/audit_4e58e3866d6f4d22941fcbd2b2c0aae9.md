### Title
Missing Declared-Class Validation in `execute_replace_class` Allows Permanent Contract Bricking — (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

### Summary

The `execute_replace_class` syscall handler in the StarkNet OS accepts any arbitrary felt value as the new class hash without verifying that the hash corresponds to a previously declared contract class. An unprivileged contract can call `replace_class` with an undeclared or zero class hash, permanently setting its own class to an invalid value. Any funds held in that contract become permanently frozen because the OS cannot resolve the class for future calls.

### Finding Description

In `execute_replace_class`, the OS reads `class_hash` directly from the syscall request and writes it into the contract's `StateEntry` without any validation:

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

The TODO comment explicitly acknowledges the missing check. The `contract_class_changes` dictionary (which maps class hashes to compiled class hashes) is never consulted. There is no `assert_not_zero(class_hash)` guard either.

This is the direct analog of the external report's vulnerability class: a restriction that is supposed to be enforced at the protocol level (only declared class hashes may be installed) is bypassed because the OS does not enforce it. Just as the Omnipool's same-block restriction was keyed on `msg.sender` and bypassed by transferring LP tokens to a second account, the OS's implicit restriction that `replace_class` must reference a declared class is bypassed simply by passing an arbitrary felt. [1](#0-0) 

### Impact Explanation

**Critical — Permanent freezing of funds.**

If a contract calls `replace_class(0)` or `replace_class(<any undeclared hash>)`, the OS writes that invalid hash into the contract's `StateEntry`. On any subsequent call to the contract, the OS attempts to look up the class by hash. Because no compiled class exists for the invalid hash, execution cannot proceed and the call permanently fails. All ERC-20 balances, NFTs, or other assets stored in that contract's storage become irretrievable.

The `UNINITIALIZED_CLASS_HASH` sentinel (value `0`) used in `deploy_contract.cairo` to detect undeployed contracts confirms that class hash `0` is treated as "no class": [2](#0-1) 

### Likelihood Explanation

**High.** The `replace_class` syscall is available to every Cairo contract with no privilege requirement. The attacker-controlled entry path is:

1. Deploy (or exploit) any contract that holds user funds and exposes a `replace_class` call path (e.g., an upgradeable contract whose governance can be influenced).
2. Invoke the contract such that it calls `replace_class` with an undeclared hash (e.g., `0x1` or `0`).
3. The OS writes the invalid class hash into the contract's `StateEntry` via `dict_update`.
4. All future calls to the contract fail; funds are permanently frozen.

No privileged role, leaked key, or external dependency is required. The root cause is entirely within the OS program's `execute_replace_class` handler. [3](#0-2) 

### Recommendation

Before writing the new `StateEntry`, verify that `class_hash` is present in `contract_class_changes` (or in the pre-existing class tree). Concretely, perform a `dict_read` on `contract_class_changes` for `class_hash` and assert the returned compiled class hash is non-zero:

```cairo
// Verify the new class hash is declared.
let (compiled_class_hash) = dict_read{dict_ptr=contract_class_changes}(key=class_hash);
assert_not_zero(compiled_class_hash);
```

This mirrors the check already present in `execute_declare_transaction`: [4](#0-3) 

### Proof of Concept

1. Deploy contract `Vault` that accepts ETH/ERC-20 deposits and exposes an `upgrade(new_class_hash)` function that calls `replace_class(new_class_hash)`.
2. Users deposit funds into `Vault`.
3. Attacker (or governance exploit) calls `Vault.upgrade(0x1)` — an undeclared class hash.
4. The OS executes `execute_replace_class`, writes `class_hash=0x1` into `Vault`'s `StateEntry` with no validation.
5. Any subsequent call to `Vault` (withdraw, transfer, etc.) fails because the OS cannot resolve class `0x1`.
6. All deposited funds are permanently frozen. [5](#0-4)

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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/deploy_contract.cairo (L53-53)
```text
    assert state_entry.class_hash = UNINITIALIZED_CLASS_HASH;
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L816-819)
```text
    assert_not_zero(compiled_class_hash);
    dict_update{dict_ptr=contract_class_changes}(
        key=[class_hash_ptr], prev_value=0, new_value=compiled_class_hash
    );
```
