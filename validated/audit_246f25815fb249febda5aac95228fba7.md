### Title
`compute_meta_tx_v0_hash` Omits Nonce, Enabling Replay of Signed Meta-Transactions — (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/transaction_hash/transaction_hash.cairo`)

---

### Summary

`compute_meta_tx_v0_hash` computes a Pedersen hash over `(prefix, version=0, contract_address, selector, calldata, max_fee=0, chain_id)` with `additional_data_size=0` — **no nonce is included**. The OS syscall handler `execute_meta_tx_v0` then hardcodes `nonce=0` in the synthetic `TxInfo` it passes to the called account contract and never calls `check_and_increment_nonce`. Because the hash is identical for every invocation with the same parameters, any valid signature over a meta-transaction is permanently reusable, enabling unlimited replay.

---

### Finding Description

`compute_meta_tx_v0_hash` in `transaction_hash.cairo` calls `deprecated_get_transaction_hash` with `additional_data_size=0` and `additional_data=cast(0, felt*)`: [1](#0-0) 

This means the hash domain is `H(prefix, 0, contract_address, selector, H(calldata), 0, chain_id)` — **no nonce, no block number, no timestamp**. Compare with every other transaction type (`compute_invoke_transaction_hash`, `compute_deploy_account_transaction_hash`, `compute_declare_transaction_hash`), all of which include `nonce` via `hash_tx_common_fields`: [2](#0-1) 

In `execute_meta_tx_v0` (syscall_impls.cairo), the OS constructs a synthetic `TxInfo` with `nonce=0` hardcoded and never calls `check_and_increment_nonce`: [3](#0-2) 

The account contract's `__execute__` entry point receives `tx_info.nonce = 0` and `tx_info.transaction_hash = meta_tx_hash` on every invocation. Standard StarkNet account contracts rely on the OS to enforce nonce uniqueness (via `check_and_increment_nonce`); they do not self-increment the nonce inside `__execute__`. Because the OS never increments the nonce for meta-transactions, the account's stored nonce is never advanced, and the same signature validates successfully on every replay. [4](#0-3) 

---

### Impact Explanation

**Critical — Direct loss of funds.**

A user (Alice) signs a meta-transaction authorising a token transfer of amount X to Bob. A relayer (or any contract) submits this via the `meta_tx_v0` syscall. Because the hash contains no nonce, the identical hash and signature remain valid indefinitely. Any party that observed the original call can replay it an arbitrary number of times within the same or future blocks, draining Alice's account balance without her consent.

---

### Likelihood Explanation

**High.**

1. The `meta_tx_v0` syscall is dispatched like any other syscall — any deployed contract can invoke it with attacker-supplied calldata and signature.
2. The replay requires only data already visible on-chain (calldata + signature from the original transaction).
3. Account contracts written to the standard StarkNet account interface delegate nonce enforcement to the OS; they have no reason to implement a separate replay-protection mechanism for meta-transactions.
4. No privileged access, leaked key, or network-level attack is required.

---

### Recommendation

**Short term:** Include a nonce in the meta-transaction hash. Either:
- Add a `nonce` parameter to `compute_meta_tx_v0_hash` and pass it as `additional_data`, mirroring how `compute_l1_handler_transaction_hash` passes `nonce` via `additional_data_size=1, additional_data=&nonce`.
- After executing the meta-transaction, call `check_and_increment_nonce` on the target account's stored nonce so the OS enforces uniqueness.

**Long term:** Do not assume properties of signed messages beyond what is explicitly committed in the hash preimage. Every signed action that can cause state changes must include a unique, monotonically increasing counter (nonce) in its hash domain to prevent substitution and replay.

---

### Proof of Concept

1. Alice signs `meta_tx_hash = H(INVOKE_PREFIX, 0, alice_addr, __execute__, H(transfer_calldata), 0, chain_id)` with her private key, producing `sig`.
2. Relayer submits a transaction containing a contract call that invokes `meta_tx_v0(contract_address=alice_addr, selector=__execute__, calldata=transfer_calldata, signature=sig)`.
3. OS computes the same `meta_tx_hash` (no nonce), passes `TxInfo(nonce=0, transaction_hash=meta_tx_hash, signature=sig)` to Alice's `__execute__`.
4. Alice's account validates `sig` against `meta_tx_hash` — valid. Transfer executes.
5. Attacker (or relayer) submits an identical `meta_tx_v0` call in the next block.
6. OS computes the same `meta_tx_hash` (same inputs, no nonce). Alice's stored nonce was never incremented by the OS. Alice's account again validates `sig` against the same hash — still valid. Transfer executes again.
7. Steps 5–6 repeat until Alice's balance is zero. [1](#0-0) [5](#0-4)

### Citations

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/transaction_hash/transaction_hash.cairo (L167-176)
```text
    poseidon_hash_update_single(item=common_fields.tx_hash_prefix);
    poseidon_hash_update_single(item=common_fields.version);
    poseidon_hash_update_single(item=common_fields.sender_address);
    poseidon_hash_update_single(item=fee_fields_hash);
    poseidon_hash_update_with_nested_hash(
        data_ptr=common_fields.paymaster_data, data_length=common_fields.paymaster_data_length
    );
    poseidon_hash_update_single(item=common_fields.chain_id);
    poseidon_hash_update_single(item=common_fields.nonce);
    poseidon_hash_update_single(item=data_availability_modes);
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
