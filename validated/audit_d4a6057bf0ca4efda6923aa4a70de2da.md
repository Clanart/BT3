### Title
Missing Declared-Class Validation in `execute_replace_class` Allows Permanent Freezing of Contract Funds - (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

---

### Summary

The `execute_replace_class` syscall handler in the StarkNet OS updates a contract's class hash in `contract_state_changes` without verifying that the replacement hash corresponds to any declared contract class. An unprivileged contract can call `replace_class` with an arbitrary, undeclared hash, permanently bricking itself. Any funds held by that contract become irretrievably frozen.

---

### Finding Description

In `syscall_impls.cairo`, the function `execute_replace_class` (lines 878–916) performs the following steps:

1. Reads the `ReplaceClassRequest` from the syscall pointer.
2. Deducts gas.
3. Reads the current `StateEntry` for the calling contract.
4. Writes a new `StateEntry` with the caller-supplied `class_hash` into `contract_state_changes`.
5. Appends a revert-log entry recording the old class hash.

Critically, step 4 is performed with **no validation** that the new `class_hash` is a declared class. The developers themselves acknowledge this gap with an explicit TODO at line 898:

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

Once this state change is committed (i.e., the transaction is not reverted), the contract's on-chain class hash permanently points to an undeclared hash. Every subsequent transaction that targets this contract requires the OS to resolve the compiled class from `compiled_class_facts_bundle` (populated from `OsGlobalContext.compiled_class_facts_bundle`). Because no compiled class exists for the bogus hash, the OS cannot execute the contract — it is permanently bricked. [2](#0-1) 

The `charge_fee` path in `transaction_impls.cairo` also reads the class hash from `contract_state_changes` at fee-charge time:

```cairo
local fee_token_address = block_context.os_global_context.starknet_os_config.fee_token_address;
let (fee_state_entry: StateEntry*) = dict_read{dict_ptr=contract_state_changes}(
    key=fee_token_address
);
...
local execution_context: ExecutionContext = ExecutionContext(
    ...
    class_hash=fee_state_entry.class_hash,
    ...
);
``` [3](#0-2) 

If the fee token contract itself were to call `replace_class` with an undeclared hash (e.g., via a governance path), every subsequent fee-charging call across the entire network would fail, escalating the impact to a total network halt.

The structural parallel to the external report is exact:

| External Report (MarginAccountHelper) | StarkNet OS (`execute_replace_class`) |
|---|---|
| `syncDeps` updates `marginAccount` / `insuranceFund` references | `replace_class` updates `class_hash` in `contract_state_changes` |
| No new ERC-20 approvals granted to new contract addresses | No check that the new `class_hash` is a declared class |
| All fee/transfer calls revert → contract bricked | All subsequent calls to the contract fail → contract bricked |
| Funds locked in dependent contracts | Funds locked inside the bricked contract |

---

### Impact Explanation

**Critical — Permanent freezing of funds.**

Once a contract's class hash is set to an undeclared value and the transaction is not reverted, the contract is permanently inaccessible. The OS cannot resolve the compiled class, so no entry point can be executed. Any ERC-20 tokens, ETH, or other assets held in the contract's storage are irretrievably frozen. There is no recovery path: `replace_class` itself requires calling the contract, which is now impossible.

---

### Likelihood Explanation

The `replace_class` syscall is available to every Sierra contract without any privilege requirement. The missing check is a single-line omission explicitly flagged by the development team as a TODO. Any contract that (a) holds user funds and (b) exposes a code path that calls `replace_class` — whether through a governance mechanism, an upgrade proxy, or a direct attacker-controlled call — is vulnerable. The attack requires no special role, no leaked key, and no external dependency.

---

### Recommendation

Inside `execute_replace_class`, after reading `class_hash` from the request, verify that `class_hash` exists in `contract_class_changes` (i.e., it has been declared in the current or a prior block) before writing the new `StateEntry`. Concretely:

```cairo
// Verify the replacement class is declared.
let (compiled_hash) = dict_read{dict_ptr=contract_class_changes}(key=class_hash);
with_attr error_message("Class hash is not declared.") {
    assert_not_zero(compiled_hash);
}
```

This mirrors the check already performed implicitly during `execute_declare_transaction`, where `prev_value=0` enforces that a class may be declared only once. [4](#0-3) 

---

### Proof of Concept

1. **Deploy** a contract `Victim` that holds user funds and exposes a public function `brick()` which calls the `replace_class` syscall with a hardcoded, never-declared hash (e.g., `0xdeadbeef`).
2. **Call** `brick()` on `Victim`. The OS executes `execute_replace_class`:
   - Gas is deducted.
   - `contract_state_changes[Victim.address].class_hash` is set to `0xdeadbeef`.
   - A revert-log entry is written.
   - The syscall returns success.
3. The transaction completes without revert; the state diff is committed.
4. **Attempt** any subsequent call to `Victim` (e.g., a withdrawal). The OS reads `class_hash = 0xdeadbeef` from state, attempts to locate the compiled class in `compiled_class_facts_bundle`, finds nothing, and the hint fails — the call cannot be included in any provable block.
5. All funds inside `Victim` are permanently frozen with no recovery path. [5](#0-4)

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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/block_context.cairo (L25-39)
```text
struct OsGlobalContext {
    // OS config available globally for all blocks.
    starknet_os_config: StarknetOsConfig,
    starknet_os_config_hash: felt,
    virtual_os_config_hash: felt,
    // Compiled class facts available globally for all blocks.
    compiled_class_facts_bundle: CompiledClassFactsBundle,

    // Parameters for select_builtins.
    builtin_params: BuiltinParams*,
    // A function pointer to the 'execute_syscalls' function.
    execute_syscalls_ptr: felt*,
    // A function pointer to the 'execute_deprecated_syscalls' function.
    execute_deprecated_syscalls_ptr: felt*,
}
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L138-158)
```text
    local fee_token_address = block_context.os_global_context.starknet_os_config.fee_token_address;
    let (fee_state_entry: StateEntry*) = dict_read{dict_ptr=contract_state_changes}(
        key=fee_token_address
    );
    let (__fp__, _) = get_fp_and_pc();
    // Use block_info directly from block_context, so that charge_fee will always run in
    // execute-mode rather than validate-mode.
    local execution_context: ExecutionContext = ExecutionContext(
        entry_point_type=ENTRY_POINT_TYPE_EXTERNAL,
        class_hash=fee_state_entry.class_hash,
        calldata_size=TransferCallData.SIZE,
        calldata=&calldata,
        execution_info=new ExecutionInfo(
            block_info=block_context.block_info_for_execute,
            tx_info=tx_info,
            caller_address=tx_info.account_contract_address,
            contract_address=fee_token_address,
            selector=TRANSFER_ENTRY_POINT_SELECTOR,
        ),
        deprecated_tx_info=tx_execution_context.deprecated_tx_info,
    );
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L815-819)
```text
    // Note that prev_value=0 enforces that a class may be declared only once.
    assert_not_zero(compiled_class_hash);
    dict_update{dict_ptr=contract_class_changes}(
        key=[class_hash_ptr], prev_value=0, new_value=compiled_class_hash
    );
```
