### Title
Missing Nonce Guard in `execute_meta_tx_v0` Enables Intra-Transaction Replay of Meta Transactions — (File: `execution/syscall_impls.cairo`)

---

### Summary

The `execute_meta_tx_v0` syscall implementation in `syscall_impls.cairo` hardcodes `nonce=0` in the synthesized `TxInfo` for the target contract and never calls `check_and_increment_nonce` on the target. The meta-transaction hash also excludes any nonce. A malicious relayer contract can therefore call `execute_meta_tx_v0` an arbitrary number of times within a single outer transaction using the same victim-signed parameters, causing the victim account's `__execute__` to run repeatedly — directly analogous to the "callback called multiple times" class in the reference report.

---

### Finding Description

In `execute_meta_tx_v0` (lines 286–399 of `syscall_impls.cairo`), the OS synthesizes a new `TxInfo` for the target contract:

```cairo
tempvar new_tx_info = new TxInfo(
    version=0,
    account_contract_address=contract_address,
    ...
    nonce=0,          // ← hardcoded, never incremented
    ...
);
``` [1](#0-0) 

The hash used to authenticate the meta transaction is computed as:

```cairo
let meta_tx_hash = compute_meta_tx_v0_hash(
    contract_address=contract_address,
    entry_point_selector=selector,
    calldata=calldata_start,
    calldata_size=calldata_size,
    chain_id=old_tx_info.chain_id,
);
``` [2](#0-1) 

No nonce is included in the hash. After `contract_call_helper` executes the target's `__execute__`, the OS returns without touching the target contract's on-chain nonce.

Compare this to every regular account transaction type, which calls `check_and_increment_nonce` before execution:

```cairo
// execute_invoke_function_transaction
check_and_increment_nonce(tx_info=tx_info);
``` [3](#0-2) 

```cairo
// check_and_increment_nonce implementation
func check_and_increment_nonce{contract_state_changes: DictAccess*}(tx_info: TxInfo*) -> () {
    if (tx_info.version == 0) {
        return ();   // ← version-0 skips nonce check entirely
    }
    ...
    assert current_nonce = tx_info.nonce;
    ...nonce + 1...
``` [4](#0-3) 

Because `new_tx_info.version = 0`, even if `check_and_increment_nonce` were called it would be a no-op. The target contract's stored nonce is never incremented. On every repeated call the target's `__validate__` sees `tx_info.nonce = 0` matching the stored nonce `0`, so validation passes unconditionally.

---

### Impact Explanation

A malicious relayer contract can call `execute_meta_tx_v0` N times within one outer transaction using the same victim-signed `(contract_address, calldata, signature)` tuple. Each call:

1. Produces the identical `meta_tx_hash` (no nonce in hash).
2. Presents `nonce=0` to the target's `__validate__`, which matches the stored nonce (never incremented).
3. Executes the target's `__execute__` in full.

If `__execute__` transfers tokens (the primary use-case for meta transactions / gasless relaying), the victim loses `N × amount` tokens — bounded only by their balance and the outer transaction's gas limit. This is **direct loss of funds (Critical)**.

---

### Likelihood Explanation

The meta-tx-v0 feature is explicitly designed for gasless relaying: a third-party relayer pays the outer transaction fee while the victim signs the inner payload. Any relayer who receives a single valid victim signature can replay it within one outer transaction. No privileged access, leaked key, or operator collusion is required — only a deployed malicious relayer contract and one victim-signed meta tx. Likelihood is **medium-high** given the feature's intended deployment context.

---

### Recommendation

Add replay protection at the OS level for meta transactions. Options:

1. **Include a nonce in the meta-tx hash** — extend `compute_meta_tx_v0_hash` to accept a caller-supplied nonce and verify/increment the target contract's nonce in `execute_meta_tx_v0`, analogous to `check_and_increment_nonce`.
2. **Track consumed meta-tx hashes** — maintain a set of consumed `meta_tx_hash` values in the OS state and assert each hash is consumed at most once per block.
3. **Restrict to one meta-tx call per outer transaction** — add a per-transaction counter and assert it does not exceed 1.

---

### Proof of Concept

1. Victim signs: `meta_tx = {contract_address: victim_account, selector: __execute__, calldata: [transfer 100 STRK → attacker], chain_id: SN_MAIN}`.
2. Attacker deploys `MaliciousRelayer` whose `__execute__` calls `execute_meta_tx_v0` 10 times with the same `(victim_account, calldata, victim_signature)`.
3. Attacker submits an outer invoke transaction calling `MaliciousRelayer.__execute__`.
4. OS processes `execute_meta_tx_v0` × 10:
   - Each call computes the same `meta_tx_hash` (no nonce in hash). [2](#0-1) 
   - Each call presents `nonce=0` to victim's `__validate__`; stored nonce remains `0` throughout. [1](#0-0) 
   - Each call executes victim's `__execute__`, transferring 100 STRK.
5. Victim loses 1 000 STRK (or their full balance) from a single outer transaction they never authorized.

### Citations

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo (L331-340)
```text
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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L311-311)
```text
    check_and_increment_nonce(tx_info=tx_info);
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/execute_transaction_utils.cairo (L63-88)
```text
func check_and_increment_nonce{contract_state_changes: DictAccess*}(tx_info: TxInfo*) -> () {
    // Do not handle nonce for version 0.
    if (tx_info.version == 0) {
        return ();
    }

    tempvar state_entry: StateEntry*;
    %{ SetStateEntryToAccountContractAddress %}

    tempvar current_nonce = state_entry.nonce;
    with_attr error_message("Unexpected nonce.") {
        assert current_nonce = tx_info.nonce;
    }

    // Update contract_state_changes.
    tempvar new_state_entry = new StateEntry(
        class_hash=state_entry.class_hash,
        storage_ptr=state_entry.storage_ptr,
        nonce=current_nonce + 1,
    );
    dict_update{dict_ptr=contract_state_changes}(
        key=tx_info.account_contract_address,
        prev_value=cast(state_entry, felt),
        new_value=cast(new_state_entry, felt),
    );
    return ();
```
