### Title
Missing Zero Class Hash Validation in `execute_replace_class` Allows Permanent Contract Bricking - (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

---

### Summary

The `execute_replace_class` syscall handler in the StarkNet OS accepts `class_hash = 0` without any validation. Because `0` is the sentinel value `UNINITIALIZED_CLASS_HASH` — the exact value the OS uses to mark a contract address as having no deployed code — setting a live contract's class hash to `0` permanently bricks it. All funds held in that contract's storage become permanently frozen, with no recovery path.

---

### Finding Description

In `syscall_impls.cairo`, the `execute_replace_class` function reads the requested `class_hash` directly from the syscall request and writes it into the contract's `StateEntry` without any validation:

```cairo
// execute_replace_class (syscall_impls.cairo ~line 877)
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

The inline TODO comment explicitly acknowledges the missing validation. There is no `assert_not_zero(class_hash)` and no check that the class hash corresponds to a declared class.

The value `0` is the `UNINITIALIZED_CLASS_HASH` sentinel. In `deploy_contract.cairo`, the OS enforces that a contract address is unoccupied before deployment by asserting:

```cairo
assert state_entry.class_hash = UNINITIALIZED_CLASS_HASH;
assert state_entry.nonce = 0;
```

After a successful `replace_class(0)` call, the contract's `StateEntry` has `class_hash = 0`. Any subsequent call to that contract will attempt to dispatch to class hash `0`, which has no declared entry points, causing every call to fail. Because the nonce is non-zero after prior use, re-deployment to the same address is also blocked. The contract and all its storage are permanently inaccessible.

The revert log records the old class hash (`CHANGE_CLASS_ENTRY`), so the change is only permanent if the enclosing transaction commits — which it will, since `replace_class(0)` itself succeeds from the OS's perspective.

---

### Impact Explanation

Any contract that holds user funds (e.g., a vault, a pool, an ERC-20 token contract) and calls `replace_class(0)` — whether due to a bug or malicious intent — will have its class hash set to the uninitialized sentinel. All future calls to that contract revert at the entry-point dispatch stage. The contract's storage, including all token balances and user deposits, is permanently frozen with no recovery mechanism. This satisfies the **Critical: Permanent freezing of funds** impact category.

---

### Likelihood Explanation

The `replace_class` syscall is callable by any contract on itself. Realistic trigger paths include:

1. **Buggy contract**: A contract passes an uninitialized or zero-valued variable as the new class hash argument to `replace_class`. Cairo's default uninitialized `felt` value is `0`, making this a natural mistake.
2. **Malicious contract acting as a shared vault**: A contract that holds funds for multiple users calls `replace_class(0)` to permanently lock all deposited funds, e.g., as a griefing or rug-pull mechanism.
3. **Upgrade logic error**: A contract performing a conditional upgrade computes a class hash that evaluates to `0` under certain inputs and calls `replace_class` with it.

No privileged role or operator access is required. Any deployed contract can trigger this via a standard user-submitted transaction.

---

### Recommendation

Add an explicit zero-check before updating the state entry in `execute_replace_class`:

```cairo
// In execute_replace_class, after reading class_hash:
let class_hash = request.class_hash;
if (class_hash == 0) {
    write_failure_response(remaining_gas=remaining_gas, failure_felt=ERROR_INVALID_ARGUMENT);
    return ();
}
```

Additionally, resolve the existing TODO by verifying that `class_hash` corresponds to a declared class in `contract_class_changes` before accepting the syscall. This mirrors the fix applied to the PoolTogether H-06 issue: reject the sentinel/zero value at the validation boundary rather than allowing it to corrupt persistent state.

---

### Proof of Concept

1. Deploy a contract `VaultContract` that holds ERC-20 balances for multiple users.
2. Submit a transaction that calls `VaultContract.__execute__`, which internally invokes the `replace_class` syscall with `class_hash = 0`.
3. The OS processes `execute_replace_class`: no validation fires, `dict_update` writes `StateEntry(class_hash=0, ...)` for `VaultContract`'s address.
4. The transaction commits. `VaultContract`'s state entry now has `class_hash = UNINITIALIZED_CLASS_HASH`.
5. Any user attempting to call `VaultContract` (e.g., to withdraw funds) triggers `execute_call_contract`, which reads `state_entry.class_hash = 0` and attempts entry-point dispatch on class `0`. No entry point exists; the call reverts.
6. Re-deployment to the same address fails because `state_entry.nonce != 0`.
7. All user funds stored in `VaultContract`'s storage are permanently frozen.

---

**Relevant code locations:** [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo (L192-215)
```text
    tempvar contract_address = request.contract_address;
    let (state_entry: StateEntry*) = dict_read{dict_ptr=contract_state_changes}(
        key=contract_address
    );

    // Prepare execution context.
    // TODO(Yoni, 1/1/2026): change ExecutionContext to hold calldata_start, calldata_end.
    tempvar calldata_start = request.calldata_start;
    tempvar caller_execution_info = caller_execution_context.execution_info;
    tempvar caller_address = caller_execution_info.contract_address;
    tempvar execution_context: ExecutionContext* = new ExecutionContext(
        entry_point_type=ENTRY_POINT_TYPE_EXTERNAL,
        class_hash=state_entry.class_hash,
        calldata_size=request.calldata_end - calldata_start,
        calldata=calldata_start,
        execution_info=new ExecutionInfo(
            block_info=caller_execution_info.block_info,
            tx_info=caller_execution_info.tx_info,
            caller_address=caller_address,
            contract_address=contract_address,
            selector=request.selector,
        ),
        deprecated_tx_info=caller_execution_context.deprecated_tx_info,
    );
```

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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/deploy_contract.cairo (L44-54)
```text
    // Assert that we don't deploy to one of the reserved addresses.
    assert_not_zero(
        (contract_address - ORIGIN_ADDRESS) * (contract_address - BLOCK_HASH_CONTRACT_ADDRESS) * (
            contract_address - ALIAS_CONTRACT_ADDRESS
        ) * (contract_address - RESERVED_CONTRACT_ADDRESS),
    );

    local state_entry: StateEntry*;
    %{ GetContractAddressStateEntry %}
    assert state_entry.class_hash = UNINITIALIZED_CLASS_HASH;
    assert state_entry.nonce = 0;
```
