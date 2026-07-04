### Title
Missing Nonce in `compute_meta_tx_v0_hash` Enables Signature Replay via `meta_tx_v0` Syscall — (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/transaction_hash/transaction_hash.cairo` and `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

---

### Summary

The `compute_meta_tx_v0_hash` function computes a transaction hash that deliberately omits any nonce (`additional_data_size=0`). Combined with the OS hardcoding `nonce=0` in the resulting `TxInfo` and `check_and_increment_nonce` unconditionally skipping version-0 transactions, any signature produced for a `meta_tx_v0` call is permanently reusable. An unprivileged attacker who observes a valid `meta_tx_v0` call on-chain can replay it from their own contract an unlimited number of times, causing the target account contract's `__execute__` to run again with the same calldata and the same valid signature — leading to direct loss of funds.

---

### Finding Description

`execute_meta_tx_v0` in `syscall_impls.cairo` is a syscall available to any executing contract. It:

1. Reads `contract_address`, `selector`, `calldata`, and `signature` from the syscall request.
2. Computes a hash via `compute_meta_tx_v0_hash`.
3. Constructs a synthetic `TxInfo` with `version=0` and `nonce=0`.
4. Calls the target contract's `__execute__` entry point with the provided signature and the computed hash as `transaction_hash`. [1](#0-0) 

`compute_meta_tx_v0_hash` calls `deprecated_get_transaction_hash` with `additional_data_size=0` and `additional_data=cast(0, felt*)`:

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
        additional_data_size=0,   // <-- NO NONCE COMMITTED
        additional_data=cast(0, felt*),
    );
    return tx_hash;
}
``` [2](#0-1) 

Compare this to `compute_l1_handler_transaction_hash`, which correctly includes the nonce in `additional_data`: [3](#0-2) 

Back in `execute_meta_tx_v0`, the synthetic `TxInfo` is built with `nonce=0` hardcoded: [4](#0-3) 

The OS nonce-increment guard explicitly skips version-0 transactions:

```cairo
func check_and_increment_nonce{contract_state_changes: DictAccess*}(tx_info: TxInfo*) -> () {
    // Do not handle nonce for version 0.
    if (tx_info.version == 0) {
        return ();
    }
    ...
}
``` [5](#0-4) 

The result is that the hash committed to by the user's signature is a pure function of `(INVOKE_PREFIX, 0, contract_address, selector, calldata, 0, chain_id)`. It contains no per-use uniqueness. Any party who supplies the same tuple with the same signature will produce the same hash, pass the target contract's signature check, and successfully execute `__execute__`.

---

### Impact Explanation

**Critical — Direct loss of funds.**

The `meta_tx_v0` syscall is designed for gasless/relayed execution: a user signs a meta-transaction hash and a relayer submits it. Because the hash has no nonce, the signed authorization is valid forever and for any caller. An attacker who observes a `meta_tx_v0` call that transfers tokens (e.g., an ERC-20 transfer encoded in calldata) can replay it from their own contract in every subsequent block, draining the target account's balance until it is empty.

---

### Likelihood Explanation

**High.** The attack requires only:
- Observing a confirmed `meta_tx_v0` call on-chain (public information).
- Deploying a contract that issues the same `meta_tx_v0` syscall with the same `(contract_address, selector, calldata, signature)`.

No privileged access, leaked keys, or trusted-role compromise is needed. The syscall is available to any executing contract.

---

### Recommendation

Include a per-use nonce in the meta-transaction hash, mirroring how `compute_l1_handler_transaction_hash` passes `additional_data_size=1, additional_data=&nonce`. The nonce must be tracked in the target contract's storage (or in OS state) and rejected if already consumed. Concretely:

1. Add a `nonce` parameter to `compute_meta_tx_v0_hash` and pass it as `additional_data`.
2. Track used nonces for each `(caller, contract_address)` pair in contract storage or OS state.
3. Reject any `meta_tx_v0` request whose nonce has already been consumed.

---

### Proof of Concept

**Setup**: Deploy a victim account contract `V` that holds funds. A legitimate relayer submits a transaction containing a `meta_tx_v0` syscall with:
- `contract_address = V`
- `selector = __execute__`
- `calldata = [transfer(attacker, 1_ETH)]`
- `signature = sig_V` (signed by V's private key over the nonce-free hash)

**Attack**:
1. Attacker observes the confirmed transaction and extracts `(V, __execute__, calldata, sig_V)`.
2. Attacker deploys contract `A` with a single function that calls `meta_tx_v0(contract_address=V, selector=__execute__, calldata=calldata, signature=sig_V)`.
3. Attacker submits a transaction invoking `A`.
4. The OS computes `meta_tx_hash = hash(INVOKE_PREFIX, 0, V, __execute__, calldata, 0, chain_id)` — identical to the original.
5. `V.__execute__` receives `tx_info.transaction_hash = meta_tx_hash` and `tx_info.signature = sig_V`. Signature verification passes.
6. The transfer executes again. Attacker repeats until `V` is drained. [6](#0-5) [7](#0-6)

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
