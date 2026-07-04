### Title
Missing Nonce in `compute_meta_tx_v0_hash` Enables Signature Replay for Meta Transactions - (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/transaction_hash/transaction_hash.cairo`)

---

### Summary

The `compute_meta_tx_v0_hash` function omits a nonce from the hash preimage and the OS skips nonce enforcement for version-0 transactions. Any valid `meta_tx_v0` signature observed on-chain can be replayed indefinitely by an unprivileged attacker, causing repeated unauthorized execution of the target account's `__execute__` entry point — directly analogous to the ERC1271 signature replay vulnerability in the reference report.

---

### Finding Description

`compute_meta_tx_v0_hash` delegates to `deprecated_get_transaction_hash` with `additional_data_size=0` and `additional_data=cast(0, felt*)`:

```cairo
// transaction_hash.cairo L302-L314
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

Contrast this with `compute_l1_handler_transaction_hash`, which uses the same `deprecated_get_transaction_hash` but correctly includes a nonce:

```cairo
// transaction_hash.cairo L233-L234
additional_data_size=1,
additional_data=&nonce,
``` [2](#0-1) 

In `execute_meta_tx_v0`, the resulting `new_tx_info` is constructed with `nonce=0` hardcoded:

```cairo
// syscall_impls.cairo L343-L363
tempvar new_tx_info = new TxInfo(
    version=0,
    ...
    nonce=0,
    ...
);
``` [3](#0-2) 

`check_and_increment_nonce` explicitly skips nonce enforcement for version-0 transactions:

```cairo
// execute_transaction_utils.cairo L64-L67
if (tx_info.version == 0) {
    return ();
}
``` [4](#0-3) 

`run_validate` also skips `__validate__` for version-0 transactions:

```cairo
// execute_transaction_utils.cairo L128-L130
if (tx_execution_info.tx_info.version == 0) {
    return ();
}
``` [5](#0-4) 

The resulting hash is a pure deterministic function of `(INVOKE_HASH_PREFIX, 0, contract_address, __execute__, calldata, 0, chain_id)`. Because no nonce is included and no nonce is checked, the same `(contract_address, calldata, signature)` triple is valid forever.

---

### Impact Explanation

**Critical — Direct loss of funds.**

An account contract's `__execute__` entry point verifies the caller's signature against `tx_info.transaction_hash`. Because the hash is nonce-free, a signature that was legitimately produced for a specific calldata (e.g., "transfer 100 tokens to address X") remains valid for all future blocks. An attacker who observes a valid `meta_tx_v0` signature on-chain can replay it by issuing the same syscall from any contract they control, causing the victim account to re-execute the same financial operation repeatedly until funds are exhausted.

---

### Likelihood Explanation

**Medium-High.** The `meta_tx_v0` syscall is a public protocol feature callable by any contract during execution. Any transaction sender can deploy a contract that issues the syscall with a replayed signature. The only prerequisite is observing a valid `(contract_address, calldata, signature)` triple from a prior block — trivially achievable by reading chain history. No privileged access, leaked key, or operator cooperation is required.

---

### Recommendation

Include a nonce in the meta tx v0 hash preimage, mirroring the pattern used by `compute_l1_handler_transaction_hash`:

```cairo
func compute_meta_tx_v0_hash{pedersen_ptr: HashBuiltin*}(
    contract_address: felt,
    entry_point_selector: felt,
    calldata: felt*,
    calldata_size: felt,
    chain_id: felt,
    nonce: felt,   // ← add nonce parameter
) -> felt {
    let (tx_hash) = deprecated_get_transaction_hash{hash_ptr=pedersen_ptr}(
        ...
        additional_data_size=1,
        additional_data=&nonce,
    );
    return tx_hash;
}
```

The nonce must be sourced from the target account's on-chain state and incremented after each successful meta tx v0 execution, analogous to how `check_and_increment_nonce` operates for version-1/3 transactions. The `execute_meta_tx_v0` handler in `syscall_impls.cairo` must read the account nonce from `contract_state_changes`, pass it to the hash function, and write back the incremented nonce.

---

### Proof of Concept

1. **Block N**: Legitimate user U calls contract A, which issues `meta_tx_v0` syscall targeting account B with `calldata = [transfer, victim_recipient, 100_tokens]` and a valid ECDSA signature `σ` over `H(INVOKE_HASH_PREFIX, 0, B, __execute__, H(calldata), 0, chain_id)`.

2. **Block N+1**: Attacker deploys contract Evil. Evil's constructor (or any entry point) issues the identical `meta_tx_v0` syscall: same `contract_address=B`, same `calldata`, same `σ`.

3. The OS computes the identical hash (no nonce in preimage), constructs `new_tx_info` with `nonce=0`, skips `__validate__`, and calls `B.__execute__` with `σ` and the same hash.

4. Account B's `__execute__` verifies `σ` against `tx_info.transaction_hash` — the check passes because the hash is identical to the one `σ` was originally produced for.

5. The transfer executes again. Steps 2–5 repeat until B's balance is drained. [6](#0-5) [7](#0-6) [8](#0-7)

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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/execute_transaction_utils.cairo (L128-130)
```text
    if (tx_execution_info.tx_info.version == 0) {
        return ();
    }
```
