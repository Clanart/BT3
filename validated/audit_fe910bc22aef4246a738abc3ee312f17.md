### Title
Missing Nonce in `compute_meta_tx_v0_hash` Enables Signature Replay of Meta Transactions — (`File: crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/transaction_hash/transaction_hash.cairo`)

---

### Summary

The `compute_meta_tx_v0_hash` function omits a nonce from the signed message, and `check_and_increment_nonce` explicitly skips nonce enforcement for version-0 transactions. Any attacker who observes a valid `meta_tx_v0` syscall can replay the identical `(contract_address, selector, calldata, signature)` tuple in a new outer transaction, causing the inner `__execute__` to run again with the same authorization — leading to direct, repeated loss of funds from the victim account.

---

### Finding Description

**Root cause — no nonce in the meta-tx hash:**

`compute_meta_tx_v0_hash` delegates to `deprecated_get_transaction_hash` with `additional_data_size=0` and `additional_data=cast(0, felt*)`:

```cairo
// transaction_hash.cairo lines 295-315
func compute_meta_tx_v0_hash{pedersen_ptr: HashBuiltin*}(
    contract_address, entry_point_selector, calldata, calldata_size, chain_id
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
        additional_data_size=0,           // ← no nonce slot
        additional_data=cast(0, felt*),   // ← no nonce value
    );
    return tx_hash;
}
```

The resulting hash is a pure function of `(prefix, version=0, contract_address, selector, calldata, chain_id)`. It is identical for every replay. [1](#0-0) 

**Root cause — nonce check skipped for version 0:**

`check_and_increment_nonce` contains an explicit early-return for version-0 transactions, so even if a nonce were tracked, it would never be enforced:

```cairo
// execute_transaction_utils.cairo lines 63-67
func check_and_increment_nonce{contract_state_changes: DictAccess*}(tx_info: TxInfo*) {
    // Do not handle nonce for version 0.
    if (tx_info.version == 0) {
        return ();
    }
    ...
}
``` [2](#0-1) 

**Root cause — meta-tx execution sets nonce=0 unconditionally:**

Inside `execute_meta_tx_v0`, the synthesized `TxInfo` always carries `nonce=0`, confirming no per-invocation counter exists:

```cairo
// syscall_impls.cairo lines 343-363
tempvar new_tx_info = new TxInfo(
    version=0,
    ...
    nonce=0,   // ← always zero, never incremented
    ...
);
``` [3](#0-2) 

The hash is then computed and placed in `new_tx_info.transaction_hash` — the value the account contract's `__validate__` will sign-check against: [4](#0-3) 

---

### Impact Explanation

`execute_meta_tx_v0` is designed to let a relayer submit a gasless transaction on behalf of a user: the user signs a hash covering `(contract_address, __execute__, calldata, chain_id)` and the relayer includes that signature in the syscall. Because the hash never changes (no nonce), any party who sees the on-chain calldata and signature can construct a new outer v3 transaction that re-invokes the same syscall with the same arguments. The inner `__execute__` runs again under the victim's identity, executing whatever the original calldata encoded — typically an ERC-20 transfer or other fund movement. This constitutes **direct, repeated loss of funds** for the victim, matching the Critical impact tier.

---

### Likelihood Explanation

The attack requires only:
1. Observing a confirmed block containing a `meta_tx_v0` syscall (public on-chain data).
2. Submitting a new outer transaction that calls the same relayer contract (or any contract that issues the same syscall) with the captured parameters.

No privileged access, leaked key, or Sybil attack is needed. Any unprivileged network participant can execute this.

---

### Recommendation

Include a per-account nonce in the meta-tx hash, analogous to how `deprecated_get_transaction_hash` already handles it for L1-handler transactions (which pass `additional_data_size=1, additional_data=&nonce`):

```cairo
func compute_meta_tx_v0_hash{pedersen_ptr: HashBuiltin*}(
    contract_address, entry_point_selector, calldata, calldata_size, chain_id, nonce  // ← add nonce
) -> felt {
    let (tx_hash) = deprecated_get_transaction_hash{hash_ptr=pedersen_ptr}(
        ...
        additional_data_size=1,
        additional_data=&nonce,   // ← bind hash to a single-use nonce
    );
    return tx_hash;
}
```

Additionally, `execute_meta_tx_v0` must read and increment the target account's nonce in `contract_state_changes` after each successful meta-tx execution, and `check_and_increment_nonce` must not skip version-0 meta transactions. [5](#0-4) 

---

### Proof of Concept

1. **Victim setup:** Account contract at address `A` approves a relayer contract `R` to submit meta transactions on its behalf. The victim signs a meta-tx hash `H = H(INVOKE_HASH_PREFIX, 0, A, __execute__, calldata_transfer_10_tokens, 0, chain_id)` and sends the signature `σ` to the relayer off-chain.

2. **Relayer submits:** Relayer `R` issues an outer v3 transaction that calls `execute_meta_tx_v0` with `(contract_address=A, selector=__execute__, calldata=calldata_transfer_10_tokens, signature=σ)`. The OS computes the same hash `H`, the account's `__validate__` verifies `σ` against `H`, and `__execute__` transfers 10 tokens. The outer transaction is confirmed on-chain.

3. **Attacker replays:** An attacker reads `(A, __execute__, calldata_transfer_10_tokens, σ)` from the confirmed block. The attacker submits a new outer v3 transaction (from their own account, paying their own gas) that calls `execute_meta_tx_v0` with the identical parameters. The OS recomputes the same `H` (no nonce changes it), `__validate__` accepts `σ` again, and `__execute__` transfers another 10 tokens from `A`.

4. **Drain:** The attacker repeats step 3 until `A`'s balance or token allowance is exhausted. Each replay costs only the attacker's gas; the victim loses funds with no recourse. [6](#0-5) [1](#0-0) [2](#0-1)

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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/execute_transaction_utils.cairo (L63-67)
```text
func check_and_increment_nonce{contract_state_changes: DictAccess*}(tx_info: TxInfo*) -> () {
    // Do not handle nonce for version 0.
    if (tx_info.version == 0) {
        return ();
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
