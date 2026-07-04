### Title
Missing Zero-Validation on `class_hash` in `execute_replace_class` Allows Permanent Contract Bricking and Fund Freezing — (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

---

### Summary

The `execute_replace_class` function in the StarkNet OS accepts a caller-supplied `class_hash` of zero without any validation. Setting a contract's class hash to zero is equivalent to setting it to `UNINITIALIZED_CLASS_HASH`, permanently rendering the contract non-callable. Any funds held by that contract are irreversibly frozen. The OS itself contains a TODO comment acknowledging the missing check.

---

### Finding Description

`execute_replace_class` in `syscall_impls.cairo` reads `request.class_hash` directly from the syscall pointer and writes it into `contract_state_changes` without asserting it is non-zero or that it corresponds to a declared class:

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
``` [1](#0-0) 

The same pattern exists in the deprecated path:

```cairo
func execute_replace_class{contract_state_changes: DictAccess*, revert_log: RevertLogEntry*}(
    contract_address, syscall_ptr: ReplaceClass*
) {
    alloc_locals;
    let class_hash = syscall_ptr.class_hash;
    ...
    tempvar new_state_entry = new StateEntry(
        class_hash=class_hash, storage_ptr=state_entry.storage_ptr, nonce=state_entry.nonce
    );
    dict_update{dict_ptr=contract_state_changes}(...);
``` [2](#0-1) 

The value `UNINITIALIZED_CLASS_HASH` is the sentinel used by `deploy_contract` to assert a slot is empty before deployment:

```cairo
assert state_entry.class_hash = UNINITIALIZED_CLASS_HASH;
``` [3](#0-2) 

And `get_contract_state_hash` treats `UNINITIALIZED_CLASS_HASH` as the "no contract" sentinel:

```cairo
if (class_hash == UNINITIALIZED_CLASS_HASH) {
    if (storage_root == 0) {
        if (nonce == 0) {
            return (hash=0);
        }
    }
}
``` [4](#0-3) 

Calling `replace_class(0)` writes `UNINITIALIZED_CLASS_HASH` into the live contract's state entry. The contract's storage and nonce are preserved (so it is not re-deployable), but it has no class — every subsequent entry-point dispatch will fail to find any code to execute.

---

### Impact Explanation

**Critical — Permanent freezing of funds.**

Any ERC-20 balance, ETH bridged via the L1↔L2 bridge, or any other asset stored in the contract's storage becomes permanently inaccessible. Because the nonce is non-zero after deployment, `deploy_contract` will reject a re-deployment to the same address (`assert state_entry.class_hash = UNINITIALIZED_CLASS_HASH` fails when nonce ≠ 0 or storage is non-empty). There is no upgrade or recovery path once the class hash is zeroed.

---

### Likelihood Explanation

The syscall is reachable by any deployed contract. Two realistic paths exist:

1. **Malicious rug-pull**: An attacker deploys a contract that accepts user deposits (e.g., a fake vault or staking contract). After accumulating funds, the attacker calls `replace_class(0)`. The OS accepts it, the contract is bricked, and all deposited assets are frozen permanently.

2. **Vulnerable contract exploited by a third party**: A legitimate contract that exposes an unguarded `replace_class` call (e.g., via a proxy upgrade mechanism with insufficient access control) can be triggered by any external caller to zero out the class hash, freezing all funds held by the contract.

Both paths require only an unprivileged transaction sender and no special privileges.

---

### Recommendation

Add an explicit non-zero assertion on `class_hash` immediately after reading it in both `execute_replace_class` implementations:

```cairo
// In syscall_impls.cairo
let class_hash = request.class_hash;
with_attr error_message("Invalid class hash: zero is not allowed.") {
    assert_not_zero(class_hash);
}
```

Additionally, resolve the existing TODO by verifying that `class_hash` is present in `contract_class_changes` (i.e., it has been declared in the current or a prior block) before committing the state update. [5](#0-4) 

---

### Proof of Concept

1. Deploy contract `Vault` that holds user ERC-20 balances in storage.
2. Users call `Vault.deposit(amount)` — balances accumulate in storage.
3. Attacker (contract owner or exploiter of an access-control bug) submits an invoke transaction that calls `Vault.__execute__` → internally issues the `replace_class` syscall with `class_hash = 0`.
4. The OS processes `execute_replace_class`:
   - Reads `class_hash = 0` from the syscall request.
   - No `assert_not_zero` is present.
   - Writes `StateEntry(class_hash=0, storage_ptr=..., nonce=...)` into `contract_state_changes`.
5. Block is proven and committed on L1.
6. All subsequent calls to `Vault` fail: the OS dispatches to class hash `0` (UNINITIALIZED), finds no entry points, and reverts.
7. User balances remain in storage but are permanently inaccessible — **funds frozen**. [6](#0-5) [7](#0-6)

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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/deploy_contract.cairo (L53-54)
```text
    assert state_entry.class_hash = UNINITIALIZED_CLASS_HASH;
    assert state_entry.nonce = 0;
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/state/commitment.cairo (L55-61)
```text
    if (class_hash == UNINITIALIZED_CLASS_HASH) {
        if (storage_root == 0) {
            if (nonce == 0) {
                return (hash=0);
            }
        }
    }
```
