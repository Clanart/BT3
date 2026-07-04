### Title
Meta Transaction V0 Hash Excludes Nonce, Enabling Unbounded Replay of Signed Calls - (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/transaction_hash/transaction_hash.cairo`)

---

### Summary

`compute_meta_tx_v0_hash` omits the nonce from the hash preimage, and `execute_meta_tx_v0` never calls `check_and_increment_nonce`. Because `check_and_increment_nonce` explicitly skips version-0 transactions anyway, there is zero replay protection for meta transactions at the OS level. An attacker who possesses a valid signature for any meta_tx_v0 call can replay it an unlimited number of times within the same or future blocks, causing the target account's `__execute__` entry point to be invoked repeatedly with the same calldata.

---

### Finding Description

**Root cause 1 — hash excludes nonce:**

`compute_meta_tx_v0_hash` calls `deprecated_get_transaction_hash` with `additional_data_size=0` and `additional_data=cast(0, felt*)`: [1](#0-0) 

Compare this with `compute_l1_handler_transaction_hash`, which passes `additional_data_size=1, additional_data=&nonce` — the nonce is explicitly committed into the L1-handler hash, preventing replay. No equivalent protection exists for meta_tx_v0. [2](#0-1) 

**Root cause 2 — nonce is hardcoded to 0 and never incremented:**

Inside `execute_meta_tx_v0`, the synthesised `TxInfo` always sets `nonce=0`: [3](#0-2) 

`check_and_increment_nonce` is never called for this path. Even if it were, it explicitly returns early for version-0 transactions: [4](#0-3) 

**Root cause 3 — only `__execute__` is called, not `__validate__`:**

`execute_meta_tx_v0` enforces `selector == EXECUTE_ENTRY_POINT_SELECTOR` and calls `contract_call_helper` directly: [5](#0-4) 

Standard account contracts perform nonce enforcement inside `__validate__`, not `__execute__`. Because `__validate__` is never invoked for meta_tx_v0, the account's own nonce guard is bypassed entirely.

**Combined effect:** For a fixed tuple `(contract_address, selector, calldata, chain_id)`, `compute_meta_tx_v0_hash` always returns the same value. A signature that was valid once remains valid forever. The OS never increments the target account's nonce, so the account's stored nonce stays at whatever value it had before the first replay, and every subsequent replay presents the same `nonce=0` in `TxInfo`.

---

### Impact Explanation

**Critical — Direct loss of funds.**

Any meta_tx_v0 call that transfers tokens (e.g., an ERC-20 `transfer` wrapped in `__execute__`) can be replayed indefinitely. Each replay is a fully processed OS-level execution: state changes are committed, fees are charged to the outer transaction's sender, and token balances are updated. A victim whose account accepted one meta_tx_v0 transfer can be drained of all funds by an attacker who replays the same signed call in subsequent transactions.

---

### Likelihood Explanation

The `meta_tx_v0` syscall is callable by any deployed contract; no privileged role is required. An attacker needs only:

1. Observe a single on-chain meta_tx_v0 invocation (calldata and signature are public in the transaction trace).
2. Deploy a contract that re-issues the same `MetaTxV0Request` with the captured signature.
3. Submit that contract's transaction repeatedly.

No key compromise, Sybil attack, or operator collusion is needed. The attack is fully self-contained and deterministic.

---

### Recommendation

Include the nonce in the meta_tx_v0 hash preimage, mirroring how `compute_l1_handler_transaction_hash` passes the nonce as `additional_data`. Additionally, call `check_and_increment_nonce` (or an equivalent version-0-aware nonce check) after computing the hash inside `execute_meta_tx_v0`, so that each meta transaction can only be executed once per account nonce value.

---

### Proof of Concept

1. Account `A` (nonce = 5) signs a meta_tx_v0 that calls `token.transfer(victim=B, amount=1000)`. The hash is `H = hash(prefix, 0, A, __execute__, calldata, 0, chain_id)` — no nonce in preimage.
2. Attacker deploys contract `Evil` containing:
   ```
   meta_tx_v0(contract_address=A, selector=__execute__, calldata=<transfer calldata>, signature=<captured sig>)
   ```
3. Attacker submits `Evil.__execute__` in a loop across multiple transactions. Each invocation:
   - Computes the same `H` (nonce absent from hash).
   - Presents `TxInfo{version=0, nonce=0, transaction_hash=H}` to `A.__execute__`.
   - `A.__execute__` verifies the signature against `H` — valid every time.
   - `A`'s on-chain nonce is never incremented (OS skips it for version 0).
   - The token transfer executes, draining `A`'s balance.
4. After `k` replays, `A` has lost `k × 1000` tokens with no OS-level mechanism to stop it.

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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo (L317-399)
```text
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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/execute_transaction_utils.cairo (L63-68)
```text
func check_and_increment_nonce{contract_state_changes: DictAccess*}(tx_info: TxInfo*) -> () {
    // Do not handle nonce for version 0.
    if (tx_info.version == 0) {
        return ();
    }

```
