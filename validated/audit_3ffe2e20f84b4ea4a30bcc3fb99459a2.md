### Title
Nonce-less `meta_tx_v0` Hash Enables Unbounded Signature Replay — (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/transaction_hash/transaction_hash.cairo`)

---

### Summary

The `execute_meta_tx_v0` syscall constructs a version-0 transaction context with a hardcoded `nonce=0` and computes its hash via `compute_meta_tx_v0_hash`, which passes `additional_data_size=0` — meaning **no nonce is committed to the hash**. No on-chain nonce is checked or incremented for the target contract, and `__validate__` is entirely skipped (version-0 path). Any attacker who has observed a valid meta_tx_v0 signature can replay it in any future block with identical calldata, causing the target account's `__execute__` to re-run the same authorized action indefinitely.

---

### Finding Description

`execute_meta_tx_v0` in `syscall_impls.cairo` is a syscall callable from any executing contract. It:

1. Reads `contract_address`, `selector`, `calldata`, and `signature` from the caller.
2. Computes the transaction hash via `compute_meta_tx_v0_hash`:

```cairo
func compute_meta_tx_v0_hash{pedersen_ptr: HashBuiltin*}(...) -> felt {
    let (tx_hash) = deprecated_get_transaction_hash{hash_ptr=pedersen_ptr}(
        ...
        additional_data_size=0,          // ← no nonce committed to hash
        additional_data=cast(0, felt*),
    );
    return tx_hash;
}
```

3. Constructs a `TxInfo` with `version=0` and `nonce=0` (hardcoded):

```cairo
tempvar new_tx_info = new TxInfo(
    version=0,
    ...
    nonce=0,          // ← always zero, never checked or incremented
    ...
);
```

4. Calls `contract_call_helper` → `select_execute_entry_point_func`, which invokes the target contract's `__execute__` entry point directly.

The OS-level `check_and_increment_nonce` explicitly exempts version-0 transactions:

```cairo
func check_and_increment_nonce(...) {
    // Do not handle nonce for version 0.
    if (tx_info.version == 0) {
        return ();
    }
    ...
}
```

And `run_validate` is also skipped for version 0:

```cairo
// Do not run "__validate__" for version 0.
if (tx_execution_info.tx_info.version == 0) {
    return ();
}
```

Because the hash commits only to `(prefix, version=0, contract_address, selector, calldata, max_fee=0, chain_id)` — with no nonce — the same `(contract_address, calldata, signature)` triple produces an identical hash in every block. A signature that was valid once remains valid forever.

---

### Impact Explanation

**Critical — Direct loss of funds.**

If a user has ever authorized a meta_tx_v0 call (e.g., a token transfer), an attacker who captures the signature and calldata can replay it in any subsequent block by deploying a contract that issues the same `meta_tx_v0` syscall. The target account's `__execute__` receives the same hash and signature, accepts it as valid, and re-executes the transfer. This can be repeated until the account is drained.

---

### Likelihood Explanation

**High.** The `meta_tx_v0` syscall is reachable by any unprivileged contract deployer or transaction sender. No privileged role is required. The attacker only needs to:
1. Observe a prior meta_tx_v0 invocation (on-chain, publicly visible).
2. Deploy a contract that re-issues the same syscall with the captured parameters.

There is no on-chain state that invalidates the old signature after first use.

---

### Recommendation

**Short term:** Include a per-account nonce in `compute_meta_tx_v0_hash` by passing it as `additional_data` (analogous to how `compute_l1_handler_transaction_hash` passes `nonce` via `additional_data_size=1, additional_data=&nonce`). Check and increment the target contract's nonce in `execute_meta_tx_v0` before dispatching to `contract_call_helper`.

**Long term:** Add replay-protection tests that verify a meta_tx_v0 signature cannot be re-executed after first use, and consider whether the version-0 nonce exemption in `check_and_increment_nonce` should be narrowed or removed.

---

### Proof of Concept

1. **Block N**: Contract A calls `meta_tx_v0` targeting victim account V with calldata `transfer(attacker, 100_ETH)` and victim's valid signature `sig`. The OS computes `H = hash(invoke, 0, V, __execute__, calldata, 0, chain_id)`. V's `__execute__` verifies `sig` against `H` and executes the transfer.

2. **Block N+k**: Attacker deploys contract B. Contract B issues the identical `meta_tx_v0` syscall: same `contract_address=V`, same `calldata`, same `sig`. The OS recomputes the identical `H` (no nonce in hash). V's `__execute__` receives the same `(H, sig)` pair, verifies it as valid, and executes the transfer again.

3. Attacker repeats step 2 until V's balance is zero. No nonce is ever incremented; no state change invalidates `sig`.

**Root cause lines:** [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/execute_transaction_utils.cairo (L127-130)
```text
    // Do not run "__validate__" for version 0.
    if (tx_execution_info.tx_info.version == 0) {
        return ();
    }
```
