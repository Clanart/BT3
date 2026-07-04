### Title
Missing Nonce in `meta_tx_v0` Hash Enables Signature Replay Attacks — (`File: crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

---

### Summary

The `execute_meta_tx_v0` syscall implementation constructs a transaction hash that does not include a nonce, and the OS explicitly skips nonce enforcement for version-0 transactions. This allows any observer of a valid `meta_tx_v0` call to replay the same signature in a different outer transaction, causing the target account contract to re-execute the authorized action without the user's intent.

---

### Finding Description

The `execute_meta_tx_v0` function in `syscall_impls.cairo` is a syscall that allows any contract to invoke another contract's `__execute__` entry point with a caller-supplied signature, creating a synthetic version-0 transaction context. The hash that the signature covers is computed by `compute_meta_tx_v0_hash`:

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
        additional_data_size=0,
        additional_data=cast(0, felt*),  // ← no nonce
    );
    return tx_hash;
}
``` [1](#0-0) 

The hash covers only: `(prefix, version=0, contract_address, selector, calldata, max_fee=0, chain_id)`. **No nonce is included.**

The synthetic `TxInfo` built in `execute_meta_tx_v0` hardcodes `nonce=0`:

```cairo
tempvar new_tx_info = new TxInfo(
    version=0,
    account_contract_address=contract_address,
    max_fee=0,
    signature_start=request.signature_start,
    signature_end=request.signature_end,
    transaction_hash=meta_tx_hash,
    chain_id=old_tx_info.chain_id,
    nonce=0,          // ← always zero
    ...
);
``` [2](#0-1) 

Furthermore, `check_and_increment_nonce` explicitly skips nonce enforcement for version-0 transactions:

```cairo
func check_and_increment_nonce{contract_state_changes: DictAccess*}(tx_info: TxInfo*) -> () {
    // Do not handle nonce for version 0.
    if (tx_info.version == 0) {
        return ();
    }
    ...
}
``` [3](#0-2) 

And `execute_meta_tx_v0` never calls `check_and_increment_nonce` at all — there is no nonce check or increment anywhere in the meta-transaction execution path. [4](#0-3) 

The result: for any fixed tuple `(contract_address, selector, calldata, chain_id)`, the meta-transaction hash is a constant. A signature valid for that hash is valid forever and can be submitted by anyone in any outer transaction.

---

### Impact Explanation

**Critical — Direct loss of funds.**

If a user signs a `meta_tx_v0` authorizing a token transfer (e.g., `calldata = [recipient, amount]`), the resulting signature covers a hash with no nonce. An attacker who observes this signature (e.g., from on-chain data or a mempool) can replay it in a new outer transaction — one with a fresh nonce of their own — by calling any contract that issues the same `meta_tx_v0` syscall with the same parameters. The target account contract's `__execute__` will receive the same hash and the same signature, verify it as valid, and execute the transfer again. This can be repeated until the victim's balance is drained.

---

### Likelihood Explanation

**High.** The `meta_tx_v0` syscall is a first-class protocol feature dispatched in `execute_syscalls`:

```cairo
assert selector = META_TX_V0_SELECTOR;
execute_meta_tx_v0(block_context=block_context, caller_execution_context=execution_context);
``` [5](#0-4) 

Any unprivileged transaction sender can:
1. Observe a valid `meta_tx_v0` call on-chain (signature is public).
2. Deploy a contract that issues the same `meta_tx_v0` syscall with the same `(contract_address, selector, calldata, signature)`.
3. Submit that outer transaction with a valid nonce of their own.

No privileged access, leaked key, or social engineering is required.

---

### Recommendation

Include a nonce in the `meta_tx_v0` hash. Concretely, pass the nonce as `additional_data` in `deprecated_get_transaction_hash`, analogous to how `compute_l1_handler_transaction_hash` includes the L1→L2 nonce:

```cairo
// compute_l1_handler_transaction_hash passes nonce as additional_data:
additional_data_size=1,
additional_data=&nonce,
``` [6](#0-5) 

For `meta_tx_v0`, the nonce should be sourced from the target account contract's on-chain nonce (read from `contract_state_changes`) and incremented after each successful meta-transaction execution, just as `check_and_increment_nonce` does for regular account transactions. [7](#0-6) 

---

### Proof of Concept

**Setup:**
- Victim account contract `V` at address `0xVVVV` holds 1000 STRK.
- `V` implements `__execute__` to verify a Stark signature over the meta-tx hash and, if valid, transfer tokens.

**Step 1 — Legitimate use:**
- A relayer submits outer tx (nonce=N) containing a call to contract `R`, which issues:
  ```
  meta_tx_v0(contract_address=0xVVVV, selector=__execute__, calldata=[transfer, recipient, 100], sig=S)
  ```
- OS computes `hash = H(invoke, 0, 0xVVVV, __execute__, calldata, 0, chain_id)` — no nonce.
- `V.__execute__` verifies `sig S` over `hash`, executes transfer of 100 STRK. ✓

**Step 2 — Replay attack:**
- Attacker observes `sig S` on-chain.
- Attacker submits outer tx (nonce=M, any valid nonce) calling their own contract `A`, which issues the identical `meta_tx_v0` call with the same parameters and `sig S`.
- OS computes the **same** `hash` (no nonce in hash).
- `V.__execute__` verifies `sig S` over the same `hash` → valid → executes another transfer of 100 STRK. ✓

**Step 3 — Drain:**
- Attacker repeats Step 2 until `V`'s balance is zero. Each outer transaction has a fresh attacker-controlled nonce; the meta-tx hash never changes.

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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/execute_transaction_utils.cairo (L63-89)
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
}
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/execute_syscalls.cairo (L343-344)
```text
    assert selector = META_TX_V0_SELECTOR;
    execute_meta_tx_v0(block_context=block_context, caller_execution_context=execution_context);
```
