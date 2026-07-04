### Title
Missing Nonce in `meta_tx_v0` Hash Allows Signature Replay — (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/transaction_hash/transaction_hash.cairo`)

---

### Summary

`compute_meta_tx_v0_hash` computes a transaction hash that omits any nonce or replay-prevention field. Because the hash is a pure function of `(contract_address, selector, calldata, chain_id)`, any signature produced over it is valid for every future invocation of `meta_tx_v0` with the same parameters. An unprivileged attacker who observes a legitimate `meta_tx_v0` call can replay the captured signature through a malicious contract to re-execute the victim account's `__execute__` entry point, draining funds.

---

### Finding Description

`compute_meta_tx_v0_hash` is defined as:

```cairo
func compute_meta_tx_v0_hash{pedersen_ptr: HashBuiltin*}(
    contract_address: felt,
    entry_point_selector: felt,
    calldata: felt*,
    calldata_size: felt,
    chain_id: felt,
) -> felt {
    let (tx_hash) = deprecated_get_transaction_hash{hash_ptr=pedersen_ptr}(
        ...
        additional_data_size=0,
        additional_data=cast(0, felt*),   // ← no nonce committed
    );
    return tx_hash;
}
``` [1](#0-0) 

The `additional_data` array (which is where the nonce is placed in all other transaction types, e.g. `compute_l1_handler_transaction_hash` passes `additional_data=&nonce`) is empty here. [2](#0-1) 

`execute_meta_tx_v0` in `syscall_impls.cairo` calls this function and then constructs a `new_tx_info` with `nonce=0` hardcoded:

```cairo
tempvar new_tx_info = new TxInfo(
    version=0,
    ...
    transaction_hash=meta_tx_hash,
    nonce=0,          // always zero, never checked or incremented
    ...
);
``` [3](#0-2) 

No nonce is stored, checked, or invalidated anywhere in `execute_meta_tx_v0`. The OS enforces nonce uniqueness only for regular account transactions via `check_and_increment_nonce`, which is never called for meta transactions. [4](#0-3) 

The called contract's `__execute__` entry point receives `tx_info.transaction_hash = meta_tx_hash` and `tx_info.signature = request.signature`. Standard account contracts verify the signature against `transaction_hash`. Because the hash is identical on every replay (same `contract_address`, `selector`, `calldata`, `chain_id`), the same signature passes verification every time. [5](#0-4) 

---

### Impact Explanation

**Critical — Direct loss of funds.**

A victim account contract's `__execute__` function (e.g., one that transfers ERC-20 tokens or ETH) can be re-invoked an unlimited number of times using a single captured signature. Each replay executes the same calldata (e.g., `transfer(attacker, amount)`), draining the victim's balance. Because `__validate__` is never called for meta transactions (the OS calls `__execute__` directly), the only protection is the signature check inside `__execute__`, which passes on every replay due to the static hash.

---

### Likelihood Explanation

Any unprivileged StarkNet user can:
1. Monitor the mempool or on-chain history for a `meta_tx_v0` syscall emitted by any transaction.
2. Extract `(contract_address, selector, calldata, signature)` from the observed call.
3. Deploy a trivial malicious contract that issues `meta_tx_v0` with those exact parameters.
4. Submit a transaction invoking that contract.

No privileged access, leaked key, or operator cooperation is required. The attack is fully permissionless and repeatable across blocks.

---

### Recommendation

Include a replay-prevention field in the meta-transaction hash. Two options:

1. **Explicit nonce**: Add a caller-supplied nonce to `MetaTxV0Request`, commit it into the hash via `additional_data`, and store/invalidate it in `contract_state_changes` after first use (analogous to `check_and_increment_nonce`).

2. **Outer transaction nonce binding**: Commit the outer transaction's nonce (`old_tx_info.nonce`) and the outer transaction's hash into the meta-transaction hash. This ties each meta-tx signature to exactly one outer transaction, preventing cross-transaction replay at zero storage cost.

Either approach mirrors how `compute_l1_handler_transaction_hash` already passes `additional_data=&nonce` to bind the hash to a unique value. [2](#0-1) 

---

### Proof of Concept

1. **Victim setup**: Account contract `A` at address `0xA` has funds. A legitimate caller invokes `meta_tx_v0` with `(contract_address=0xA, selector=EXECUTE, calldata=[transfer, attacker, 100], signature=SIG)`. The OS computes `H = hash(INVOKE_PREFIX, 0, 0xA, EXECUTE, calldata, 0, chain_id)` and calls `A.__execute__`. Signature `SIG` over `H` is valid; the transfer executes.

2. **Replay**: Attacker deploys contract `Evil` containing:
   ```
   meta_tx_v0(contract_address=0xA, selector=EXECUTE,
              calldata=[transfer, attacker, 100], signature=SIG)
   ```
3. Attacker submits a transaction calling `Evil`. The OS again computes the identical `H` (no nonce in the hash), passes `SIG` to `A.__execute__`, which verifies `SIG` against `H` — succeeds — and executes the transfer again.

4. Step 3 can be repeated in every block until `A`'s balance is zero. [6](#0-5) [1](#0-0)

### Citations

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/transaction_hash/transaction_hash.cairo (L220-238)
```text
func compute_l1_handler_transaction_hash{pedersen_ptr: HashBuiltin*}(
    execution_context: ExecutionContext*, chain_id: felt, nonce: felt
) -> felt {
    let (__fp__, _) = get_fp_and_pc();
    let (transaction_hash) = deprecated_get_transaction_hash{hash_ptr=pedersen_ptr}(
        tx_hash_prefix=L1_HANDLER_HASH_PREFIX,
        version=L1_HANDLER_VERSION,
        contract_address=execution_context.execution_info.contract_address,
        entry_point_selector=execution_context.execution_info.selector,
        calldata_size=execution_context.calldata_size,
        calldata=execution_context.calldata,
        max_fee=0,
        chain_id=chain_id,
        additional_data_size=1,
        additional_data=&nonce,
    );

    return transaction_hash;
}
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/transaction_hash/transaction_hash.cairo (L295-315)
```text
func compute_meta_tx_v0_hash{pedersen_ptr: HashBuiltin*}(
    contract_address: felt,
    entry_point_selector: felt,
    calldata: felt*,
    calldata_size: felt,
    chain_id: felt,
) -> felt {
    let (tx_hash) = deprecated_get_transaction_hash{hash_ptr=pedersen_ptr}(
        tx_hash_prefix=INVOKE_HASH_PREFIX,
        version=0,
        contract_address=contract_address,
        entry_point_selector=entry_point_selector,
        calldata_size=calldata_size,
        calldata=calldata,
        max_fee=0,
        chain_id=chain_id,
        additional_data_size=0,
        additional_data=cast(0, felt*),
    );
    return tx_hash;
}
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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/execute_transaction_utils.cairo (L63-88)
```text
func check_and_increment_nonce{contract_state_changes: DictAccess*}(tx_info: TxInfo*) -> () {
    // Do not handle nonce for version 0.
    if (tx_info.version == 0) {
        return ();
    }

    tempvar state_entry: StateEntry*;
    %{ SetStateEntryToAccountContractAddress %}

    tempvar current_nonce = state_entry.nonce;
    with_attr error_message("Unexpected nonce.") {
        assert current_nonce = tx_info.nonce;
    }

    // Update contract_state_changes.
    tempvar new_state_entry = new StateEntry(
        class_hash=state_entry.class_hash,
        storage_ptr=state_entry.storage_ptr,
        nonce=current_nonce + 1,
    );
    dict_update{dict_ptr=contract_state_changes}(
        key=tx_info.account_contract_address,
        prev_value=cast(state_entry, felt),
        new_value=cast(new_state_entry, felt),
    );
    return ();
```
