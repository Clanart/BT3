### Title
Meta-Transaction V0 Signatures Lack Nonce Binding and Are Permanently Replayable — (`File: crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/transaction_hash/transaction_hash.cairo`)

---

### Summary

The `compute_meta_tx_v0_hash` function omits any nonce or replay-protection field from the signed hash. Combined with the OS unconditionally setting `nonce=0` in the constructed `TxInfo` and skipping `check_and_increment_nonce` for version-0 transactions, a meta-transaction signature is permanently valid. Any party who obtains the signature can replay it an unlimited number of times, and the original signer has no mechanism to cancel or invalidate it.

---

### Finding Description

`compute_meta_tx_v0_hash` in `transaction_hash/transaction_hash.cairo` computes the hash that the account contract's `__execute__` entry point will verify:

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
        additional_data_size=0,          // ← no nonce, no deadline, no block number
        additional_data=cast(0, felt*),
    );
``` [1](#0-0) 

The hash covers only: `INVOKE_HASH_PREFIX | version=0 | contract_address | selector | calldata | max_fee=0 | chain_id`. No nonce, no block number, no expiry.

In `execute_meta_tx_v0` (in `syscall_impls.cairo`), the OS constructs the `TxInfo` with `nonce=0` hardcoded:

```cairo
tempvar new_tx_info = new TxInfo(
    version=0,
    ...
    nonce=0,          // ← always zero, never incremented
    ...
);
``` [2](#0-1) 

And `check_and_increment_nonce` explicitly skips version-0 transactions:

```cairo
func check_and_increment_nonce{contract_state_changes: DictAccess*}(tx_info: TxInfo*) -> () {
    // Do not handle nonce for version 0.
    if (tx_info.version == 0) {
        return ();
    }
``` [3](#0-2) 

Because `execute_meta_tx_v0` is a syscall available inside any contract execution, any unprivileged caller can invoke it with a previously-observed signature. The OS will recompute the same deterministic hash, the account contract will verify the same signature as valid, and the inner `__execute__` will run again — with no state change that would cause the second (or Nth) execution to fail. [4](#0-3) 

---

### Impact Explanation

**Critical — Direct loss of funds.**

A meta-transaction signature authorizing a token transfer (e.g., "transfer 1000 STRK to address A") can be replayed by any observer on every subsequent block. The victim's account balance is drained until it reaches zero. Because the hash is fully deterministic from public calldata and the nonce is always 0, there is no on-chain state that changes between replays to prevent re-execution.

---

### Likelihood Explanation

**High.** Meta-transaction signatures are broadcast publicly when first submitted. Any network participant can observe the signature and the calldata from the first execution and immediately begin replaying. No privileged access, leaked key, or special role is required — only the ability to submit a transaction that calls the `meta_tx_v0` syscall with the captured signature.

---

### Recommendation

Include a per-account nonce in the meta-transaction hash and increment it on each execution, mirroring the treatment of regular account transactions:

1. In `compute_meta_tx_v0_hash`, pass `additional_data_size=1` and `additional_data=&nonce`, where `nonce` is the current on-chain nonce of `contract_address`.
2. In `execute_meta_tx_v0`, call `check_and_increment_nonce` after constructing `new_tx_info` (with the real nonce, not `0`).
3. Alternatively, expose a dedicated cancellation syscall or allow the account contract to track and invalidate used meta-transaction hashes.

---

### Proof of Concept

1. Alice signs a meta-transaction: `transfer(bob, 1000_STRK)` targeting her account contract. The resulting hash is `H = Pedersen(INVOKE_PREFIX, 0, alice_addr, __execute__, calldata, 0, chain_id)`.
2. Alice's delegatee submits a transaction that calls `meta_tx_v0` with Alice's signature. The OS computes `H`, Alice's `__execute__` verifies the ECDSA signature over `H`, and 1000 STRK is transferred to Bob.
3. An attacker (or Bob himself) immediately submits another transaction calling `meta_tx_v0` with the same `(contract_address, selector, calldata, signature)`.
4. The OS recomputes the identical `H` (no nonce in the hash, `nonce=0` in `TxInfo`, no nonce increment), Alice's `__execute__` verifies the same signature as valid again, and another 1000 STRK is transferred.
5. Steps 3–4 repeat until Alice's balance is zero. Alice has no mechanism to cancel the signature because there is no nonce she can increment to invalidate it.

### Citations

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/transaction_hash/transaction_hash.cairo (L295-314)
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
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo (L329-393)
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
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/execute_transaction_utils.cairo (L63-67)
```text
func check_and_increment_nonce{contract_state_changes: DictAccess*}(tx_info: TxInfo*) -> () {
    // Do not handle nonce for version 0.
    if (tx_info.version == 0) {
        return ();
    }
```
