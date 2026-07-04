### Title
Bootstrap Declare Path Bypasses Signature Verification, Enabling Unauthorized Class Declaration — (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo`)

---

### Summary

`execute_declare_transaction` contains a privileged "bootstrap" shortcut that skips all account validation (signature, nonce, fee) when a declare transaction carries `sender_address == 'BOOTSTRAP'`, `nonce == 0`, `version == 3`, and zero resource bounds. Because the only gate is a felt-literal comparison on the sender address — and the signature-verification step (`__validate_declare__`) is the very thing being skipped — any unprivileged class declarer can craft a transaction that satisfies these conditions and permanently register an arbitrary class hash with an attacker-chosen `compiled_class_hash`, without owning or signing from the `'BOOTSTRAP'` account.

---

### Finding Description

In `execute_declare_transaction`, after the transaction hash is computed, the following branch fires before any account-level checks:

```cairo
// Do not run validate or perform any account-related actions for declare transactions that
// meet the following conditions.
// This flow is used for the sequencer to bootstrap a new system.
if (sender_address == 'BOOTSTRAP' and tx_info.nonce == 0 and tx_info.version == 3) {
    let max_possible_fee = compute_max_possible_fee(tx_info=tx_info);
    if (max_possible_fee == 0) {
        assert_not_zero(compiled_class_hash);
        dict_update{dict_ptr=contract_class_changes}(
            key=[class_hash_ptr], prev_value=0, new_value=compiled_class_hash
        );
        %{ SkipTx %}
        return ();
    }
}
``` [1](#0-0) 

The four conditions (`sender_address`, `nonce`, `version`, `max_possible_fee`) are all data fields supplied by the transaction submitter. None of them is cryptographically bound to a privileged identity. The signature-verification entry point `__validate_declare__` — which is the only mechanism that would prove the sender controls the `'BOOTSTRAP'` account — is entirely skipped by this branch. [2](#0-1) 

The normal declare path that follows (lines 779–827) does call `check_and_increment_nonce`, runs `__validate_declare__`, and charges a fee. The bootstrap branch bypasses all three. [3](#0-2) 

The `compiled_class_hash` written into `contract_class_changes` is attacker-supplied and is only checked to be non-zero. It is **not** verified against the Sierra class at this point; verification only occurs at execution time via `find_element` in `execute_entry_point`. [4](#0-3) 

Furthermore, the `dict_update` call enforces `prev_value=0`, meaning a class hash can be declared **only once**. Once an attacker registers a class hash with a bogus `compiled_class_hash`, the entry is permanent and cannot be overwritten by a legitimate declaration. [5](#0-4) 

---

### Impact Explanation

**High — Network not being able to confirm new transactions (total network shutdown).**

If an attacker front-runs the bootstrap declaration of any class that a critical system contract (e.g., the fee token, the account class used by the sequencer) depends on, and registers it with a `compiled_class_hash` that does not correspond to any entry in the `compiled_class_facts_bundle`, then every subsequent block that attempts to execute that contract will reach `find_element` with an unknown key.

### Citations

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L761-776)
```text
    // Do not run validate or perform any account-related actions for declare transactions that
    // meet the following conditions.
    // This flow is used for the sequencer to bootstrap a new system.
    if (sender_address == 'BOOTSTRAP' and tx_info.nonce == 0 and tx_info.version == 3) {
        let max_possible_fee = compute_max_possible_fee(tx_info=tx_info);
        if (max_possible_fee == 0) {
            // Declare the class hash and skip the rest of the transaction.
            // Note that prev_value=0 enforces that a class may be declared only once.
            assert_not_zero(compiled_class_hash);
            dict_update{dict_ptr=contract_class_changes}(
                key=[class_hash_ptr], prev_value=0, new_value=compiled_class_hash
            );
            %{ SkipTx %}
            return ();
        }
    }
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L778-825)
```text
    // Increment nonce.
    check_and_increment_nonce(tx_info=tx_info);

    // Prepare the validate execution context.
    let (state_entry: StateEntry*) = dict_read{dict_ptr=contract_state_changes}(key=sender_address);
    // The calldata for declare tx is the class hash.
    local validate_declare_execution_context: ExecutionContext* = new ExecutionContext(
        entry_point_type=ENTRY_POINT_TYPE_EXTERNAL,
        class_hash=state_entry.class_hash,
        calldata_size=1,
        calldata=class_hash_ptr,
        execution_info=new ExecutionInfo(
            block_info=block_context.block_info_for_validate,
            tx_info=tx_info,
            caller_address=ORIGIN_ADDRESS,
            contract_address=sender_address,
            selector=VALIDATE_DECLARE_ENTRY_POINT_SELECTOR,
        ),
        deprecated_tx_info=deprecated_tx_info,
    );

    let remaining_gas = get_initial_user_gas_bound(common_tx_fields=common_tx_fields);
    with remaining_gas {
        cap_remaining_gas(max_gas=VALIDATE_MAX_SIERRA_GAS);
        // Run the account contract's "__validate_declare__" entry point.
        %{ StartTx %}
        let (retdata_size, retdata, is_deprecated) = non_reverting_select_execute_entry_point_func(
            block_context=block_context, execution_context=validate_declare_execution_context
        );
    }
    // TODO(Yoni): calculate the gas consumed and use it to charge fee (for all transactions).
    if (is_deprecated == 0) {
        assert retdata_size = 1;
        assert retdata[0] = VALIDATED;
    }

    // Declare the class hash.
    // Note that prev_value=0 enforces that a class may be declared only once.
    assert_not_zero(compiled_class_hash);
    dict_update{dict_ptr=contract_class_changes}(
        key=[class_hash_ptr], prev_value=0, new_value=compiled_class_hash
    );

    // Charge fee.
    charge_fee(
        block_context=block_context, tx_execution_context=validate_declare_execution_context
    );
    %{ EndTx %}
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/execute_entry_point.cairo (L154-166)
```text
    let (compiled_class_hash: felt) = dict_read{dict_ptr=contract_class_changes}(
        key=execution_context.class_hash
    );

    // The key must be at offset 0.
    static_assert CompiledClassFact.hash == 0;
    let compiled_class_facts_bundle = block_context.os_global_context.compiled_class_facts_bundle;
    let (compiled_class_fact: CompiledClassFact*) = find_element(
        array_ptr=compiled_class_facts_bundle.compiled_class_facts,
        elm_size=CompiledClassFact.SIZE,
        n_elms=compiled_class_facts_bundle.n_compiled_class_facts,
        key=compiled_class_hash,
    );
```
