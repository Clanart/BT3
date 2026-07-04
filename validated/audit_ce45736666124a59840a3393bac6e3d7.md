### Title
`meta_tx_v0` Hash Omits Caller Address and Nonce, Enabling Cross-Context Signature Replay — (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/transaction_hash/transaction_hash.cairo`)

---

### Summary

`compute_meta_tx_v0_hash` produces a hash that binds only to `(contract_address, selector, calldata, chain_id)`. It omits both the address of the contract invoking the `meta_tx_v0` syscall and any nonce. Because `execute_meta_tx_v0` hard-codes `nonce=0` and never calls `check_and_increment_nonce`, the same signature is cryptographically valid for every future invocation with identical parameters, by any caller. An unprivileged attacker who observes a valid `meta_tx_v0` signature can replay it from their own contract, causing the victim account's `__execute__` entry point to run under attacker-controlled conditions.

---

### Finding Description

`compute_meta_tx_v0_hash` delegates to `deprecated_get_transaction_hash` with `additional_data_size=0`: [1](#0-0) 

The resulting hash covers only:

```
H(INVOKE_HASH_PREFIX, version=0, contract_address, EXECUTE_ENTRY_POINT_SELECTOR,
  H(calldata), max_fee=0, chain_id)
```

No nonce and no caller address appear anywhere in this preimage.

In `execute_meta_tx_v0`, the `new_tx_info` is assembled with `nonce=0` hard-coded: [2](#0-1) 

Unlike every account transaction (`execute_invoke_function_transaction`, `execute_deploy_account_transaction`, `execute_declare_transaction`), `execute_meta_tx_v0` never calls `check_and_increment_nonce`: [3](#0-2) 

The caller's identity is available in `caller_execution_context.execution_info.contract_address` but is never fed into the hash: [4](#0-3) 

The analog to the external report is exact: the external hooks bound execution to a router address but not to the specific user; here the hash binds to the target account address but not to the invoking contract or any replay counter.

---

### Impact Explanation

The target account's `__execute__` entry point is invoked with a `TxInfo` whose `transaction_hash` is the nonce-less, caller-less `meta_tx_hash`. The account's signature-verification logic checks the user's signature against this hash. Because the hash is fully deterministic for a given `(contract_address, calldata, chain_id)` triple, a signature produced once is valid forever and from any calling contract.

If the `__execute__` calldata encodes a token transfer or any other value-moving operation, an attacker who replays the signature causes that operation to execute again — **direct loss of funds** from the victim account. This meets the Critical impact threshold.

---

### Likelihood Explanation

The `meta_tx_v0` syscall is reachable by any deployed contract; no privileged role is required. An attacker needs only to observe a valid `(calldata, signature)` pair — available from a pending transaction in the mempool or from on-chain history — and submit their own transaction that calls `meta_tx_v0` with those values. The attack requires no leaked keys, no operator cooperation, and no network-level capability.

---

### Recommendation

1. **Include the caller address in the hash preimage.** Pass `caller_execution_context.execution_info.contract_address` into `compute_meta_tx_v0_hash` and add it to the `deprecated_get_transaction_hash` call (e.g., as part of `additional_data`).

2. **Include a nonce in the hash and enforce it.** Either reuse the target account's on-chain nonce (reading and incrementing it via `check_and_increment_nonce`) or introduce a dedicated per-account meta-tx nonce. Set `additional_data_size=1` and pass the nonce as `additional_data` so the hash commits to it.

3. **Remove the `nonce=0` hard-code** in the `new_tx_info` construction inside `execute_meta_tx_v0` and replace it with the verified nonce value.

---

### Proof of Concept

1. Alice's account holds tokens. She authorises a `meta_tx_v0` call: calldata encodes `transfer(bob, 1000)`, and she signs the resulting `meta_tx_hash = H(PREFIX, 0, alice_account, EXECUTE_SEL, H(calldata), 0, chain_id)`.

2. Alice's outer transaction is broadcast. An MEV bot reads `(calldata, signature)` from the mempool.

3. The bot deploys a minimal contract whose only action is to issue the `meta_tx_v0` syscall with Alice's `contract_address`, `calldata`, and `signature`.

4. The OS executes `execute_meta_tx_v0`:
   - Recomputes `meta_tx_hash` — identical to Alice's, because caller address and nonce are absent from the hash.
   - Constructs `new_tx_info` with `nonce=0` and `transaction_hash=meta_tx_hash`.
   - Calls Alice's `__execute__` entry point.

5. Alice's account validates the signature against `meta_tx_hash` — it passes. The transfer executes. Alice loses 1000 tokens.

6. The bot can repeat step 3–5 in subsequent blocks until Alice's balance is drained, because no nonce is consumed and the hash never changes. [1](#0-0) [5](#0-4)

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
