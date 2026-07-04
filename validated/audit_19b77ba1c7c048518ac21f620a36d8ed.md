### Title
Missing Nonce in `meta_tx_v0` Hash Enables Signature Replay — (`File: crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo` and `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/transaction_hash/transaction_hash.cairo`)

---

### Summary

The `execute_meta_tx_v0` syscall creates a version-0 execution context whose transaction hash is computed **without any nonce**. The OS also explicitly skips nonce checking for version-0 transactions. As a result, any signature produced for a `meta_tx_v0` call can be replayed an unlimited number of times by any unprivileged attacker who observed the signature on-chain, leading to direct loss of funds.

---

### Finding Description

`execute_meta_tx_v0` in `syscall_impls.cairo` is a syscall that allows a contract to call another contract (specifically its `__execute__` entry point) with a caller-supplied signature and a version-0 transaction context. The OS computes the transaction hash via `compute_meta_tx_v0_hash`:

```cairo
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
        additional_data_size=0,       // <-- NO NONCE
        additional_data=cast(0, felt*),
    );
    return tx_hash;
}
``` [1](#0-0) 

The hash is a pure function of `(contract_address, selector, calldata, chain_id)` — no nonce, no expiry, no block number. The resulting `new_tx_info` is constructed with `nonce=0`:

```cairo
tempvar new_tx_info = new TxInfo(
    version=0,
    ...
    nonce=0,
    ...
);
``` [2](#0-1) 

The OS-level `check_and_increment_nonce` function explicitly skips all nonce enforcement for version-0 transactions:

```cairo
func check_and_increment_nonce{contract_state_changes: DictAccess*}(tx_info: TxInfo*) -> () {
    // Do not handle nonce for version 0.
    if (tx_info.version == 0) {
        return ();
    }
    ...
}
``` [3](#0-2) 

Similarly, `run_validate` skips `__validate__` for version-0 contexts: [4](#0-3) 

The syscall is dispatched from `execute_syscalls` and is reachable by any contract: [5](#0-4) 

The full `execute_meta_tx_v0` function performs no nonce read, no nonce write, no consumed-message check, and no expiry check before executing the target: [6](#0-5) 

---

### Impact Explanation

An attacker who observes a valid `meta_tx_v0` signature on-chain (e.g., from a prior transaction) can replay it indefinitely. Because the OS computes the same hash for the same `(contract_address, selector, calldata, chain_id)` tuple every time, and because no nonce is tracked or enforced at the OS level, the signature remains valid forever. If the replayed meta-transaction transfers tokens or performs any value-bearing action, the attacker can drain the victim's account — **direct loss of funds** (Critical).

---

### Likelihood Explanation

The attack requires only:
1. Observing a `meta_tx_v0` signature from a prior on-chain transaction (signatures are public).
2. Deploying a contract that calls the `meta_tx_v0` syscall with the captured signature and the same calldata.
3. Submitting a transaction that invokes that contract.

No privileged access, leaked keys, or trusted-role compromise is required. Any unprivileged user can execute this attack.

---

### Recommendation

Include a nonce in the `meta_tx_v0` hash computation. The nonce should be tracked per `(contract_address)` in contract state and incremented on each successful `meta_tx_v0` execution, analogous to how `check_and_increment_nonce` works for version-1/3 transactions. Specifically:

1. Add a `nonce` parameter to `compute_meta_tx_v0_hash` and pass it as `additional_data`.
2. In `execute_meta_tx_v0`, read the current nonce from `contract_state_changes` for the target `contract_address`, include it in the hash, and increment it after successful execution.

---

### Proof of Concept

1. Alice signs a `meta_tx_v0` message authorizing a transfer of 100 STRK from her account to Bob: `sign(hash(INVOKE_PREFIX, 0, alice_addr, __execute__, calldata_transfer_100_STRK, 0, chain_id))`.
2. A transaction is submitted that calls `meta_tx_v0` with Alice's signature. The transfer executes successfully.
3. The attacker observes Alice's signature from the transaction data.
4. The attacker deploys a contract `Replayer` whose `__execute__` calls `meta_tx_v0(contract_address=alice_addr, selector=__execute__, calldata=calldata_transfer_100_STRK, signature=alice_sig)`.
5. The attacker submits a transaction invoking `Replayer.__execute__`.
6. The OS computes `hash(INVOKE_PREFIX, 0, alice_addr, __execute__, calldata_transfer_100_STRK, 0, chain_id)` — identical to step 1 — and Alice's signature verifies. Another 100 STRK is transferred.
7. Steps 5–6 repeat until Alice's account is drained.

### Citations

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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/execute_transaction_utils.cairo (L63-67)
```text
func check_and_increment_nonce{contract_state_changes: DictAccess*}(tx_info: TxInfo*) -> () {
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
