### Title
Unprivileged `execute_meta_tx_v0` Syscall Bypasses `__validate__` Enabling Arbitrary `__execute__` Invocation on Any Account — (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

---

### Summary

The `execute_meta_tx_v0` syscall is callable by any contract with no access control. It directly invokes the `__execute__` entry point of an arbitrary target account with fully attacker-controlled calldata and signature, while the OS never calls `__validate__` and never checks the nonce. This is a state-transition bypass: the normal two-phase account security model (`__validate__` → `__execute__`) is collapsed to a single phase that any unprivileged caller can trigger against any victim account.

---

### Finding Description

`execute_meta_tx_v0` is dispatched unconditionally from the syscall loop in `execute_syscalls.cairo`: [1](#0-0) 

Inside `execute_meta_tx_v0` (in `syscall_impls.cairo`), the only restriction enforced by the OS is that the target selector must equal `EXECUTE_ENTRY_POINT_SELECTOR`: [2](#0-1) 

A new `TxInfo` is then constructed with `version=0`, `nonce=0`, `max_fee=0`, and the **attacker-supplied** `signature_start`/`signature_end`: [3](#0-2) 

Execution is handed directly to `contract_call_helper`, which calls `select_execute_entry_point_func` → `execute_entry_point`. **`__validate__` is never invoked.** [4](#0-3) 

The OS's own `run_validate` explicitly skips validation for `version == 0`: [5](#0-4) 

And `check_and_increment_nonce` skips the nonce check for `version == 0`: [6](#0-5) 

`execute_meta_tx_v0` never calls either of these functions. The result: any contract can force execution of `__execute__` on any account with zero OS-level authentication.

---

### Impact Explanation

Standard StarkNet account contracts place all signature verification logic in `__validate__`. Their `__execute__` functions trust that `__validate__` has already run and simply execute the provided calls. Because `execute_meta_tx_v0` bypasses `__validate__` entirely, an attacker can supply arbitrary calldata (e.g., an ERC-20 `transfer` to the attacker's address) and have it executed on behalf of any victim account. This constitutes **direct loss of funds** — the entire token balance of any targeted account can be drained.

---

### Likelihood Explanation

The attack requires only:
1. Deploying a malicious contract (permissionless on StarkNet).
2. Submitting a single invoke transaction that calls `execute_meta_tx_v0` from within that contract.

No privileged role, leaked key, or operator cooperation is needed. The syscall is reachable by any unprivileged transaction sender.

---

### Recommendation

The OS must enforce the same two-phase security model for meta-transactions as for normal invoke transactions. Concretely, before calling `contract_call_helper` in `execute_meta_tx_v0`, the OS should invoke the target contract's `__validate__` entry point with the provided signature and the computed `meta_tx_hash`, and assert that it returns `VALIDATED`. Alternatively, restrict `execute_meta_tx_v0` so it can only be called from a designated, audited relayer contract class.

---

### Proof of Concept

1. Attacker deploys `MaliciousRelayer` — a contract whose `__execute__` issues the `META_TX_V0` syscall with:
   - `contract_address = victim_account`
   - `selector = EXECUTE_ENTRY_POINT_SELECTOR`
   - `calldata = [transfer_selector, attacker_address, victim_balance]`
   - `signature = []` (empty — `__validate__` will never run)

2. Attacker submits an invoke transaction calling `MaliciousRelayer.__execute__`.

3. The OS dispatches `execute_meta_tx_v0`:
   - Constructs `new_tx_info` with `version=0`, `nonce=0`, empty signature.
   - Calls `contract_call_helper` → `execute_entry_point` on `victim_account.__execute__`.
   - `__validate__` is never called; nonce is never checked.

4. `victim_account.__execute__` receives the attacker's calldata with no prior signature check and executes the token transfer.

5. Victim's entire balance is transferred to the attacker in a single transaction. [7](#0-6)

### Citations

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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/execute_transaction_utils.cairo (L64-67)
```text
    // Do not handle nonce for version 0.
    if (tx_info.version == 0) {
        return ();
    }
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/execute_transaction_utils.cairo (L127-130)
```text
    // Do not run "__validate__" for version 0.
    if (tx_execution_info.tx_info.version == 0) {
        return ();
    }
```
