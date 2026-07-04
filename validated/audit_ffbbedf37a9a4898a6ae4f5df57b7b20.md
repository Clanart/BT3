### Title
Unrestricted `contract_address` in `execute_meta_tx_v0` Allows Any Contract to Execute `__execute__` on Arbitrary Accounts, Bypassing `__validate__` — (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

---

### Summary

The `execute_meta_tx_v0` syscall handler in the StarkNet OS accepts a caller-controlled `contract_address` field from the syscall request with no restriction that it must equal the calling contract's own address. This allows any deployed contract to invoke `__execute__` on any victim account contract, with an attacker-supplied signature and calldata, while completely bypassing the `__validate__` entry point — because the synthesized `TxInfo` carries `version=0`, which causes `run_validate` to unconditionally skip signature verification.

---

### Finding Description

`execute_meta_tx_v0` in `syscall_impls.cairo` reads `contract_address` directly from the syscall request struct:

```cairo
local contract_address = request.contract_address;
``` [1](#0-0) 

There is no assertion that `contract_address` equals `caller_execution_context.execution_info.contract_address` or any other restriction. The only guard present is a check that `selector == EXECUTE_ENTRY_POINT_SELECTOR`:

```cairo
if (selector != EXECUTE_ENTRY_POINT_SELECTOR) {
    write_failure_response(remaining_gas=remaining_gas, failure_felt=ERROR_INVALID_ARGUMENT);
    return ();
}
``` [2](#0-1) 

The function then constructs a new `TxInfo` with `version=0`, `nonce=0`, and an attacker-supplied `signature_start/end`:

```cairo
tempvar new_tx_info = new TxInfo(
    version=0,
    account_contract_address=contract_address,
    ...
    signature_start=request.signature_start,
    signature_end=request.signature_end,
    ...
    nonce=0,
    ...
);
``` [3](#0-2) 

It then directly calls `contract_call_helper`, which invokes `select_execute_entry_point_func` — executing `__execute__` on the victim account without ever calling `run_validate`:

```cairo
contract_call_helper(
    remaining_gas=remaining_gas,
    block_context=block_context,
    execution_context=execution_context,
);
``` [4](#0-3) 

The `run_validate` function in `execute_transaction_utils.cairo` explicitly skips `__validate__` for version-0 transactions:

```cairo
if (tx_execution_info.tx_info.version == 0) {
    return ();
}
``` [5](#0-4) 

Similarly, `check_and_increment_nonce` skips nonce enforcement for version 0:

```cairo
if (tx_info.version == 0) {
    return ();
}
``` [6](#0-5) 

The standard StarkNet account pattern places all signature verification in `__validate__`. The `__execute__` entry point trusts that `__validate__` has already run and does not re-verify the signature. Because `execute_meta_tx_v0` bypasses `__validate__` entirely, any contract can execute arbitrary calldata through any victim account's `__execute__` without a valid signature.

---

### Impact Explanation

**Critical — Direct loss of funds.**

An attacker-controlled contract can call `execute_meta_tx_v0` targeting any victim account address with arbitrary calldata (e.g., `transfer` of all ERC-20 tokens to the attacker). Because `__validate__` is skipped and `__execute__` does not re-verify signatures in standard account implementations, the victim's account executes the attacker's calldata unconditionally. All assets held by or approved to the victim account are at risk of immediate theft.

---

### Likelihood Explanation

**High.** The attack requires only:
1. Deploying a malicious contract (permissionless on StarkNet).
2. Sending a single invoke transaction to the malicious contract.
3. The malicious contract issues the `execute_meta_tx_v0` syscall with the victim's address and drain calldata.

No privileged access, leaked keys, or social engineering is required. Any unprivileged transaction sender can execute this against any account on the network.

---

### Recommendation

Add an assertion inside `execute_meta_tx_v0` that the requested `contract_address` equals the calling contract's own address:

```cairo
// Only allow a contract to issue a meta-tx on behalf of itself.
assert contract_address = caller_execution_context.execution_info.contract_address;
```

This mirrors the fix applied to the VaultRouter bug: force the "owner" to be the actual caller rather than an arbitrary caller-supplied value. The check should be placed immediately after reading `contract_address` from the request, before any further processing. [7](#0-6) 

---

### Proof of Concept

1. Attacker deploys `MaliciousContract` on StarkNet.
2. Attacker sends an invoke transaction calling `MaliciousContract.__execute__`.
3. Inside `MaliciousContract.__execute__`, the contract issues a `META_TX_V0` syscall with:
   - `contract_address` = victim account address
   - `selector` = `EXECUTE_ENTRY_POINT_SELECTOR` (`__execute__`)
   - `calldata` = encoded multicall: `[transfer(attacker_address, victim_balance)]`
   - `signature_start/end` = any value (ignored, since `__validate__` is skipped)
4. The OS executes `execute_meta_tx_v0`:
   - Reads `contract_address = victim_address` from request (no restriction check).
   - Constructs `TxInfo` with `version=0`, `nonce=0`, attacker calldata.
   - Calls `contract_call_helper` → `select_execute_entry_point_func` → victim's `__execute__`.
   - `run_validate` is never called (version 0 path returns immediately).
5. Victim's `__execute__` runs the transfer calldata, sending all funds to the attacker.
6. All victim funds are drained in a single transaction.

The root cause — `contract_address` accepted from caller-controlled input without restriction — is structurally identical to the VaultRouter `owner` parameter bypass described in the reference report. [8](#0-7)

### Citations

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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/execute_transaction_utils.cairo (L65-67)
```text
    if (tx_info.version == 0) {
        return ();
    }
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/execute_transaction_utils.cairo (L128-130)
```text
    if (tx_execution_info.tx_info.version == 0) {
        return ();
    }
```
