### Title
`compute_meta_tx_v0_hash` Omits Nonce from Hash, Enabling Unbounded Signature Replay — (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/transaction_hash/transaction_hash.cairo`)

---

### Summary

`compute_meta_tx_v0_hash` passes `additional_data_size=0` to `deprecated_get_transaction_hash`, meaning no nonce is committed to in the meta-tx v0 hash. The OS also never increments any nonce for the target contract when processing a meta-tx v0. A single user-signed meta-tx v0 signature can therefore be replayed an unlimited number of times by any unprivileged attacker who submits fresh outer transactions, leading to direct loss of funds from the victim's account.

---

### Finding Description

`compute_meta_tx_v0_hash` in `transaction_hash.cairo` delegates to `deprecated_get_transaction_hash` with the following arguments:

```cairo
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
``` [1](#0-0) 

Compare this with `compute_l1_handler_transaction_hash`, which correctly commits to the nonce:

```cairo
additional_data_size=1,
additional_data=&nonce,
``` [2](#0-1) 

In `execute_meta_tx_v0`, the constructed `new_tx_info` hardcodes `nonce=0`:

```cairo
tempvar new_tx_info = new TxInfo(
    version=0,
    ...
    nonce=0,
    ...
);
``` [3](#0-2) 

The OS-level nonce guard, `check_and_increment_nonce`, explicitly skips all version-0 transactions:

```cairo
// Do not handle nonce for version 0.
if (tx_info.version == 0) {
    return ();
}
``` [4](#0-3) 

Because the hash is a deterministic function of `(contract_address, selector, calldata, chain_id)` with no nonce, the identical hash — and therefore the identical signature — is valid for every invocation of the same meta-tx v0 payload.

---

### Impact Explanation

**Critical — Direct loss of funds.**

A victim signs a meta-tx v0 payload (e.g., authorizing a token transfer of amount X to address Y). That signature is broadcast on-chain as part of the first outer transaction. An attacker observes the signature and the calldata. The attacker then deploys a contract that calls the `meta_tx_v0` syscall with the victim's signature and the same calldata, and submits N additional outer transactions (each with a fresh outer nonce). Each outer transaction causes the victim's account to re-execute `__execute__` with the original calldata, draining the victim's balance N times. The OS provides no counter-measure: the meta-tx hash is identical each time, the signature verifies each time, and no nonce is ever incremented for the target account.

---

### Likelihood Explanation

**High.** The `meta_tx_v0` syscall is callable by any contract from within any regular invoke transaction. The attacker needs only:
1. Observe a meta-tx v0 signature on-chain (it is stored in the outer transaction's syscall trace, which is public).
2. Deploy a contract that re-issues the `meta_tx_v0` syscall with the captured signature and calldata.
3. Submit multiple outer transactions calling that contract.

No privileged access, leaked key, or trusted role is required. The attacker is a standard unprivileged transaction sender.

---

### Recommendation

Include a nonce in `compute_meta_tx_v0_hash` — analogous to how `compute_l1_handler_transaction_hash` passes `additional_data_size=1, additional_data=&nonce`. The OS must also read and increment the target contract's nonce when processing a meta-tx v0, rather than hardcoding `nonce=0` and skipping the nonce check.

Concretely, in `transaction_hash.cairo`:

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
        additional_data=&nonce,
    );
    return tx_hash;
}
```

And in `execute_meta_tx_v0`, read the target contract's current nonce from `contract_state_changes`, pass it to `compute_meta_tx_v0_hash`, and increment it before execution.

---

### Proof of Concept

1. **Victim** deploys an account contract at address `A` and signs a meta-tx v0 payload:
   - `contract_address = A`
   - `selector = EXECUTE_ENTRY_POINT_SELECTOR`
   - `calldata = [transfer(token_contract, attacker, 100)]`
   - `chain_id = MAINNET`
   - Signature `σ` over `H(INVOKE_PREFIX, 0, A, selector, H(calldata), 0, chain_id)`.

2. **Victim** submits outer transaction T1 that calls a relayer contract, which issues `meta_tx_v0(A, selector, calldata, σ)`. 100 tokens are transferred. T1 is included in a block; `σ` and `calldata` are now public.

3. **Attacker** deploys contract `M` whose `__execute__` calls `meta_tx_v0(A, selector, calldata, σ)` with the captured values.

4. **Attacker** submits outer transactions T2, T3, … Tk, each calling `M.__execute__`. Each one produces the same meta-tx hash (no nonce), `σ` verifies each time, and 100 tokens are transferred from `A` to the attacker per transaction.

5. The victim's account is drained. The OS never rejects any of these transactions because `check_and_increment_nonce` is a no-op for version-0 transactions and the hash is identical each time. [5](#0-4) [6](#0-5) [7](#0-6)

### Citations

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/transaction_hash/transaction_hash.cairo (L233-235)
```text
        additional_data_size=1,
        additional_data=&nonce,
    );
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
