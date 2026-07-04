### Title
`compute_meta_tx_v0_hash` Omits Nonce, Enabling Signature Replay of Meta-Transactions — (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/transaction_hash/transaction_hash.cairo`)

---

### Summary

`compute_meta_tx_v0_hash` computes the hash for a meta-transaction v0 without including any nonce or uniqueness field in `additional_data`. Because the hash is a pure deterministic function of `(contract_address, selector, calldata, chain_id)`, any valid meta-tx v0 signature can be replayed an unlimited number of times by any unprivileged transaction sender, leading to direct loss of funds.

---

### Finding Description

The `meta_tx_v0` syscall is designed to allow a relayer contract to submit a "version-0 style" transaction on behalf of a user. The OS computes the hash that the account contract's `__execute__` entry point will see as `tx_info.transaction_hash`.

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
        additional_data_size=0,          // ← no nonce
        additional_data=cast(0, felt*),  // ← no nonce
    );
    return tx_hash;
}
``` [1](#0-0) 

The resulting hash is `H(INVOKE_HASH_PREFIX, 0, contract_address, selector, H(calldata), 0, chain_id)` — entirely deterministic with no per-invocation uniqueness.

In `execute_meta_tx_v0`, the new `TxInfo` is constructed with `nonce=0` and no call to `check_and_increment_nonce`:

```cairo
tempvar new_tx_info = new TxInfo(
    version=0,
    account_contract_address=contract_address,
    ...
    transaction_hash=meta_tx_hash,
    chain_id=old_tx_info.chain_id,
    nonce=0,
    ...
);
``` [2](#0-1) 

`check_and_increment_nonce` explicitly skips version-0 transactions:

```cairo
func check_and_increment_nonce{contract_state_changes: DictAccess*}(tx_info: TxInfo*) -> () {
    // Do not handle nonce for version 0.
    if (tx_info.version == 0) {
        return ();
    }
``` [3](#0-2) 

And `execute_meta_tx_v0` never calls `check_and_increment_nonce` at all — it proceeds directly to `contract_call_helper`: [4](#0-3) 

The OS provides zero replay protection for meta_tx_v0 at the hash or nonce level.

---

### Impact Explanation

**Critical — Direct loss of funds.**

A user signs a meta_tx_v0 payload (e.g., a token transfer: `calldata = [recipient, amount]`) and hands it to a relayer. The relayer submits an outer transaction that invokes the meta_tx_v0 syscall. Because the meta_tx_v0 hash is `H(INVOKE_HASH_PREFIX, 0, contract_address, selector, H(calldata), 0, chain_id)` — with no nonce — the same signature is valid for every future invocation with the same calldata.

Any party who observes the signature (on-chain or off-chain) can submit new outer transactions (each with their own valid nonce) that replay the meta_tx_v0 indefinitely. Each replay executes the account's `__execute__` entry point with the same hash and signature, draining the account of whatever asset the calldata specifies.

---

### Likelihood Explanation

**High.** The meta_tx_v0 syscall is specifically designed for the relayer/gasless-transaction use case, where a user's signature is broadcast to relayers. Any unprivileged transaction sender can submit an outer transaction invoking the meta_tx_v0 syscall with a previously-seen signature. No privileged access, leaked key, or operator cooperation is required — only the ability to submit a standard StarkNet transaction.

---

### Recommendation

Include a nonce in the meta_tx_v0 hash. Pass the nonce as `additional_data` to `deprecated_get_transaction_hash`, and enforce that the nonce matches and is incremented in `execute_meta_tx_v0`:

```cairo
func compute_meta_tx_v0_hash{pedersen_ptr: HashBuiltin*}(
    contract_address: felt,
    entry_point_selector: felt,
    calldata: felt*,
    calldata_size: felt,
    chain_id: felt,
    nonce: felt,   // add nonce parameter
) -> felt {
    let (tx_hash) = deprecated_get_transaction_hash{hash_ptr=pedersen_ptr}(
        ...
        additional_data_size=1,
        additional_data=&nonce,   // include nonce in hash
    );
    return tx_hash;
}
```

And in `execute_meta_tx_v0`, read the current nonce from `contract_state_changes` for `contract_address`, pass it to `compute_meta_tx_v0_hash`, and increment it afterward — analogous to how `check_and_increment_nonce` works for v3 transactions.

---

### Proof of Concept

1. User signs a meta_tx_v0 payload: `(contract_address=ACCOUNT, selector=__execute__, calldata=[transfer_to_attacker, 1000_STRK], chain_id=SN_MAIN)`. The resulting hash is `H(INVOKE_HASH_PREFIX, 0, ACCOUNT, __execute__, H(calldata), 0, SN_MAIN)`.

2. User submits (or hands to a relayer) an outer transaction T1 that calls a relayer contract, which invokes `meta_tx_v0(contract_address=ACCOUNT, selector=__execute__, calldata=..., signature=SIG)`.

3. T1 is included in a block. The OS computes `meta_tx_hash` identically and calls `ACCOUNT.__execute__` with `tx_info.transaction_hash = meta_tx_hash` and `tx_info.signature = SIG`. The account validates the signature and executes the transfer.

4. Attacker submits T2, T3, … Tn — each a fresh outer transaction with a new nonce — all invoking the same meta_tx_v0 with the same `SIG`. Each time, `compute_meta_tx_v0_hash` produces the identical hash. The account's `__validate__` accepts the same signature each time. The transfer executes repeatedly until the account is drained. [1](#0-0) [5](#0-4) [2](#0-1)

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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo (L386-399)
```text
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
