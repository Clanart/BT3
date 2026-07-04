Looking at the scope directory, I need to find an analog to the "initialization order / authorization bypass" vulnerability class — specifically, code that performs privileged state writes while bypassing the normal authorization/registration checks.

The most relevant finding is in `execute_declare_transaction` in `transaction_impls.cairo`.

---

### Title
Unauthorized Class Declaration via Unauthenticated BOOTSTRAP Path Allows Poisoning Class Hashes — (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo`)

---

### Summary

`execute_declare_transaction` contains a special "BOOTSTRAP" branch that completely bypasses signature verification, nonce enforcement, and fee payment. Any unprivileged user can trigger it by submitting a v3 declare transaction with `sender_address = 'BOOTSTRAP'`, `nonce = 0`, and all resource bounds set to zero. This lets an attacker declare any valid Sierra class hash with an **arbitrary** `compiled_class_hash`, poisoning the class tree entry before a legitimate declaration can occur.

---

### Finding Description

In `execute_declare_transaction`, after computing and verifying the transaction hash, the following branch is evaluated:

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

When this branch is taken, the function returns **immediately**, skipping:

- `check_and_increment_nonce` — no nonce enforcement
- `run_validate` — no `__validate_declare__` signature check
- `charge_fee` — no fee payment [2](#0-1) 

The three guard conditions (`sender_address == 'BOOTSTRAP'`, `nonce == 0`, `version == 3`, `max_possible_fee == 0`) are **entirely user-controlled** field elements in the transaction. `'BOOTSTRAP'` is the felt encoding of the ASCII string; no contract needs to be deployed at that address because the signature check is the very thing being skipped. Any user can craft a transaction satisfying all three conditions.

The Sierra class hash is still verified via `finalize_class_hash`: [3](#0-2) 

So the attacker must supply a valid Sierra class pre-image — but the `compiled_class_hash` stored in the class tree is **not** verified against any actual compiled class. The attacker can supply any non-zero felt as `compiled_class_hash`.

The `dict_update` with `prev_value=0` enforces that each class hash can only be declared once: [4](#0-3) 

This means a poisoned entry permanently occupies the slot — a subsequent legitimate declare for the same class hash will fail because `prev_value` is no longer `0`.

---

### Impact Explanation

**High — Network not being able to confirm new transactions.**

The class tree maps `class_hash → compiled_class_hash`. When the OS executes any contract, it resolves the `compiled_class_hash` from this tree and the sequencer must supply the matching compiled class. If the stored `compiled_class_hash` is an arbitrary value that corresponds to no real compiled class, every execution attempt for a contract of that class will fail.

Attack path to network halt:

1. A network upgrade is planned that declares class hash `H` for a critical system contract (e.g., the fee token or the account class used by all accounts).
2. The Sierra class for `H` is public (e.g., open-source or visible in a pending transaction).
3. The attacker submits a BOOTSTRAP declare for `H` with an arbitrary `compiled_class_hash C'` (one that has no corresponding compiled class).
4. The attacker's transaction is included first; `H → C'` is written to the class tree.
5. The legitimate declare for `H` fails (`prev_value` is no longer `0`).
6. Any contract upgraded to class `H` (via `replace_class`) or deployed with class `H` will fail to execute, because the sequencer cannot supply a compiled class matching `C'`.
7. If the fee token or a universal account class is affected, fee charging or account validation fails for all transactions, halting the network.

Note also that `execute_replace_class` explicitly does **not** check whether the new class hash is declared: [5](#0-4) 

This means a contract can call `replace_class(H)` even after `H` has been poisoned, completing the attack without any additional authorization barrier.

---

### Likelihood Explanation

**Medium.** The attacker needs to:
1. Know the class hash `H` before it is legitimately declared — feasible if the Sierra source is public or the pending transaction is observable in the mempool.
2. Have their transaction included before the legitimate one — feasible because the OS imposes no ordering restriction on BOOTSTRAP transactions; a sequencer following OS rules must accept them.

The zero-fee requirement means a sequencer may deprioritize the attacker's transaction, but this is a sequencer-level policy, not an OS-level enforcement. The OS itself accepts the transaction unconditionally when the three conditions are met.

---

### Recommendation

Remove the BOOTSTRAP special-case from the OS entirely, or gate it behind a block-number or genesis-only check enforced in Cairo (not just in hints). Initialization logic that requires writing to the class tree without a valid account signature should be handled through a separate, explicitly privileged mechanism (e.g., a system transaction type with its own entry in the OS that is only valid at block 0), analogous to the report's recommendation to place constructor logic in an `initialise()` function called after registration.

---

### Proof of Concept

A malicious actor constructs and submits the following declare transaction (pseudocode):

```
DeclareTransaction {
    version: 3,
    sender_address: felt('BOOTSTRAP'),   // = 0x424f4f545354524150
    nonce: 0,
    resource_bounds: { l1_gas: (0,0), l2_gas: (0,0), l1_data_gas: (0,0) },
    tip: 0,
    class_hash: H,                        // valid Sierra class hash (pre-image provided)
    compiled_class_hash: ARBITRARY_FELT,  // any non-zero felt, no real compiled class
    signature: [],                        // empty — never checked
}
```

The OS evaluates:
- `sender_address == 'BOOTSTRAP'` ✓
- `tx_info.nonce == 0` ✓
- `tx_info.version == 3` ✓
- `compute_max_possible_fee(tx_info) == 0` ✓ (all bounds zero)

The BOOTSTRAP branch is taken. `run_validate` is never called. `dict_update(key=H, prev_value=0, new_value=ARBITRARY_FELT)` is executed. Class hash `H` is now permanently poisoned in the class tree. Any subsequent legitimate declare for `H` fails with a dict-update mismatch. Any contract executing under class `H` fails because no compiled class matches `ARBITRARY_FELT`.

### Citations

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L735-744)
```text
        local contract_class_component_hashes: ContractClassComponentHashes*;
        %{ SetComponentHashes %}

        let expected_class_hash = finalize_class_hash(
            contract_class_component_hashes=contract_class_component_hashes
        );
        with_attr error_message("Invalid class hash pre-image.") {
            assert [class_hash_ptr] = expected_class_hash;
        }
    }
```

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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo (L896-915)
```text
    let class_hash = request.class_hash;

    // TODO(Yoni, 1/1/2026): Check that there is a declared contract class with the given hash.
    local state_entry: StateEntry*;
    %{ GetContractAddressStateEntry %}

    tempvar new_state_entry = new StateEntry(
        class_hash=class_hash, storage_ptr=state_entry.storage_ptr, nonce=state_entry.nonce
    );

    dict_update{dict_ptr=contract_state_changes}(
        key=contract_address,
        prev_value=cast(state_entry, felt),
        new_value=cast(new_state_entry, felt),
    );

    assert [revert_log] = RevertLogEntry(selector=CHANGE_CLASS_ENTRY, value=state_entry.class_hash);
    let revert_log = &revert_log[1];

    return ();
```
