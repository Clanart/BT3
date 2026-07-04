### Title
Missing Nonce and Deadline in `meta_tx_v0` Signed Payload Enables Indefinite Replay - (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

---

### Summary

The `execute_meta_tx_v0` syscall implementation is the StarkNet OS analog of the reported Forwarder vulnerability. The `MetaTxV0Request` struct contains no nonce or deadline field, and `compute_meta_tx_v0_hash` does not commit to any replay-protection data. The OS explicitly skips nonce checking for version-0 transactions. As a result, any signed meta-tx v0 payload can be replayed indefinitely by any party who has observed it, with no OS-level mechanism to prevent re-execution.

---

### Finding Description

The `MetaTxV0Request` struct is defined as: [1](#0-0) 

It contains only `contract_address`, `selector`, `calldata_start/end`, and `signature_start/end`. There is no `nonce`, no `deadline`, and no block-number bound.

The hash committed to by the signer is computed in `compute_meta_tx_v0_hash`: [2](#0-1) 

The hash covers only `contract_address`, `entry_point_selector`, `calldata`, and `chain_id`. `additional_data_size=0` — no nonce, no timestamp, no deadline is committed to.

Inside `execute_meta_tx_v0`, the new `TxInfo` for the inner call is constructed with `nonce=0` hardcoded: [3](#0-2) 

The OS-level nonce check (`check_and_increment_nonce`) explicitly skips version-0 transactions: [4](#0-3) 

Therefore, the target account's on-chain nonce is never read, validated, or incremented during a meta-tx v0 execution. The same `(contract_address, selector, calldata, signature)` tuple produces the identical hash on every replay, and the OS provides zero replay protection.

---

### Impact Explanation

**Critical — Direct loss of funds.**

An account contract whose `__validate__` entry point does not implement its own used-hash tracking (which is the common case for v0-style accounts that rely on OS-level nonce enforcement) will accept the same meta-tx v0 signature on every replay. An attacker who has observed a legitimately submitted meta-tx v0 payload (e.g., a token transfer) can wrap it in a new outer transaction (with a fresh outer nonce) and re-execute the inner meta-tx v0 an unlimited number of times, draining the victim's token balance.

---

### Likelihood Explanation

The `meta_tx_v0` syscall is callable by any contract from within its `__execute__` entry point. Signed meta-tx v0 payloads are visible on-chain after the first submission. Any unprivileged actor who observes a submitted meta-tx v0 can immediately begin replaying it. No special access or key material is required beyond what is already public. [5](#0-4) 

---

### Recommendation

1. Add a `nonce` field to `MetaTxV0Request` and include it in `compute_meta_tx_v0_hash` (as `additional_data`). The OS should read, validate, and increment the target contract's nonce for meta-tx v0 executions, analogously to how `check_and_increment_nonce` works for regular invoke transactions.
2. Alternatively or additionally, add a `deadline` (block number or timestamp) field to `MetaTxV0Request` and enforce it in `execute_meta_tx_v0` by comparing against `block_context.block_info_for_execute`.

---

### Proof of Concept

**Attacker steps:**

1. Alice signs a meta-tx v0 payload: `sign(hash(contract=Alice, selector=__execute__, calldata=[transfer Bob 100], chain_id))`.
2. Relayer submits outer invoke tx → `execute_meta_tx_v0` runs → 100 tokens transferred to Bob. Nonce of Alice's account is **not** incremented by the OS for the inner call.
3. Attacker constructs a new outer invoke tx (different outer nonce, same inner `MetaTxV0Request` with Alice's original signature).
4. `execute_meta_tx_v0` recomputes the identical `meta_tx_hash` (same inputs, no nonce in hash).
5. Alice's `__validate__` receives `version=0, nonce=0, transaction_hash=<same hash>` and the same signature — it passes validation again.
6. 100 more tokens are transferred. Repeat until Alice's balance is zero.

The root cause is confirmed at: [6](#0-5) [7](#0-6)

### Citations

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/common/new_syscalls.cairo (L325-336)
```text
struct MetaTxV0Request {
    // The address of the L2 contract to call.
    contract_address: felt,
    // The selector of the function to call.
    selector: felt,
    // The calldata.
    calldata_start: felt*,
    calldata_end: felt*,
    // The signature.
    signature_start: felt*,
    signature_end: felt*,
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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo (L329-340)
```text
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
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo (L343-363)
```text
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
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/execute_transaction_utils.cairo (L63-67)
```text
func check_and_increment_nonce{contract_state_changes: DictAccess*}(tx_info: TxInfo*) -> () {
    // Do not handle nonce for version 0.
    if (tx_info.version == 0) {
        return ();
    }
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/execute_syscalls.cairo (L343-350)
```text
    assert selector = META_TX_V0_SELECTOR;
    execute_meta_tx_v0(block_context=block_context, caller_execution_context=execution_context);
    %{ OsLoggerExitSyscall %}
    return execute_syscalls(
        block_context=block_context,
        execution_context=execution_context,
        syscall_ptr_end=syscall_ptr_end,
    );
```
