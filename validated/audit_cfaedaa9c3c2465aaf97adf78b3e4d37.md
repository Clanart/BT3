### Title
`meta_tx_v0` Signature Hash Excludes Nonce, Enabling Unbounded Replay Attacks — (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/transaction_hash/transaction_hash.cairo` and `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

---

### Summary

The `meta_tx_v0` syscall computes a transaction hash that does not include a nonce or any other replay-prevention field. The OS also explicitly skips nonce checking for version-0 transactions. As a result, any valid `meta_tx_v0` signature can be replayed an unlimited number of times by any unprivileged attacker who has observed the signature, leading to direct loss of funds from the target account contract.

---

### Finding Description

The `compute_meta_tx_v0_hash` function in `transaction_hash.cairo` computes the hash that a user must sign to authorize a v0 meta transaction:

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
        additional_data_size=0,          // ← no nonce
        additional_data=cast(0, felt*),  // ← no nonce
    );
    return tx_hash;
}
``` [1](#0-0) 

The hash covers only: `(prefix, version=0, contract_address, selector, calldata, max_fee=0, chain_id)`. There is no nonce, no deadline, and no per-invocation uniqueness field.

In `execute_meta_tx_v0` in `syscall_impls.cairo`, the resulting `TxInfo` is constructed with `nonce=0` hardcoded:

```cairo
tempvar new_tx_info = new TxInfo(
    version=0,
    ...
    nonce=0,   // ← always zero
    ...
);
``` [2](#0-1) 

The OS nonce enforcement function `check_and_increment_nonce` explicitly skips all version-0 transactions:

```cairo
func check_and_increment_nonce{contract_state_changes: DictAccess*}(tx_info: TxInfo*) -> () {
    // Do not handle nonce for version 0.
    if (tx_info.version == 0) {
        return ();
    }
    ...
}
``` [3](#0-2) 

`execute_meta_tx_v0` does not call `check_and_increment_nonce` at all, and no used-hash tracking is performed anywhere in the OS for meta transactions. [4](#0-3) 

---

### Impact Explanation

**Critical — Direct loss of funds.**

The target contract's `__execute__` entry point receives a `TxInfo` whose `transaction_hash` is the meta_tx_v0 hash and whose `signature` is the user-provided signature. The target contract verifies the signature against this hash. Because the hash is deterministic over `(contract_address, selector, calldata, chain_id)` with no nonce, the same `(calldata, signature)` pair produces the same valid hash on every invocation.

An attacker who observes a single successful `meta_tx_v0` call (e.g., from mempool or on-chain history) can craft a new outer transaction that invokes the same relayer contract with the same `meta_tx_v0` parameters and signature. The OS will compute the identical hash, the target contract's signature check will pass, and `__execute__` will run again — transferring funds a second (or Nth) time.

---

### Likelihood Explanation

**High.** The `meta_tx_v0` syscall is a public protocol feature callable from any contract. Any account contract that relies on the OS-level hash for signature verification (the standard pattern, since `get_execution_info()` exposes `tx_info.transaction_hash`) is vulnerable. Signatures are observable on-chain after the first use. No special privilege or key access is required to mount the replay.

---

### Recommendation

1. **Include a nonce in `compute_meta_tx_v0_hash`**: Add a `nonce` parameter and pass it as `additional_data` (analogous to how `compute_l1_handler_transaction_hash` passes its nonce via `additional_data_size=1, additional_data=&nonce`). [5](#0-4) 

2. **Track and enforce nonce for meta_tx_v0 at the OS level**: In `execute_meta_tx_v0`, read the target contract's current nonce, require it matches the signed nonce, and increment it — or maintain a separate used-hash mapping.

3. **Consider adding a deadline field** to the hash to prevent indefinitely valid signatures, consistent with the fix applied to the analogous Scroll `EnforcedTxGateway` bug.

---

### Proof of Concept

1. User signs `H = hash(prefix, 0, contract_A, __execute__, calldata_T, 0, chain_id)` — a meta_tx_v0 authorizing a token transfer of amount `T` from `contract_A`.
2. Relayer submits outer transaction (nonce=5) calling `relayer_contract` which invokes `meta_tx_v0(contract_A, __execute__, calldata_T, sig)`. Transfer executes. Outer tx nonce increments to 6.
3. Attacker observes the signature `sig` on-chain.
4. Attacker submits a new outer transaction (any nonce, any sender) calling `relayer_contract` (or any contract that calls `meta_tx_v0`) with the same `(contract_A, __execute__, calldata_T, sig)`.
5. OS computes the same `meta_tx_hash` (no nonce in hash), constructs `TxInfo` with `nonce=0`, calls `contract_A.__execute__` with the same signature.
6. `contract_A.__execute__` verifies `sig` against `meta_tx_hash` — passes — and executes the transfer again.
7. Repeat indefinitely until `contract_A` is drained. [6](#0-5) [7](#0-6)

### Citations

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/transaction_hash/transaction_hash.cairo (L220-237)
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
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/transaction_hash/transaction_hash.cairo (L294-315)
```text
// Computes the hash of a v0 meta transaction. See the `meta_tx_v0` syscall.
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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/execute_transaction_utils.cairo (L63-67)
```text
func check_and_increment_nonce{contract_state_changes: DictAccess*}(tx_info: TxInfo*) -> () {
    // Do not handle nonce for version 0.
    if (tx_info.version == 0) {
        return ();
    }
```
