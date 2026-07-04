### Title
Unprivileged Caller Can Force Arbitrary `__execute__` Invocation on Any Contract via `execute_meta_tx_v0` — (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

---

### Summary

The `execute_meta_tx_v0` syscall in the StarkNet OS allows **any executing contract** to invoke the `__execute__` entry point of **any other contract** with fully attacker-controlled `calldata` and `signature`, without any OS-level access control on the caller. This is a direct analog to the flash loan vulnerability: just as anyone could force any `IFlash` receiver to open a flash loan, any contract can force any other contract's `__execute__` to run with arbitrary inputs. If the target contract does not implement proper signature verification for the meta-tx hash scheme, an attacker can drain its funds.

---

### Finding Description

`execute_meta_tx_v0` in `syscall_impls.cairo` (lines 286–400) implements a syscall that:

1. Reads `contract_address`, `selector`, `calldata_start/end`, and `signature_start/end` entirely from the syscall request — all attacker-controlled.
2. Enforces only that `selector == EXECUTE_ENTRY_POINT_SELECTOR` (line 317–320); no restriction on which `contract_address` may be targeted.
3. Constructs a brand-new `TxInfo` for the target with:
   - `version = 0`
   - `nonce = 0`
   - `account_contract_address = contract_address` (the target itself)
   - `caller_address = ORIGIN_ADDRESS` (address 0 — the OS, not the actual triggering contract)
   - `signature_start/end` = attacker-supplied values
   - `transaction_hash = meta_tx_hash` computed only from `(contract_address, selector, calldata, chain_id)` — no binding to the outer transaction's sender
4. Calls `contract_call_helper` which dispatches into the target's `__execute__` entry point with this fabricated context. [1](#0-0) 

The OS **never checks** whether the calling contract has any relationship to the target `contract_address`. The `caller_address` exposed to the target is `ORIGIN_ADDRESS` (0), not the actual triggering contract, so the target cannot use `get_execution_info` to identify who triggered the meta-tx. [2](#0-1) 

Compare with `execute_call_contract`, which explicitly **blocks** calling `__execute__` via the normal `call_contract` path (lines 187–190), making `meta_tx_v0` the only OS-sanctioned path to invoke `__execute__` on an arbitrary target: [3](#0-2) 

The syscall is reachable from `execute_syscalls` as the final dispatch branch, callable by any contract during execution: [4](#0-3) 

---

### Impact Explanation

**Critical — Direct loss of funds.**

An attacker deploys a malicious contract `M`. `M` issues a `META_TX_V0` syscall targeting victim contract `V` with:
- `calldata` encoding a token transfer to the attacker's address
- `signature` set to any value (e.g., empty or garbage)

The OS calls `V.__execute__` with `account_contract_address = V`, `caller_address = 0`, `version = 0`, `nonce = 0`, and the attacker's calldata and signature. If `V.__execute__` does not validate the signature against the meta-tx hash scheme (e.g., it is a contract that was not designed with meta-tx awareness, or it skips validation for `version=0`), the transfer executes and all token balances held by `V` are drained.

The `caller_address = ORIGIN_ADDRESS` provides no useful signal to `V` — it cannot distinguish a legitimate meta-tx from an attacker-triggered one using standard `get_execution_info`.

---

### Likelihood Explanation

**Moderate-to-High.**

- `META_TX_V0` is a new syscall. Existing account contracts were not designed with the expectation that their `__execute__` could be invoked by an arbitrary third-party contract.
- Many deployed contracts may implement `__execute__` with signature checks tied to the standard transaction hash scheme (Poseidon-based v3 hash), not the Pedersen-based `meta_tx_v0` hash. Such contracts would receive an unexpected hash and may fail in unpredictable ways, or silently accept if their validation logic has edge cases for `version=0`.
- The OS provides no documentation, no example of a safe `__execute__` implementation under meta-tx, and no warning that `__execute__` is now externally triggerable by any contract.
- The `nonce = 0` in the fabricated `TxInfo` means contracts that skip nonce checks for `version=0` (a common pattern for legacy compatibility) are immediately vulnerable.

---

### Recommendation

1. **Restrict the caller**: Enforce that `meta_tx_v0` can only be issued by the target contract itself (i.e., `caller_execution_context.execution_info.contract_address == request.contract_address`), eliminating the ability for a third party to trigger it on behalf of another contract.
2. **Expose the triggering contract**: If third-party triggering is intentional, pass the actual triggering contract's address into the fabricated `TxInfo` (e.g., as `caller_address`) so the target can implement access control.
3. **Documentation and safe examples**: Publish explicit documentation warning that `__execute__` is now externally invocable via `meta_tx_v0`, and provide reference implementations showing how to safely validate the meta-tx hash and reject unauthorized callers.

---

### Proof of Concept

```
// Attacker's malicious contract (pseudocode)
fn __execute__(calldata):
    // Issue META_TX_V0 syscall:
    syscall meta_tx_v0(
        contract_address = VICTIM_ADDRESS,
        selector         = EXECUTE_ENTRY_POINT_SELECTOR,  // __execute__
        calldata         = [transfer(ATTACKER_ADDRESS, VICTIM_BALANCE)],
        signature        = []  // empty / garbage
    )
```

**OS execution path:**

1. `execute_syscalls` dispatches to `execute_meta_tx_v0` (line 343–344 of `execute_syscalls.cairo`).
2. `execute_meta_tx_v0` constructs `new_tx_info` with `version=0`, `nonce=0`, `account_contract_address=VICTIM`, `caller_address=0`, attacker's `calldata` and `signature`, and a freshly computed `meta_tx_hash`.
3. `contract_call_helper` invokes `VICTIM.__execute__` with this context.
4. If `VICTIM.__execute__` does not validate the signature against `meta_tx_hash`, the transfer executes.
5. All funds held by `VICTIM` are transferred to the attacker. [5](#0-4)

### Citations

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo (L187-190)
```text
    if (request.selector == EXECUTE_ENTRY_POINT_SELECTOR) {
        write_failure_response(remaining_gas=remaining_gas, failure_felt=ERROR_INVALID_ARGUMENT);
        return ();
    }
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo (L286-320)
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
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo (L329-399)
```text
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
