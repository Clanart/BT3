### Title
`meta_tx_v0` Hash Omits Nonce, Enabling Signature Replay Across Executions - (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/transaction_hash/transaction_hash.cairo`)

---

### Summary

The `compute_meta_tx_v0_hash` function computes a hash over `(prefix, version=0, contract_address, selector, calldata, max_fee=0, chain_id)` with `additional_data_size=0` — no nonce is included. The OS then constructs a `TxInfo` for the sub-execution with `nonce=0` hardcoded. Because neither the hash nor the nonce field carries any per-invocation uniqueness, a valid `meta_tx_v0` signature can be replayed an unlimited number of times by any caller who can invoke the syscall.

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
        additional_data_size=0,          // ← no nonce
        additional_data=cast(0, felt*),
    );
    return tx_hash;
}
``` [1](#0-0) 

Contrast this with `compute_l1_handler_transaction_hash`, which correctly passes `additional_data_size=1, additional_data=&nonce`: [2](#0-1) 

In `execute_meta_tx_v0`, the resulting `TxInfo` is assembled with `nonce=0` hardcoded:

```cairo
tempvar new_tx_info = new TxInfo(
    version=0,
    ...
    transaction_hash=meta_tx_hash,
    chain_id=old_tx_info.chain_id,
    nonce=0,          // ← always zero, never incremented
    ...
);
``` [3](#0-2) 

The OS never calls `check_and_increment_nonce` for `meta_tx_v0` (unlike every account transaction). The target contract's `__execute__` entry point receives a `TxInfo` whose `transaction_hash` is fully determined by `(contract_address, selector, calldata, chain_id)` — identical across every replay — and whose `nonce` is always `0`, giving the account contract no OS-enforced field it can use to distinguish invocations. [4](#0-3) 

---

### Impact Explanation

Any contract that accepts a `meta_tx_v0` signature (e.g., a gasless-relay pattern where a user pre-signs a fund transfer) is exposed to unlimited replay. Because the OS computes the same `meta_tx_hash` for the same `(contract_address, selector, calldata)` tuple on every invocation, a single valid off-chain signature authorises every future invocation with those parameters. An attacker who obtains the signature — from a broadcast, a mempool observation, or a prior on-chain execution — can drain the target account by replaying the signed call repeatedly within the same block or across blocks.

This maps directly to **Critical: Direct loss of funds**.

---

### Likelihood Explanation

The `meta_tx_v0` syscall is a public protocol primitive reachable by any deployed contract. A relayer-style contract (the intended use case) necessarily broadcasts the signature on-chain, making it observable. Once observed, replay requires only constructing an outer invoke transaction that calls the `meta_tx_v0` syscall with the same arguments — no privileged access, no key compromise, no operator cooperation. The only precondition is that the target account contract does not implement bespoke storage-based replay protection, which the OS design does not require or enforce.

---

### Recommendation

Include a per-invocation unique field in the `meta_tx_v0` hash. The most natural choice is a caller-supplied nonce committed to by the signer:

```cairo
func compute_meta_tx_v0_hash{pedersen_ptr: HashBuiltin*}(
    contract_address: felt,
    entry_point_selector: felt,
    calldata: felt*,
    calldata_size: felt,
    chain_id: felt,
    nonce: felt,          // ← add nonce
) -> felt {
    let (tx_hash) = deprecated_get_transaction_hash{hash_ptr=pedersen_ptr}(
        ...
        additional_data_size=1,
        additional_data=&nonce,
    );
    return tx_hash;
}
```

The `nonce` should be passed through from `MetaTxV0Request`, stored and incremented in the target account's state (analogous to how `check_and_increment_nonce` works for regular account transactions), and reflected in the `TxInfo.nonce` field so the account contract can enforce uniqueness.

---

### Proof of Concept

1. Alice signs a `meta_tx_v0` payload authorising a 100-token transfer: `(alice_addr, __execute__, [transfer_calldata])`. The resulting hash is `H = Pedersen(prefix, 0, alice_addr, __execute__, hash(calldata), 0, chain_id)`.
2. A relayer submits an outer invoke transaction containing a `meta_tx_v0` syscall with Alice's signature. The transfer executes; Alice loses 100 tokens.
3. An attacker observes the signature on-chain. They submit a second outer invoke transaction with the identical `meta_tx_v0` syscall arguments and Alice's signature.
4. `compute_meta_tx_v0_hash` produces the same `H`. The target contract's `__execute__` receives `transaction_hash=H, nonce=0` — identical to step 2. Signature verification passes. The transfer executes again.
5. Steps 3–4 repeat until Alice's balance is zero. The OS never rejects the replay because it performs no hash-uniqueness or nonce check for `meta_tx_v0`. [5](#0-4) [1](#0-0)

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
