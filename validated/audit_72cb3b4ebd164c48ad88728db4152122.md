I found a significant issue. Let me read the key section more carefully before writing the report.

### Title
Unprivileged Bootstrap Bypass in `execute_declare_transaction` Allows Fee-Free, Signature-Free Class Declaration - (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo`)

---

### Summary

`execute_declare_transaction` contains a special-case path intended for sequencer bootstrapping. Any unprivileged user can satisfy its conditions by crafting a declare transaction with `sender_address = 'BOOTSTRAP'`, `nonce = 0`, `version = 3`, and all resource bounds set to zero. This causes the OS to skip signature verification (`__validate_declare__`), nonce enforcement, and fee charging entirely, and to write an arbitrary `compiled_class_hash` for any valid Sierra class hash into the protocol state.

---

### Finding Description

Inside `execute_declare_transaction`, after computing the transaction hash and filling `tx_info`, the following branch executes:

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

The string literal `'BOOTSTRAP'` is a plain Cairo felt (the ASCII encoding of the word). There is no privileged role, deployed contract, or cryptographic secret associated with it. Any transaction sender can set their `sender_address` field to this felt value.

`compute_max_possible_fee` returns zero when all three resource-bound `max_amount` fields are zero:

```cairo
return l1_gas_bounds.max_amount * l1_gas_bounds.max_price_per_unit + l2_gas_bounds.max_amount *
    (l2_gas_bounds.max_price_per_unit + tx_info.tip) + l1_data_gas_bounds.max_amount *
    l1_data_gas_bounds.max_price_per_unit;
``` [2](#0-1) 

When both conditions are met, the function returns immediately after writing to `contract_class_changes`, skipping:

1. **`check_and_increment_nonce`** — nonce replay protection is not enforced.
2. **`non_reverting_select_execute_entry_point_func` with `VALIDATE_DECLARE_ENTRY_POINT_SELECTOR`** — the account's `__validate_declare__` entry point (signature check) is never called.
3. **`charge_fee`** — no ERC-20 transfer is executed; the sequencer receives nothing. [3](#0-2) 

The only remaining guard is `assert_not_zero(compiled_class_hash)` and the `prev_value=0` constraint in `dict_update`, which enforces that a class hash can only be declared once. [4](#0-3) 

---

### Impact Explanation

**Critical — Direct loss of funds and permanent freezing of funds.**

**Fee bypass (direct loss of funds):** Every bootstrap-bypass declare transaction is processed by the OS and included in a proven block without any fee being charged. The sequencer bears the computational cost of proving the transaction while receiving zero compensation. An attacker can spam arbitrarily many such transactions (each for a distinct, previously-undeclared class hash) to drain sequencer resources at zero cost.

**Class-declaration griefing (permanent freezing of funds):** The `prev_value=0` constraint means each class hash can be declared exactly once. An attacker who observes a pending legitimate declare transaction for class hash `C` can front-run it by submitting a bootstrap-bypass declare for `C` with an arbitrary (e.g., incorrect or malicious) `compiled_class_hash`. Once accepted into a proven block, the legitimate declare will always fail (`prev_value` is no longer 0). Any deployed contracts whose upgrade or initialization path depends on class `C` being declared with the correct CASM hash are permanently broken — their funds are frozen with no recovery path.

---

### Likelihood Explanation

**High.** The conditions are entirely under attacker control:

- `sender_address = 'BOOTSTRAP'` — a known felt constant, no key or deployment required.
- `nonce = 0` — freely chosen.
- `version = 3` — freely chosen.
- All resource bounds `max_amount = 0` — freely chosen.

The transaction hash is computed from these fields and verified by `%{ AssertTransactionHash %}`, but the attacker constructs the transaction themselves, so the hash they submit will match. No privileged access, leaked key, or operator cooperation is needed. The attack is executable by any user who can submit a transaction to the sequencer.

---

### Recommendation

Remove the bootstrap special-case from the production OS path entirely. If bootstrapping is a genuine operational need, it must be gated on a cryptographic check tied to a privileged key (e.g., a sequencer-operator signature over the class hash and compiled class hash), not on a plain felt comparison against a publicly known constant. Alternatively, the bootstrap path should be handled off-chain before the OS is invoked, so it never appears as a user-submittable transaction type inside the proven execution.

---

### Proof of Concept

1. Attacker identifies a Sierra class `C` with a valid component-hash pre-image (required by `finalize_class_hash`).
2. Attacker constructs a v3 declare transaction with:
   - `sender_address = 0x424f4f545354524150` (felt encoding of `'BOOTSTRAP'`)
   - `nonce = 0`
   - `version = 3`
   - `l1_gas.max_amount = 0`, `l2_gas.max_amount = 0`, `l1_data_gas.max_amount = 0`
   - `class_hash = C`, `compiled_class_hash = <attacker-chosen value>`
3. Attacker submits the transaction. The sequencer includes it in a block.
4. Inside `execute_declare_transaction`, the branch at line 764 is entered; `compute_max_possible_fee` returns 0; the function writes `(C → attacker_compiled_class_hash)` into `contract_class_changes` and returns — no signature check, no nonce check, no fee.
5. The block is proven. Class `C` is now permanently registered with the wrong CASM hash. Any subsequent legitimate declare of `C` fails the `prev_value=0` assertion. Contracts depending on `C` are permanently frozen. [5](#0-4)

### Citations

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L99-101)
```text
    return l1_gas_bounds.max_amount * l1_gas_bounds.max_price_per_unit + l2_gas_bounds.max_amount *
        (l2_gas_bounds.max_price_per_unit + tx_info.tip) + l1_data_gas_bounds.max_amount *
        l1_data_gas_bounds.max_price_per_unit;
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
