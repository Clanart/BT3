### Title
Unauthorized Class Declaration via Unenforced Bootstrap Privilege Bypass — (File: `execution/transaction_impls.cairo`)

### Summary
`execute_declare_transaction` contains a privileged "bootstrap" code path that skips signature validation, nonce enforcement, and fee charging. The gate condition is a plain felt comparison against the magic string `'BOOTSTRAP'`. Because any unprivileged transaction sender can set their `sender_address` field to that felt value, the entire authorization layer for class declaration is bypassable.

### Finding Description

In `transaction_impls.cairo` lines 764–776, after computing the transaction hash and filling `tx_info`, the OS checks:

```cairo
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

When this branch is taken, the function returns immediately — before:

- `check_and_increment_nonce` (line 779) — nonce replay protection is skipped
- `run_validate` / `non_reverting_select_execute_entry_point_func` (lines 804–806) — `__validate_declare__` is never called, so no signature is verified
- `charge_fee` (line 822) — no fee is deducted [2](#0-1) 

The `sender_address` is loaded from the hint `%{ DeclareTxFields %}` and is directly included in the transaction hash computation. It is a plain `felt` field in the transaction — there is no Cairo constraint that forces it to correspond to a deployed contract. The string literal `'BOOTSTRAP'` is simply the felt `0x424f4f545354524150`. Any party that can submit a declare transaction can set this field to that value.

The only guard on the declared class is `prev_value=0` in the `dict_update`, which prevents overwriting an already-declared class hash. New class hashes are entirely unprotected. [3](#0-2) 

### Impact Explanation

An unprivileged attacker can:

1. Declare any previously-undeclared class hash paired with an arbitrary `compiled_class_hash` — with no account contract, no signature, no fee, and no nonce consumed.
2. Front-run a legitimate protocol upgrade or system-class declaration: because `prev_value=0` is enforced, once the att

### Citations

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L764-776)
```text
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
