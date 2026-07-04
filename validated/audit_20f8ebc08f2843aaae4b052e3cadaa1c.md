### Title
Meta-Transaction v0 Missing Nonce Tracking Enables Unbounded Replay — (`execution/syscall_impls.cairo`)

---

### Summary

`execute_meta_tx_v0` constructs a synthetic `TxInfo` with a hardcoded `nonce=0` and `version=0`. Because `check_and_increment_nonce` unconditionally skips version-0 transactions, the OS never reads, validates, or increments the target contract's nonce for any meta-transaction. A single valid off-chain signature can therefore be replayed an unlimited number of times across distinct outer transactions, each of which passes all OS-level checks.

---

### Finding Description

`execute_meta_tx_v0` in `syscall_impls.cairo` synthesises a fresh `TxInfo` for the inner call:

```cairo
tempvar new_tx_info = new TxInfo(
    version=0,
    ...
    nonce=0,          // hardcoded — never read from state
    ...
);
``` [1](#0-0) 

The meta-transaction hash that the target account must sign is computed without a nonce:

```cairo
let meta_tx_hash = compute_meta_tx_v0_hash(
    contract_address=contract_address,
    entry_point_selector=selector,
    calldata=calldata_start,
    calldata_size=calldata_size,
    chain_id=old_tx_info.chain_id,   // no nonce field
);
``` [2](#0-1) 

`check_and_increment_nonce`, the sole OS mechanism for preventing transaction replay, explicitly returns early for version-0 transactions:

```cairo
func check_and_increment_nonce{contract_state_changes: DictAccess*}(tx_info: TxInfo*) -> () {
    // Do not handle nonce for version 0.
    if (tx_info.version == 0) {
        return ();
    }
``` [3](#0-2) 

`execute_meta_tx_v0` never calls `check_and_increment_nonce` for the inner call at all. The outer (wrapping) invoke transaction does have its nonce checked and incremented, but that only prevents the *outer* transaction from being replayed — it does not prevent a different outer transaction from embedding the identical meta-tx syscall. [4](#0-3) 

The OS enforces that the selector must be `EXECUTE_ENTRY_POINT_SELECTOR`, so the target is always an account contract's `__execute__` entry point. Because the OS presents `version=0` to that entry point, account contracts that gate nonce checks on `version >= 1` will not perform any replay protection either.

---

### Impact Explanation

**Critical — Direct loss of funds.**

Once a user (Alice) produces a valid signature over a meta-tx hash (e.g., authorising a token transfer), any outer-transaction sender (Bob) can embed that same `meta_tx_v0` syscall in an unlimited number of distinct outer transactions (each with a fresh, valid nonce). Every replay executes Alice's `__execute__` entry point with the original calldata, draining her account or executing any other state-changing operation she authorised exactly once.

---

### Likelihood Explanation

**High.** The attacker only needs to observe a single valid meta-tx signature on-chain (or off-chain). No privileged access, key compromise, or network-level attack is required. The outer transaction sender is an unprivileged protocol participant. The replay can be performed in the very next block after the original meta-tx appears.

---

### Recommendation

1. Include a nonce in `compute_meta_tx_v0_hash` so that each meta-tx hash commits to a unique sequence number.
2. In `execute_meta_tx_v0`, read the target contract's current nonce from `contract_state_changes`, assert it equals the nonce embedded in the meta-tx, and increment it — mirroring the logic in `check_and_increment_nonce` for version ≥ 1 transactions.
3. Use a version other than `0` for the synthetic `TxInfo`, or add an explicit nonce-check path for meta-transactions, so that the existing `check_and_increment_nonce` guard cannot be bypassed by the version-0 early-return.

---

### Proof of Concept

1. Alice signs a meta-tx authorising `transfer(Bob, 1000_STRK)` on her account contract. The hash covers only `(contract_address, __execute__, calldata, chain_id)` — no nonce.
2. Bob submits `outer_tx_A` (nonce=N) containing `meta_tx_v0(Alice, sig, transfer_calldata)`. The OS executes Alice's `__execute__`, transferring 1000 STRK. Alice's nonce in state is **not** incremented by the OS.
3. Bob submits `outer_tx_B` (nonce=N+1) containing the **identical** `meta_tx_v0(Alice, sig, transfer_calldata)`. The OS again executes Alice's `__execute__` — the same signature passes, the same transfer executes.
4. Steps 2–3 repeat until Alice's balance is exhausted. Each outer transaction is valid (distinct nonce); the OS never detects the meta-tx replay because no nonce is tracked for version-0 inner calls.

### Citations

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
