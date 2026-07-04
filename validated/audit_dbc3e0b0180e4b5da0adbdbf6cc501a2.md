### Title
`meta_tx_v0` Signature Replay Due to Missing Nonce in Transaction Hash - (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

---

### Summary

The `execute_meta_tx_v0` syscall constructs a transaction hash that commits to `(contract_address, selector, calldata, chain_id)` but **omits any nonce or uniqueness token**. Because the OS also explicitly skips nonce enforcement for version-0 transactions, an attacker who observes a valid `meta_tx_v0` call can replay the identical `(calldata, signature)` pair an unlimited number of times in new outer transactions, causing the target account's `__execute__` to re-run the same authorized action repeatedly.

---

### Finding Description

`execute_meta_tx_v0` in `syscall_impls.cairo` delegates hash computation to `compute_meta_tx_v0_hash`:

```cairo
let meta_tx_hash = compute_meta_tx_v0_hash(
    contract_address=contract_address,
    entry_point_selector=selector,
    calldata=calldata_start,
    calldata_size=calldata_size,
    chain_id=old_tx_info.chain_id,
);
``` [1](#0-0) 

`compute_meta_tx_v0_hash` passes `additional_data_size=0` — no nonce, no block number, no counter of any kind:

```cairo
func compute_meta_tx_v0_hash{pedersen_ptr: HashBuiltin*}(...) -> felt {
    let (tx_hash) = deprecated_get_transaction_hash{hash_ptr=pedersen_ptr}(
        ...
        additional_data_size=0,
        additional_data=cast(0, felt*),
    );
``` [2](#0-1) 

The resulting `TxInfo` is constructed with `nonce=0` and `version=0`: [3](#0-2) 

`check_and_increment_nonce` explicitly returns early for version-0 transactions, so no on-chain nonce is consumed or checked:

```cairo
func check_and_increment_nonce{contract_state_changes: DictAccess*}(tx_info: TxInfo*) -> () {
    // Do not handle nonce for version 0.
    if (tx_info.version == 0) {
        return ();
    }
``` [4](#0-3) 

`run_validate` is similarly skipped for version 0: [5](#0-4) 

The result: for any fixed `(contract_address, selector, calldata)` tuple, the `meta_tx_hash` is a **constant**. A signature that was valid once is valid forever, and the OS provides no mechanism to mark it as spent.

---

### Impact Explanation

**Critical — Direct loss of funds.**

A standard account contract's `__execute__` validates the caller's ECDSA signature against `tx_info.transaction_hash`. Because `meta_tx_hash` is deterministic and nonce-free, an attacker who captures a single valid `(calldata, signature)` pair — e.g., a token transfer — can replay it in arbitrarily many new outer transactions. Each replay causes the target account to re-execute the authorized action (e.g., transfer tokens to the attacker), draining the account's balance.

---

### Likelihood Explanation

**Medium-High.** All `meta_tx_v0` calls are visible on-chain. Any contract can issue a `meta_tx_v0` syscall with attacker-chosen parameters; no privileged role is required. The attacker only needs to:
1. Observe a successful `meta_tx_v0` call carrying a value-bearing action.
2. Deploy a trivial relay contract that re-issues the same syscall with the same `(contract_address, selector, calldata, signature)`.
3. Submit a normal outer transaction invoking that relay contract.

The outer transaction requires a valid account and fee payment, but these are standard unprivileged operations.

---

### Recommendation

Include a replay-prevention token in `compute_meta_tx_v0_hash`. The canonical fix (mirroring the remediation in the referenced report) is to add a **nonce** to the hash inputs and enforce it on-chain:

1. Add a `nonce` parameter to `compute_meta_tx_v0_hash` and pass it as `additional_data`.
2. In `execute_meta_tx_v0`, read the target contract's current nonce from `contract_state_changes`, include it in the hash, and increment it after a successful call — analogous to how `check_and_increment_nonce` works for version-3 transactions.
3. Alternatively, maintain a per-contract set of consumed `meta_tx_hash` values in state and reject duplicates.

---

### Proof of Concept

1. **Victim setup**: Account `A` (a Cairo 0 or Cairo 1 account) holds 1000 STRK. A legitimate caller invokes `meta_tx_v0` targeting `A` with `calldata = transfer(attacker, 100)` and a valid signature `S` from `A`'s owner. The call succeeds; `A` now holds 900 STRK.

2. **Hash is constant**: `meta_tx_hash = H(INVOKE_PREFIX, 0, A, __execute__, calldata, 0, chain_id)` — identical every time for the same inputs.

3. **Replay**: The attacker deploys contract `Evil` containing:
   ```
   fn __execute__(...) {
       meta_tx_v0(contract_address=A, selector=__execute__, calldata=transfer(attacker,100), signature=S)
   }
   ```
4. **Attacker submits** a normal invoke transaction calling `Evil.__execute__`. The OS executes `execute_meta_tx_v0`, recomputes the same `meta_tx_hash`, presents `(meta_tx_hash, S)` to `A.__execute__`, which validates successfully (same hash, same signature). Another 100 STRK is transferred.

5. **Repeat** step 4 until `A` is drained. No new signature from `A`'s owner is ever required.

### Citations

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo (L329-339)
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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/execute_transaction_utils.cairo (L127-130)
```text
    // Do not run "__validate__" for version 0.
    if (tx_execution_info.tx_info.version == 0) {
        return ();
    }
```
