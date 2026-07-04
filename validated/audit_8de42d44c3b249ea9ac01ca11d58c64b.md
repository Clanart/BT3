### Title
Unauthorized `__execute__` Invocation via `execute_meta_tx_v0` Bypasses `__validate__` Authorization — (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

---

### Summary

The `execute_meta_tx_v0` syscall handler in the StarkNet OS accepts an attacker-controlled `contract_address` field from the syscall request without any check that the calling contract has authority over that address. This allows any unprivileged contract to invoke `__execute__` on an arbitrary victim account, completely bypassing the `__validate__` authorization step that the OS normally enforces. Account contracts that rely on `__validate__` for signature verification — the standard pattern — are directly exploitable, resulting in loss of funds.

---

### Finding Description

In `execute_meta_tx_v0` (lines 286–400 of `syscall_impls.cairo`), the OS reads `contract_address` and `calldata` directly from the attacker-supplied `MetaTxV0Request` struct:

```cairo
local contract_address = request.contract_address;   // attacker-controlled
local selector = request.selector;                   // must be __execute__
``` [1](#0-0) 

The only gate on `selector` is that it must equal `EXECUTE_ENTRY_POINT_SELECTOR`:

```cairo
if (selector != EXECUTE_ENTRY_POINT_SELECTOR) {
    write_failure_response(...);
    return ();
}
``` [2](#0-1) 

There is **no check** that the calling contract has any relationship to `contract_address`. The OS then constructs a new `TxInfo` with:
- `account_contract_address = contract_address` (victim)
- `signature = request.signature_start/end` (attacker-supplied)
- `transaction_hash = compute_meta_tx_v0_hash(contract_address, selector, calldata, chain_id)` (attacker-controlled inputs)
- `nonce = 0` (no replay protection) [3](#0-2) 

And executes `contract_address.__execute__` directly via `contract_call_helper`, with `caller_address = ORIGIN_ADDRESS (0)`:

```cairo
tempvar execution_context: ExecutionContext* = new ExecutionContext(
    ...
    execution_info=new ExecutionInfo(
        ...
        caller_address=ORIGIN_ADDRESS,
        contract_address=contract_address,
        selector=selector,
    ),
    ...
);
``` [4](#0-3) 

Critically, `__validate__` is **never called**. In the normal OS transaction flow (`execute_invoke_function_transaction`), the OS enforces `__validate__` → `__execute__` sequencing:

```cairo
run_validate(block_context=block_context, tx_execution_context=tx_execution_context);
...
non_reverting_select_execute_entry_point_func(
    block_context=block_context, execution_context=updated_tx_execution_context
);
``` [5](#0-4) 

`execute_meta_tx_v0` breaks this protocol guarantee entirely. The syscall is reachable from `execute_syscalls` with no caller restriction:

```cairo
assert selector = META_TX_V0_SELECTOR;
execute_meta_tx_v0(block_context=block_context, caller_execution_context=execution_context);
``` [6](#0-5) 

---

### Impact Explanation

**Critical — Direct loss of funds.**

The standard StarkNet account contract pattern places signature verification exclusively in `__validate__`, with `__execute__` simply executing the provided calls. Because `execute_meta_tx_v0` bypasses `__validate__`, an attacker can call any victim account's `__execute__` with attacker-crafted calldata (e.g., `transfer(attacker, victim_balance)`). Since `__execute__` runs in the context of `contract_address` (the victim), all storage writes and token transfers originate from the victim account. The attacker-supplied signature is never cryptographically verified by the OS; it is merely passed through to `tx_info.signature`, which `__execute__` does not check in the standard pattern.

---

### Likelihood Explanation

**High.** The attack path requires only:
1. Deploying a malicious contract (permissionless on StarkNet).
2. Submitting an invoke transaction to that contract.
3. The contract emits a `META_TX_V0` syscall with `contract_address = victim` and `calldata = [transfer_to_attacker]`.

No privileged access, leaked keys, or operator cooperation is required. Any unprivileged L2 transaction sender can execute this attack against any account that follows the standard `__validate__`/`__execute__` separation.

---

### Recommendation

Add an authorization check inside `execute_meta_tx_v0` that enforces the calling contract is the same as `contract_address`:

```cairo
// Enforce that only the target contract itself can issue a meta-tx on its own behalf.
assert caller_execution_info.contract_address = contract_address;
```

Alternatively, require that `__validate__` is called (with the meta-tx hash and provided signature) before `__execute__` is dispatched, mirroring the normal transaction flow.

---

### Proof of Concept

1. Attacker deploys `MaliciousRelayer` contract with the following logic in its `__execute__`:
   ```
   meta_tx_v0(
       contract_address = VICTIM_ACCOUNT,
       selector        = __execute__,
       calldata        = [transfer(ATTACKER, VICTIM_BALANCE)],
       signature       = []   // empty; __execute__ won't check it
   )
   ```
2. Attacker submits an invoke transaction targeting `MaliciousRelayer.__execute__`.
3. The OS processes the invoke: calls `MaliciousRelayer.__validate__` (passes trivially), then `MaliciousRelayer.__execute__`.
4. Inside `MaliciousRelayer.__execute__`, the `META_TX_V0` syscall fires.
5. `execute_meta_tx_v0` in `syscall_impls.cairo` (line 286) reads `contract_address = VICTIM_ACCOUNT` from the request with no authorization check.
6. The OS calls `VICTIM_ACCOUNT.__execute__` with `calldata = [transfer(ATTACKER, VICTIM_BALANCE)]` and an empty signature, bypassing `VICTIM_ACCOUNT.__validate__` entirely.
7. `VICTIM_ACCOUNT.__execute__` executes the transfer; funds move to the attacker.

Root cause line: `local contract_address = request.contract_address;` at [7](#0-6) 
with no subsequent check that `caller_execution_info.contract_address == contract_address`.

### Citations

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo (L312-313)
```text
    local contract_address = request.contract_address;
    local selector = request.selector;
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo (L317-320)
```text
    if (selector != EXECUTE_ENTRY_POINT_SELECTOR) {
        write_failure_response(remaining_gas=remaining_gas, failure_felt=ERROR_INVALID_ARGUMENT);
        return ();
    }
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo (L343-363)
```text
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
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo (L366-378)
```text
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
```

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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/execute_syscalls.cairo (L343-344)
```text
    assert selector = META_TX_V0_SELECTOR;
    execute_meta_tx_v0(block_context=block_context, caller_execution_context=execution_context);
```
