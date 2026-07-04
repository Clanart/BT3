### Title
`compute_meta_tx_v0_hash` Omits Nonce from Hash Preimage, Enabling Cross-Transaction Signature Replay — (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/transaction_hash/transaction_hash.cairo`)

---

### Summary

`compute_meta_tx_v0_hash` constructs the signed hash for a `meta_tx_v0` syscall without committing to any nonce or session identifier. Combined with the OS explicitly skipping both nonce enforcement and `__validate__` for version-0 transactions, a valid `meta_tx_v0` signature is permanently replayable by any unprivileged caller across any future transaction on the same chain.

---

### Finding Description

`compute_meta_tx_v0_hash` delegates to `deprecated_get_transaction_hash` with `additional_data_size=0` and `additional_data=cast(0, felt*)`:

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
        additional_data_size=0,           // ← no nonce
        additional_data=cast(0, felt*),   // ← no nonce
    );
    return tx_hash;
}
``` [1](#0-0) 

Contrast this with `compute_l1_handler_transaction_hash`, which correctly passes `additional_data_size=1, additional_data=&nonce` to bind the hash to a specific nonce: [2](#0-1) 

In `execute_meta_tx_v0`, the resulting `TxInfo` is constructed with `nonce=0` hardcoded: [3](#0-2) 

The OS then enforces two explicit bypasses for version-0 transactions:

**1. Nonce check is skipped:**
```cairo
// Do not handle nonce for version 0.
if (tx_info.version == 0) {
    return ();
}
``` [4](#0-3) 

**2. `__validate__` is skipped:**
```cairo
// Do not run "__validate__" for version 0.
if (tx_execution_info.tx_info.version == 0) {
    return ();
}
``` [5](#0-4) 

The hash preimage for a `meta_tx_v0` is therefore bound only to:
`(prefix='invoke', version=0, contract_address, selector='__execute__', calldata, max_fee=0, chain_id)`

It does **not** commit to any nonce, block number, or session identifier. Because the OS skips both nonce enforcement and `__validate__` for version-0 transactions, the same `(contract_address, calldata, signature)` triple produces an identical hash in every block, forever.

---

### Impact Explanation

**Direct loss of funds (Critical).**

An attacker who observes a valid `meta_tx_v0` call on-chain (e.g., a signed transfer of tokens) can extract the `(contract_address, calldata, signature)` parameters and replay them in any future outer transaction by calling the `meta_tx_v0` syscall from an attacker-controlled contract. The OS will compute the identical `meta_tx_hash`, set `tx_info.transaction_hash = meta_tx_hash` and `tx_info.signature = [replayed signature]`, and invoke `__execute__` on the victim account. Because `__validate__` is skipped, the account's signature-verification entry point is never called. Account contracts that rely on the OS-level nonce for replay protection (the standard assumption) have no defense. The attacker can drain the victim's account by replaying a single signed transfer indefinitely.

---

### Likelihood Explanation

**High.** The `meta_tx_v0` syscall is callable by any unprivileged contract. The only prerequisite is that a valid `meta_tx_v0` signature has been broadcast on-chain at least once — which is the normal operating condition for any relayer-based flow using this syscall. No privileged access, leaked key, or off-chain coordination is required beyond observing the mempool or chain history. Standard account contracts (which implement signature verification in `__validate__`, not `__execute__`) are fully exposed because `__validate__` is unconditionally skipped for version-0 transactions.

---

### Recommendation

Include a nonce in the `compute_meta_tx_v0_hash` preimage, analogous to how `compute_l1_handler_transaction_hash` binds its hash to a nonce via `additional_data`:

```cairo
func compute_meta_tx_v0_hash{pedersen_ptr: HashBuiltin*}(
    contract_address: felt,
    entry_point_selector: felt,
    calldata: felt*,
    calldata_size: felt,
    chain_id: felt,
    nonce: felt,          // ← add nonce parameter
) -> felt {
    let (tx_hash) = deprecated_get_transaction_hash{hash_ptr=pedersen_ptr}(
        ...
        additional_data_size=1,
        additional_data=&nonce,   // ← bind hash to nonce
    );
    return tx_hash;
}
```

Additionally, the OS should enforce nonce checking and incrementing for `meta_tx_v0` calls (removing the version-0 bypass in `check_and_increment_nonce`), or introduce a dedicated per-account meta-tx nonce tracked separately in state.

---

### Proof of Concept

1. User's account contract at address `A` signs a `meta_tx_v0` to transfer 100 tokens. The signature `σ` covers `H('invoke', 0, A, '__execute__', calldata_transfer, 0, chain_id)`.
2. A relayer submits an outer transaction that calls `meta_tx_v0(contract_address=A, selector='__execute__', calldata=calldata_transfer, signature=σ)`. The transfer executes.
3. Attacker deploys contract `Evil` with a function that calls `meta_tx_v0(contract_address=A, selector='__execute__', calldata=calldata_transfer, signature=σ)` using the same parameters observed on-chain.
4. Attacker submits a transaction invoking `Evil`. The OS computes the identical `meta_tx_hash` (no nonce in preimage), sets `tx_info.transaction_hash = meta_tx_hash`, skips `__validate__`, and calls `A.__execute__` with the replayed signature.
5. `A.__execute__` verifies `σ` against `tx_info.transaction_hash` — it matches. The transfer executes again.
6. Attacker repeats step 4 until `A`'s balance is zero. [6](#0-5) [7](#0-6)

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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/execute_transaction_utils.cairo (L127-130)
```text
    // Do not run "__validate__" for version 0.
    if (tx_execution_info.tx_info.version == 0) {
        return ();
    }
```
