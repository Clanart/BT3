### Title
Unrestricted BOOTSTRAP Path in `execute_declare_transaction` Bypasses Signature Verification, Nonce Check, and Fee Payment — (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo`)

---

### Summary

`execute_declare_transaction` contains a special "bootstrap" code path that completely skips `__validate_declare__` (signature verification), `check_and_increment_nonce`, and `charge_fee`. The sole guard is the felt comparison `sender_address == 'BOOTSTRAP'`. Because any unprivileged user can set `sender_address` to the felt encoding of the ASCII string `"BOOTSTRAP"` in a declare transaction, the guard never actually restricts access. This is the direct StarkNet analog of the reported vulnerability: a security check that is supposed to prevent a privileged operation is placed at the wrong layer and is always satisfiable, allowing the operation to be performed for free.

---

### Finding Description

In `execute_declare_transaction`, after computing the transaction hash and filling `tx_info`, the OS evaluates:

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

When this branch is taken, the function returns immediately — skipping:

1. **`check_and_increment_nonce`** (line 779) — no nonce verification or increment.
2. **`non_reverting_select_execute_entry_point_func` for `__validate_declare__`** (line 804) — no signature check.
3. **`charge_fee`** (line 822) — no fee deducted. [2](#0-1) 

The four conditions an attacker must satisfy are all trivially controllable:

| Condition | Attacker control |
|---|---|
| `sender_address == 'BOOTSTRAP'` | Set `sender_address` to felt `0x424F4F545354524150` in the transaction |
| `tx_info.nonce == 0` | Set nonce field to 0 (no on-chain check in this path) |
| `tx_info.version == 3` | `get_account_tx_common_fields` hardcodes version=3 |
| `max_possible_fee == 0` | Set all `max_amount` or `max_price_per_unit` fields to 0 | [3](#0-2) 

The `compute_max_possible_fee` function confirms that zero resource bounds yield a zero fee: [4](#0-3) 

The `check_and_increment_nonce` function, which would normally enforce nonce ordering, is never reached in this path: [5](#0-4) 

**Structural parallel to the external report:** In the reported EVM bug, the check `msg.flags & EVMC_DELEGATED` was placed at the host layer but `msg.flags` was always `0` at the VM layer — so the check never fired. Here, the check `sender_address == 'BOOTSTRAP'` is placed at the OS layer but the felt value `'BOOTSTRAP'` is freely settable by any transaction sender — so the check never actually restricts access.

---

### Impact Explanation

An unprivileged attacker can declare an unlimited number of class hashes with **zero fee payment and no signature**:

- Each declaration writes to `contract_class_changes` and is committed to the proven state.
- The `prev_value=0` constraint only prevents re-declaring the same hash; the attacker can generate arbitrarily many distinct valid Sierra classes.
- Because no fee is charged and no nonce is consumed, the attacker can submit these transactions at negligible cost, filling block capacity with fee-free state-changing transactions.
- Legitimate fee-paying transactions are crowded out, resulting in **network inability to confirm new transactions** — matching the "High: Network not being able to confirm new transactions (total network shutdown)" impact category.

Additionally, the complete absence of signature verification means **any class hash can be declared by anyone**, bypassing the account-contract authorization model that protects class declaration in all other paths.

---

### Likelihood Explanation

The attack requires only:
1. Constructing a syntactically valid declare transaction with `sender_address = 0x424F4F545354524150` (`'BOOTSTRAP'`), `nonce = 0`, `version = 3`, and all resource bounds zeroed.
2. Submitting it to the sequencer.

No cryptographic material, privileged access, or special tooling is needed. The felt value `'BOOTSTRAP'` is a public constant derivable from the source code. Any user with access to the StarkNet transaction submission API can execute this attack.

---

### Recommendation

1. **Remove the BOOTSTRAP path entirely** if it is no longer needed for production operation.
2. If it must be retained for genesis/bootstrap scenarios, restrict it to a **specific block number range** (e.g., only block 0) enforced by a Cairo constraint against `block_context.block_info_for_execute.block_number`, not a felt string comparison.
3. At minimum, add a constraint that the `sender_address` must correspond to a contract with a non-zero class hash in `contract_state_changes`, preventing use of an undeployed address.

---

### Proof of Concept

```
1. Compute felt('BOOTSTRAP') = 0x424F4F545354524150

2. Construct a StarkNet Declare v3 transaction:
   - sender_address  = 0x424F4F545354524150
   - nonce           = 0
   - version         = 3
   - l1_gas_bounds   = { resource: L1_GAS,      max_amount: 0, max_price_per_unit: 0 }
   - l2_gas_bounds   = { resource: L2_GAS,      max_amount: 0, max_price_per_unit: 0 }
   - l1_data_bounds  = { resource: L1_DATA_GAS, max_amount: 0, max_price_per_unit: 0 }
   - class_hash      = <any valid Sierra class hash>
   - compiled_class_hash = <corresponding compiled class hash, non-zero>
   - signature       = [] (empty — never checked in this path)

3. Submit to sequencer. The OS executes execute_declare_transaction:
   - Transaction hash is computed and verified (consistent with the crafted fields).
   - The BOOTSTRAP branch is entered (all four conditions satisfied).
   - dict_update writes compiled_class_hash into contract_class_changes.
   - check_and_increment_nonce, __validate_declare__, and charge_fee are all skipped.
   - Class is declared with zero fee and no authorization.

4. Repeat with different class content to declare additional hashes for free,
   consuming block space without paying fees.
```

### Citations

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L87-102)
```text
func compute_max_possible_fee(tx_info: TxInfo*) -> felt {
    tempvar resource_bounds: ResourceBounds* = tx_info.resource_bounds_start;
    let n_resource_bounds = (tx_info.resource_bounds_end - resource_bounds) / ResourceBounds.SIZE;

    // Only V3 transactions with all resource bounds are supported.
    assert tx_info.version = 3;
    assert n_resource_bounds = 3;

    tempvar l1_gas_bounds: ResourceBounds = resource_bounds[L1_GAS_INDEX];
    tempvar l2_gas_bounds: ResourceBounds = resource_bounds[L2_GAS_INDEX];
    tempvar l1_data_gas_bounds = resource_bounds[L1_DATA_GAS_INDEX];

    return l1_gas_bounds.max_amount * l1_gas_bounds.max_price_per_unit + l2_gas_bounds.max_amount *
        (l2_gas_bounds.max_price_per_unit + tx_info.tip) + l1_data_gas_bounds.max_amount *
        l1_data_gas_bounds.max_price_per_unit;
}
```

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
