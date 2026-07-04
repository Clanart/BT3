### Title
Missing Caller-to-Target Validation in `execute_meta_tx_v0` Allows Any Contract to Invoke `__execute__` on Arbitrary Accounts - (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

---

### Summary

`execute_meta_tx_v0` in the StarkNet OS does not verify that the contract invoking the syscall is the same as the `contract_address` specified in the request. This is the direct analog of H-02: just as `primarySaleWithPermit()` failed to assert `msg.sender == permitSignature.owner`, the OS fails to assert `request.contract_address == caller_execution_context.execution_info.contract_address`. Any contract can invoke `__execute__` on any other account contract with an attacker-supplied signature and calldata, bypassing the normal transaction authorization flow.

---

### Finding Description

`execute_meta_tx_v0` is the **only** OS-level path that can invoke `__execute__` on another contract. The `call_contract` syscall explicitly blocks this:

```cairo
if (request.selector == EXECUTE_ENTRY_POINT_SELECTOR) {
    write_failure_response(remaining_gas=remaining_gas, failure_felt=ERROR_INVALID_ARGUMENT);
    return ();
}
``` [1](#0-0) 

`execute_meta_tx_v0` then reads the target `contract_address` directly from the attacker-controlled request:

```cairo
local contract_address = request.contract_address;
local selector = request.selector;
local caller_execution_info: ExecutionInfo* = caller_execution_context.execution_info;
local old_tx_info: TxInfo* = caller_execution_info.tx_info;
``` [2](#0-1) 

There is **no assertion** anywhere in the function that `contract_address == caller_execution_info.contract_address`. The function proceeds to build a new `TxInfo` with `version=0`, `account_contract_address=contract_address`, and the attacker-supplied `signature_start`/`signature_end`:

```cairo
tempvar new_tx_info = new TxInfo(
    version=0,
    account_contract_address=contract_address,
    ...
    signature_start=request.signature_start,
    signature_end=request.signature_end,
    transaction_hash=meta_tx_hash,
    ...
);
``` [3](#0-2) 

It then executes `__execute__` on the victim contract with `caller_address=ORIGIN_ADDRESS` (address 0):

```cairo
execution_info=new ExecutionInfo(
    ...
    caller_address=ORIGIN_ADDRESS,
    contract_address=contract_address,
    selector=selector,
),
``` [4](#0-3) 

Critically, `__validate__` is **skipped** for version-0 transactions by the OS:

```cairo
// Do not run "__validate__" for version 0.
if (tx_execution_info.tx_info.version == 0) {
    return ();
}
``` [5](#0-4) 

The entire authorization chain for the victim account is therefore bypassed at the OS level. The only remaining protection is whatever signature check the victim's `__execute__` performs internally.

---

### Impact Explanation

**Critical — Direct loss of funds.**

For any v0 account contract whose `__execute__` does not independently re-verify the ECDSA signature (e.g., it relies on the OS having already run `__validate__`, or it trusts `caller_address == 0` as an OS-level guarantee), an attacker can:

1. Deploy a malicious contract.
2. Issue a transaction that calls `meta_tx_v0` targeting the victim account with crafted calldata (e.g., a transfer of all ERC-20 tokens to the attacker) and an empty or arbitrary signature.
3. The OS executes `__execute__` on the victim with the attacker's calldata and signature, with no OS-level authorization check having been performed.

Funds held by or approved to the victim account can be drained in a single transaction.

---

### Likelihood Explanation

**Medium.**

- The `meta_tx_v0` syscall is a new, non-standard mechanism. v0 account contracts that predate it may not have been written with the assumption that `__execute__` could be called by an arbitrary third-party contract via this path.
- The OS's own `run_validate` explicitly skips `__validate__` for version-0, so account authors may have assumed the OS enforces authorization before reaching `__execute__`.
- Any deployed v0 account that does not independently re-verify its signature in `__execute__` is immediately exploitable by any other contract on the network.

---

### Recommendation

Add an assertion inside `execute_meta_tx_v0` that the target `contract_address` equals the address of the calling contract:

```cairo
// Enforce that a contract may only issue a meta-tx on behalf of itself.
assert contract_address = caller_execution_info.contract_address;
```

This mirrors the fix recommended in H-02: verify that the initiating party is the same as the authorized owner before proceeding with privileged execution.

---

### Proof of Concept

1. Victim: a deployed v0 account contract at address `V` holding ERC-20 tokens. Its `__execute__` does not re-verify the ECDSA signature (it trusts the OS flow).
2. Attacker: deploys contract `A`.
3. Attacker sends an invoke transaction calling `A.__execute__`, which internally issues the `meta_tx_v0` syscall with:
   - `contract_address = V`
   - `selector = EXECUTE_ENTRY_POINT_SELECTOR`
   - `calldata` = a transfer of all tokens from `V` to `A`
   - `signature_start = signature_end` (empty signature)
4. The OS executes `execute_meta_tx_v0`:
   - No check that `V == A` is performed.
   - `__validate__` on `V` is skipped (version=0).
   - `V.__execute__` is called with the attacker's calldata and empty signature.
5. `V.__execute__` runs the transfer without detecting the unauthorized invocation.
6. All funds in `V` are transferred to `A`. [6](#0-5) [7](#0-6)

### Citations

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo (L187-190)
```text
    if (request.selector == EXECUTE_ENTRY_POINT_SELECTOR) {
        write_failure_response(remaining_gas=remaining_gas, failure_felt=ERROR_INVALID_ARGUMENT);
        return ();
    }
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo (L286-399)
```text
func execute_meta_tx_v0{
    range_check_ptr,
    syscall_ptr: felt*,
    builtin_ptrs: BuiltinPointers*,
    contract_state_changes: DictAccess*,
    contract_class_changes: DictAccess*,
    revert_log: RevertLogEntry*,
    outputs: OsCarriedOutputs*,
}(block_context: BlockContext*, caller_execution_context: ExecutionContext*) {
    alloc_locals;

    let request = cast(syscall_ptr + RequestHeader.SIZE, MetaTxV0Request*);
    local calldata_start: felt* = request.calldata_start;
    local calldata_size = request.calldata_end - calldata_start;

    let specific_base_gas_cost = (
        META_TX_V0_GAS_COST + META_TX_V0_CALLDATA_FACTOR_GAS_COST * calldata_size
    );
    let (success, remaining_gas) = reduce_syscall_base_gas(
        specific_base_gas_cost=specific_base_gas_cost, request_struct_size=MetaTxV0Request.SIZE
    );
    if (success == FALSE) {
        // Not enough gas to execute the syscall.
        return ();
    }

    local contract_address = request.contract_address;
    local selector = request.selector;
    local caller_execution_info: ExecutionInfo* = caller_execution_context.execution_info;
    local old_tx_info: TxInfo* = caller_execution_info.tx_info;

    if (selector != EXECUTE_ENTRY_POINT_SELECTOR) {
        write_failure_response(remaining_gas=remaining_gas, failure_felt=ERROR_INVALID_ARGUMENT);
        return ();
    }

    // Sanity check: Verify that `signature` is a valid Sierra array.
    assert_nn_le(request.signature_end - request.signature_start, SIERRA_ARRAY_LEN_BOUND - 1);

    let (state_entry: StateEntry*) = dict_read{dict_ptr=contract_state_changes}(
        key=contract_address
    );

    // Compute the meta-transaction hash.
    let pedersen_ptr = builtin_ptrs.selectable.pedersen;
    with pedersen_ptr {
        let meta_tx_hash = compute_meta_tx_v0_hash(
            contract_address=contract_address,
            entry_point_selector=selector,
            calldata=calldata_start,
            calldata_size=calldata_size,
            chain_id=old_tx_info.chain_id,
        );
    }
    update_pedersen_in_builtin_ptrs(pedersen_ptr=pedersen_ptr);

    // Prepare execution context.
    tempvar new_tx_info = new TxInfo(
        version=0,
        account_contract_address=contract_address,
        max_fee=0,
        signature_start=request.signature_start,
        signature_end=request.signature_end,
        transaction_hash=meta_tx_hash,
        chain_id=old_tx_info.chain_id,
        nonce=0,
        resource_bounds_start=cast(0, ResourceBounds*),
        resource_bounds_end=cast(0, ResourceBounds*),
        tip=0,
        paymaster_data_start=cast(0, felt*),
        paymaster_data_end=cast(0, felt*),
        nonce_data_availability_mode=0,
        fee_data_availability_mode=0,
        account_deployment_data_start=cast(0, felt*),
        account_deployment_data_end=cast(0, felt*),
        proof_facts_start=cast(0, felt*),
        proof_facts_end=cast(0, felt*),
    );

    let (deprecated_tx_info_ptr: DeprecatedTxInfo*) = alloc();
    tempvar execution_context: ExecutionContext* = new ExecutionContext(
        entry_point_type=ENTRY_POINT_TYPE_EXTERNAL,
        class_hash=state_entry.class_hash,
        calldata_size=calldata_size,
        calldata=calldata_start,
        execution_info=new ExecutionInfo(
            block_info=caller_execution_info.block_info,
            tx_info=new_tx_info,
            caller_address=ORIGIN_ADDRESS,
            contract_address=contract_address,
            selector=selector,
        ),
        deprecated_tx_info=deprecated_tx_info_ptr,
    );
    fill_deprecated_tx_info(tx_info=new_tx_info, dst=execution_context.deprecated_tx_info);

    // Since we process the revert log backwards, entries before this point belong to the calling
    // contract.
    assert [revert_log] = RevertLogEntry(
        selector=CHANGE_CONTRACT_ENTRY, value=caller_execution_info.contract_address
    );
    let revert_log = &revert_log[1];

    contract_call_helper(
        remaining_gas=remaining_gas,
        block_context=block_context,
        execution_context=execution_context,
    );

    // Entries before this point belong to the callee.
    assert [revert_log] = RevertLogEntry(selector=CHANGE_CONTRACT_ENTRY, value=contract_address);
    let revert_log = &revert_log[1];

    return ();
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/execute_transaction_utils.cairo (L127-130)
```text
    // Do not run "__validate__" for version 0.
    if (tx_execution_info.tx_info.version == 0) {
        return ();
    }
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/execute_syscalls.cairo (L343-351)
```text
    assert selector = META_TX_V0_SELECTOR;
    execute_meta_tx_v0(block_context=block_context, caller_execution_context=execution_context);
    %{ OsLoggerExitSyscall %}
    return execute_syscalls(
        block_context=block_context,
        execution_context=execution_context,
        syscall_ptr_end=syscall_ptr_end,
    );
}
```
