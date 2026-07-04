### Title
`execute_meta_tx_v0` Allows Any Contract to Invoke `__execute__` on Arbitrary Account Contracts Without `__validate__`, Bypassing Signature Authorization — (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

---

### Summary

The `execute_meta_tx_v0` syscall in the StarkNet OS allows any unprivileged contract to call `__execute__` on any target account contract with attacker-controlled calldata, without invoking `__validate__`. Because standard account contracts perform signature verification exclusively in `__validate__` and not in `__execute__`, this bypasses the only authorization gate protecting account funds. The attacker can drain any victim account at the cost of a single transaction.

---

### Finding Description

The normal OS transaction flow enforces a strict ordering: `__validate__` is called first (signature check), then `__execute__` (fund movement). This is implemented in `execute_invoke_function_transaction`: [1](#0-0) 

`run_validate` calls `__validate__` and asserts it returns `VALIDATED` before `__execute__` is ever reached.

The `execute_meta_tx_v0` syscall breaks this invariant entirely. It is reachable by any contract via the syscall dispatcher with no caller restriction: [2](#0-1) 

Inside `execute_meta_tx_v0`, the OS:

1. Accepts any `contract_address` as the target (no ownership check).
2. Enforces only that `selector == EXECUTE_ENTRY_POINT_SELECTOR` — meaning it always targets `__execute__`.
3. Sets `caller_address = ORIGIN_ADDRESS` (i.e., `0`), which is exactly what standard account contracts check to confirm they are being called by the protocol.
4. Sets `nonce = 0` and `max_fee = 0` — no nonce check, no fee charged to the victim.
5. Provides an attacker-supplied `signature` in the new `TxInfo`.
6. **Never calls `__validate__`** on the target contract. [3](#0-2) 

The resulting `ExecutionContext` is passed directly to `contract_call_helper` → `select_execute_entry_point_func`, which executes the target's `__execute__` entry point: [4](#0-3) 

Standard StarkNet account contracts (OpenZeppelin and equivalents) verify the signature only in `__validate__`. Their `__execute__` functions check only that `get_caller_address() == 0`. Since `meta_tx_v0` sets `caller_address = ORIGIN_ADDRESS = 0`, this check passes unconditionally, and `__execute__` runs with the attacker's calldata.

The meta-tx hash is computed from `(contract_address, selector, calldata, chain_id)` — it does **not** include the caller's address or any nonce: [5](#0-4) 

This means the attacker can pre-compute the hash for any victim and any calldata, and the provided signature is irrelevant because `__execute__` never verifies it.

---

### Impact Explanation

An attacker can execute arbitrary calls on behalf of any victim account contract, including:

- Transferring all ERC-20 tokens from the victim's account to the attacker.
- Approving arbitrary spenders over the victim's assets.
- Calling `replace_class` on the victim's account to brick it permanently.

**Impact: Critical — Direct loss of funds / Permanent freezing of funds.**

---

### Likelihood Explanation

The attack requires no special privileges, no leaked keys, no trusted role, and no social engineering. Any unprivileged user can:

1. Deploy a contract that issues a `meta_tx_v0` syscall targeting the victim.
2. Submit a single transaction calling that contract.

The cost is only the gas for the attacker's outer transaction. The attack is repeatable (no nonce increment on the victim) until the victim's account is drained.

---

### Recommendation

- **Restrict the caller**: `execute_meta_tx_v0` should only be callable by the target contract itself (i.e., `caller_execution_context.execution_info.contract_address == request.contract_address`), preventing third-party contracts from targeting arbitrary victims.
- **Alternatively, call `__validate__` first**: Before calling `__execute__` on the target, invoke the target's `__validate__` entry point with the meta-tx hash and the provided signature, mirroring the normal transaction flow.
- **Or remove the syscall**: If `meta_tx_v0` is not yet in production use, remove it until a safe design is established.

---

### Proof of Concept

```
// Attacker deploys AttackerContract:
func attack{syscall_ptr: felt*}(victim: felt, transfer_calldata: felt*, calldata_len: felt) {
    // Calls __execute__ on victim with attacker-controlled calldata.
    // Signature is empty — victim's __execute__ never checks it.
    meta_tx_v0(
        contract_address=victim,
        selector=EXECUTE_ENTRY_POINT_SELECTOR,
        calldata_start=transfer_calldata,
        calldata_end=transfer_calldata + calldata_len,
        signature_start=cast(0, felt*),
        signature_end=cast(0, felt*),
    );
}
```

**Execution trace:**

1. Attacker submits a transaction calling `AttackerContract.attack(victim, [transfer_all_to_attacker])`.
2. OS dispatches `META_TX_V0_SELECTOR` → `execute_meta_tx_v0`.
3. OS constructs `TxInfo(version=0, account_contract_address=victim, nonce=0, max_fee=0, caller_address=0)`.
4. OS calls `victim.__execute__(transfer_calldata)` with `caller_address=0`.
5. Victim's `__execute__` passes the caller check (`caller == 0`) and executes the transfer.
6. Victim's funds are transferred to the attacker.
7. Attack is repeatable — victim's nonce is never incremented by `meta_tx_v0`. [6](#0-5)

### Citations

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L326-348)
```text
    with remaining_gas {
        cap_remaining_gas(max_gas=VALIDATE_MAX_SIERRA_GAS);
        let pre_validate_gas = remaining_gas;
        run_validate(block_context=block_context, tx_execution_context=tx_execution_context);
    }
    let validate_gas_consumed = pre_validate_gas - remaining_gas;
    tempvar remaining_gas = initial_user_gas_bound - validate_gas_consumed;

    let updated_tx_execution_context = update_class_hash_in_execution_context(
        execution_context=tx_execution_context
    );

    local is_reverted;
    %{ IsReverted %}
    check_is_reverted(is_reverted);
    if (is_reverted == FALSE) {
        // Execute only non-reverted transactions.
        with remaining_gas {
            cap_remaining_gas(max_gas=EXECUTE_MAX_SIERRA_GAS);
            non_reverting_select_execute_entry_point_func(
                block_context=block_context, execution_context=updated_tx_execution_context
            );
        }
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/execute_syscalls.cairo (L343-350)
```text
    assert selector = META_TX_V0_SELECTOR;
    execute_meta_tx_v0(block_context=block_context, caller_execution_context=execution_context);
    %{ OsLoggerExitSyscall %}
    return execute_syscalls(
        block_context=block_context,
        execution_context=execution_context,
        syscall_ptr_end=syscall_ptr_end,
    );
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo (L286-400)
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
}
```
