### Title
Missing Nonce in `meta_tx_v0` Signed Hash Enables Signature Replay — (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

---

### Summary

The `execute_meta_tx_v0` syscall computes a transaction hash that does not include a nonce, and the OS explicitly skips nonce checking for version-0 transactions. Any party who observes a valid `meta_tx_v0` signature can replay it an unlimited number of times against the target account contract, causing repeated execution of the signed `__execute__` calldata (e.g., repeated token transfers), resulting in direct loss of funds.

---

### Finding Description

`execute_meta_tx_v0` in `syscall_impls.cairo` constructs a meta-transaction hash by calling `compute_meta_tx_v0_hash`:

```cairo
let meta_tx_hash = compute_meta_tx_v0_hash(
    contract_address=contract_address,
    entry_point_selector=selector,
    calldata=calldata_start,
    calldata_size=calldata_size,
    chain_id=old_tx_info.chain_id,
);
``` [1](#0-0) 

`compute_meta_tx_v0_hash` delegates to `deprecated_get_transaction_hash` with `additional_data_size=0` and `additional_data=cast(0, felt*)`:

```cairo
func compute_meta_tx_v0_hash{pedersen_ptr: HashBuiltin*}(...) -> felt {
    let (tx_hash) = deprecated_get_transaction_hash{hash_ptr=pedersen_ptr}(
        ...
        additional_data_size=0,
        additional_data=cast(0, felt*),
    );
    return tx_hash;
}
``` [2](#0-1) 

No nonce is included in the hash. The resulting `TxInfo` is constructed with `nonce=0` and `version=0`:

```cairo
tempvar new_tx_info = new TxInfo(
    version=0,
    ...
    nonce=0,
    ...
);
``` [3](#0-2) 

The OS nonce enforcement function `check_and_increment_nonce` explicitly skips all version-0 transactions:

```cairo
func check_and_increment_nonce{contract_state_changes: DictAccess*}(tx_info: TxInfo*) -> () {
    // Do not handle nonce for version 0.
    if (tx_info.version == 0) {
        return ();
    }
    ...
}
``` [4](#0-3) 

`check_and_increment_nonce` is never called inside `execute_meta_tx_v0` for the target contract. Additionally, `run_validate` (which runs `__validate__`) is also skipped for version-0 transactions:

```cairo
// Do not run "__validate__" for version 0.
if (tx_execution_info.tx_info.version == 0) {
    return ();
}
``` [5](#0-4) 

The result: the hash signed for a `meta_tx_v0` is a pure function of `(contract_address, entry_point_selector, calldata, chain_id)`. It is permanently valid and replayable by anyone who observes it.

---

### Impact Explanation

**Critical — Direct loss of funds.**

A `meta_tx_v0` is designed to let a relayer call `__execute__` on a target account contract with a user-provided signature. If the signed calldata encodes a token transfer (e.g., ERC-20 `transfer` or `approve`), any observer of the original signature can replay it by deploying a contract that issues the same `meta_tx_v0` syscall with the same `(contract_address, calldata, signature)`. Because the hash does not commit to a nonce, the signature remains valid forever. Each replay causes the `__execute__` entry point to run again, draining the account of funds equal to the transfer amount per replay.

---

### Likelihood Explanation

**High.** The `meta_tx_v0` syscall is dispatched to any executing contract:

```cairo
assert selector = META_TX_V0_SELECTOR;
execute_meta_tx_v0(block_context=block_context, caller_execution_context=execution_context);
``` [6](#0-5) 

Any unprivileged user can deploy a contract that calls this syscall. All transaction data (including signatures) is public on-chain. Once a single `meta_tx_v0` is executed, the signature is visible and can be replayed by any party in any subsequent block.

---

### Recommendation

Include a nonce in the `meta_tx_v0` hash computation. Specifically:

1. Add a `nonce` parameter to `compute_meta_tx_v0_hash` and pass it as `additional_data` (analogous to how `compute_l1_handler_transaction_hash` passes `nonce` as `additional_data_size=1, additional_data=&nonce`).
2. In `execute_meta_tx_v0`, read the current nonce of `contract_address` from `contract_state_changes`, include it in the hash, and increment it after successful execution — regardless of the version-0 bypass in `check_and_increment_nonce`. [7](#0-6) 

---

### Proof of Concept

1. User U signs a `meta_tx_v0` authorizing `transfer(recipient=attacker, amount=1000)` on token contract T, targeting account contract A. The hash is `H = Pedersen(INVOKE_PREFIX, 0, A, __execute__, calldata_hash, 0, chain_id)`.
2. A relayer submits a transaction containing a contract call that issues the `meta_tx_v0` syscall with `(contract_address=A, calldata=[transfer args], signature=sig_U)`. The OS computes the same `H`, sets `tx_info.transaction_hash = H`, and calls `A.__execute__`. The transfer executes.
3. An attacker deploys contract `Replay` that also calls `meta_tx_v0` with the identical `(A, calldata, sig_U)`. The OS recomputes the same `H` (no nonce in the hash), `check_and_increment_nonce` is skipped (version=0), and `A.__execute__` runs again. The transfer executes a second time.
4. The attacker repeats step 3 until A is drained.

The root cause is confirmed at:
- `compute_meta_tx_v0_hash`: `additional_data_size=0` — no nonce in hash. [8](#0-7) 
- `execute_meta_tx_v0`: `nonce=0` in `TxInfo`, no `check_and_increment_nonce` call. [3](#0-2) 
- `check_and_increment_nonce`: version-0 bypass. [4](#0-3)

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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/execute_syscalls.cairo (L343-344)
```text
    assert selector = META_TX_V0_SELECTOR;
    execute_meta_tx_v0(block_context=block_context, caller_execution_context=execution_context);
```
