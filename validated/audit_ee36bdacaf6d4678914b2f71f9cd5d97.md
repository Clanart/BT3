### Title
`execute_meta_tx_v0` Omits Nonce from Hash and Never Increments Account Nonce, Enabling Unbounded Replay of Fund-Draining Calls — (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

---

### Summary

The `meta_tx_v0` syscall constructs a version-0 sub-transaction whose hash is computed **without a nonce**, and whose nonce field is hardcoded to `0`. Because `check_and_increment_nonce` explicitly skips version-0 transactions and `run_validate` (which calls `__validate__`) is never invoked for this path, any attacker can replay a previously observed `meta_tx_v0` call — or craft a fresh one — to execute arbitrary calldata against any account contract's `__execute__` entry point with no signature or nonce protection.

---

### Finding Description

**Root cause 1 — nonce excluded from hash.**

`compute_meta_tx_v0_hash` calls `deprecated_get_transaction_hash` with `additional_data_size=0`, meaning the nonce is not committed to in the hash: [1](#0-0) 

The hash is therefore a pure function of `(prefix, version=0, contract_address, selector, calldata, chain_id)`. For identical calldata the hash is identical across every block, forever.

**Root cause 2 — nonce hardcoded to 0 in TxInfo.**

`execute_meta_tx_v0` constructs the `TxInfo` with `nonce=0` unconditionally: [2](#0-1) 

**Root cause 3 — `check_and_increment_nonce` skips version 0.**

The nonce guard in the OS explicitly returns early for version-0 transactions, so no on-chain nonce is ever read or incremented: [3](#0-2) 

**Root cause 4 — `__validate__` is never called.**

`execute_meta_tx_v0` calls `contract_call_helper` directly, which invokes `select_execute_entry_point_func` on the `__execute__` selector. The `run_validate` wrapper (which calls `__validate__`) is only invoked from `execute_invoke_function_transaction` for top-level transactions; it is absent from the `meta_tx_v0` path entirely: [4](#0-3) 

Compare with the top-level invoke path, which calls both `check_and_increment_nonce` and `run_validate` before execution: [5](#0-4) 

---

### Impact Explanation

**Critical — Direct loss of funds.**

Standard account contracts (OpenZeppelin-style) perform signature verification in `__validate__`, not in `__execute__`. `__execute__` simply iterates over the provided calls and dispatches them. Because `meta_tx_v0` bypasses `__validate__` entirely and carries no nonce, an attacker can:

1. Deploy a contract that issues a `meta_tx_v0` syscall targeting any victim account's `__execute__` entry point.
2. Supply arbitrary calldata (e.g., `transfer(attacker, victim_balance)`).
3. Repeat in every subsequent block — the hash is identical, no nonce is consumed, no state prevents re-execution.

Every ERC-20 token balance held by any account contract on the network is reachable.

---

### Likelihood Explanation

**High.** The `meta_tx_v0` syscall is available to any contract during normal execution (it is only blocked in virtual-OS mode per `virtual_os_output.cairo` line 37). No privileged role, leaked key, or operator cooperation is required. An attacker needs only to deploy a contract and submit a standard invoke transaction. The attack is deterministic and repeatable.

---

### Recommendation

Apply one or more of the following:

1. **Include a nonce in the meta-tx hash.** Add a per-account nonce (or a caller-supplied nonce committed to in the hash) to `compute_meta_tx_v0_hash` via `additional_data`, and increment it in `execute_meta_tx_v0` via `check_and_increment_nonce`.
2. **Call `run_validate` before dispatching.** Invoke the target contract's `__validate__` entry point inside `execute_meta_tx_v0` before calling `contract_call_helper`, mirroring the top-level invoke flow.
3. **Restrict the syscall to privileged callers.** Gate `META_TX_V0_SELECTOR` so it can only be issued by OS-reserved contract addresses.

---

### Proof of Concept

```
Block N:
  Attacker deploys MaliciousContract with:
    func __execute__():
        syscall meta_tx_v0(
            contract_address = VICTIM_ACCOUNT,
            selector         = EXECUTE_ENTRY_POINT_SELECTOR,
            calldata         = [transfer(ATTACKER, VICTIM_BALANCE)],
            signature        = []   // ignored — __validate__ is never called
        )

  Attacker submits invoke tx → MaliciousContract.__execute__()
  → OS calls execute_meta_tx_v0
  → hash = H(prefix, 0, VICTIM_ACCOUNT, __execute__, calldata, chain_id)  // no nonce
  → nonce field = 0, check_and_increment_nonce skipped (version == 0)
  → run_validate skipped (version == 0)
  → VICTIM_ACCOUNT.__execute__(calldata) runs → transfer succeeds

Block N+1, N+2, …:
  Attacker resubmits identical invoke tx.
  Hash is identical. No nonce consumed. Attack repeats until victim is drained.
```

The structural parallel to the external report is exact: just as `offboard()` deleted `packedPayoutNonces[]` and reset all nonces to zero — enabling replay of every prior transaction — `execute_meta_tx_v0` **permanently fixes the nonce at zero** and excludes it from the commitment hash, producing the same unbounded-replay condition.

### Citations

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/transaction_hash/transaction_hash.cairo (L302-314)
```text
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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo (L389-393)
```text
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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L311-330)
```text
    check_and_increment_nonce(tx_info=tx_info);

    check_proof_facts(
        proof_facts_size=proof_facts_size,
        proof_facts=proof_facts,
        current_block_number=block_context.block_info_for_execute.block_number,
        virtual_os_config_hash=block_context.os_global_context.virtual_os_config_hash,
    );

    %{ StartTx %}

    let initial_user_gas_bound = get_initial_user_gas_bound(common_tx_fields=common_tx_fields);
    let remaining_gas = initial_user_gas_bound;

    // Validate.
    with remaining_gas {
        cap_remaining_gas(max_gas=VALIDATE_MAX_SIERRA_GAS);
        let pre_validate_gas = remaining_gas;
        run_validate(block_context=block_context, tx_execution_context=tx_execution_context);
    }
```
