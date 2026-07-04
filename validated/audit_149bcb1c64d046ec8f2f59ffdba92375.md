### Title
`compute_meta_tx_v0_hash` Omits Nonce, Enabling Permanent Signature Replay Against Account Contracts — (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/transaction_hash/transaction_hash.cairo`)

---

### Summary

The OS-level hash function for `meta_tx_v0` (`compute_meta_tx_v0_hash`) does not include a nonce or any expiry/deadline field. Combined with the OS explicitly skipping nonce enforcement for version-0 transactions, a captured `meta_tx_v0` signature is permanently valid and can be replayed by any unprivileged transaction sender to re-execute the same authorized operation indefinitely, leading to direct loss of funds.

---

### Finding Description

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
        ...
        max_fee=0,
        chain_id=chain_id,
        additional_data_size=0,          // ← no nonce
        additional_data=cast(0, felt*),  // ← no deadline
    );
    return tx_hash;
}
``` [1](#0-0) 

The resulting hash is a pure function of `(INVOKE_HASH_PREFIX, 0, contract_address, EXECUTE_ENTRY_POINT_SELECTOR, calldata_hash, 0, chain_id)`. No nonce, block number, or timestamp is mixed in.

When `execute_meta_tx_v0` builds the synthetic `TxInfo` for the called contract, it hard-codes `nonce=0`:

```cairo
tempvar new_tx_info = new TxInfo(
    version=0,
    ...
    transaction_hash=meta_tx_hash,
    nonce=0,          // ← always zero
    ...
);
``` [2](#0-1) 

The OS nonce-enforcement function explicitly skips version-0 transactions:

```cairo
func check_and_increment_nonce{...}(tx_info: TxInfo*) {
    // Do not handle nonce for version 0.
    if (tx_info.version == 0) {
        return ();
    }
    ...
}
``` [3](#0-2) 

Similarly, `run_validate` skips the `__validate__` entry point for version-0:

```cairo
// Do not run "__validate__" for version 0.
if (tx_execution_info.tx_info.version == 0) {
    return ();
}
``` [4](#0-3) 

The OS therefore provides **zero replay protection** for `meta_tx_v0`: no nonce is checked, no nonce is incremented, and the hash itself contains no uniqueness-bearing field.

---

### Impact Explanation

Any account contract that relies on the OS-supplied `transaction_hash` (obtained via `get_execution_info`) to verify a user's ECDSA signature inside `__execute__` will accept the same signature on every future replay. Because the hash is identical for identical `(contract_address, calldata, chain_id)` tuples, an attacker who observes one successful `meta_tx_v0` invocation (e.g., a signed ERC-20 transfer) can re-submit the same calldata + signature in any subsequent outer transaction, causing the target account to re-execute the transfer repeatedly. This constitutes **direct loss of funds** — matching the allowed Critical impact tier.

---

### Likelihood Explanation

The `meta_tx_v0` syscall is reachable by any unprivileged transaction sender: a malicious outer contract simply issues the `META_TX_V0_SELECTOR` syscall with the victim's previously observed calldata and signature. [5](#0-4) 

All inputs needed for the replay (calldata, signature) are visible on-chain after the first execution. No privileged access, key leakage, or social engineering is required. Likelihood is **High**.

---

### Recommendation

Include a monotonically-increasing, per-account nonce (or a caller-supplied deadline/expiry block number) in the data committed to by `compute_meta_tx_v0_hash`, and enforce it in `execute_meta_tx_v0` the same way `check_and_increment_nonce` does for version-3 transactions. Concretely, pass `additional_data_size=1` and `additional_data=&nonce` (mirroring `compute_l1_handler_transaction_hash`) and verify + increment the nonce in `execute_meta_tx_v0` before constructing the synthetic `TxInfo`. [6](#0-5) 

---

### Proof of Concept

1. **Victim** calls outer contract `A`, which internally issues `execute_meta_tx_v0` targeting victim account `V` with calldata `transfer(attacker, 100_STRK)` and victim's valid ECDSA signature `σ`.
2. OS computes `H = hash(INVOKE_PREFIX, 0, V, __execute__, transfer(attacker,100), 0, chain_id)`. Victim's `__execute__` verifies `σ` against `H` — succeeds. 100 STRK transferred.
3. **Attacker** deploys contract `B`. In the same or any future block, `B` issues `execute_meta_tx_v0` with identical `(V, transfer(attacker,100), σ)`.
4. OS recomputes the same `H` (nonce is not in the hash; `additional_data_size=0`). Victim's `__execute__` again verifies `σ` against `H` — succeeds. Another 100 STRK transferred.
5. Step 4 repeats until victim's balance is zero. No new signature from the victim is ever required. [7](#0-6) [8](#0-7)

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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/execute_transaction_utils.cairo (L127-130)
```text
    // Do not run "__validate__" for version 0.
    if (tx_execution_info.tx_info.version == 0) {
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
