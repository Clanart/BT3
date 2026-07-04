### Title
Bootstrap Declare Path Bypasses `__validate_declare__` Signature Verification — (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo`)

---

### Summary

`execute_declare_transaction` contains a special "BOOTSTRAP" branch that unconditionally skips the account's `__validate_declare__` entry point (signature verification), nonce enforcement, and fee charging. The four conditions that gate this branch are all fields of the user-submitted declare transaction, making the bypass reachable by an unprivileged class declarer.

---

### Finding Description

In `execute_declare_transaction`, after computing the transaction hash and filling `tx_info`, the following branch is evaluated:

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

When this branch is taken, the function returns immediately — before reaching:

- `check_and_increment_nonce` (line 779) — nonce replay protection is skipped
- `run_validate` / `non_reverting_select_execute_entry_point_func` with `VALIDATE_DECLARE_ENTRY_POINT_SELECTOR` (lines 800–812) — the account's `__validate_declare__` is never called, so **no signature is verified**
- `charge_fee` (line 822) — fee is not collected [2](#0-1) 

All four gating conditions are fields of the declare transaction itself, loaded from the hint `%{ DeclareTxFields %}`:

| Condition | Source |
|---|---|
| `sender_address == 'BOOTSTRAP'` | `sender_address` field of the tx |
| `tx_info.nonce == 0` | `nonce` field of the tx |
| `tx_info.version == 3` | `version` field of the tx |
| `max_possible_fee == 0` | all three `resource_bounds` set to zero | [3](#0-2) 

The `sender_address` is not validated against any on-chain state in this branch; the OS simply checks the felt equality `sender_address == 'BOOTSTRAP'` (felt `0x424f4f545354524150`). No privileged key or operator role is required to produce a transaction carrying this felt as the sender address.

The only structural guard that remains is the Sierra class-hash pre-image check (`finalize_class_hash`) and the post-execution compiled-class-facts validation (`validate_compiled_class_facts_post_execution`). These verify that the declared class is a well-formed Sierra class, but they do **not** verify that the submitter is authorized to declare it. [4](#0-3) 

---

### Impact Explanation

An attacker who can submit a declare transaction satisfying the four conditions can register an arbitrary (but structurally valid) Sierra class hash → compiled class hash mapping in `contract_class_changes` without any account authorization. Because `prev_value=0` is enforced, the attack targets class hashes not yet declared.

Once a class hash is declared this way, any contract that calls the `replace_class` syscall to upgrade to that class hash will execute the attacker-supplied CASM. This is a direct path to **loss of funds** for any contract that performs a class replacement based on an off-chain governance signal or automated upgrade mechanism, because the on-chain class hash appears legitimately declared.

This matches the allowed impact: **Critical — Direct loss of funds**.

---

### Likelihood Explanation

The four conditions are entirely within the transaction fields a user submits. The felt value `'BOOTSTRAP'` is not a reserved or unaddressable value; it is `0x424f4f545354524150`, a normal field element. A user can craft a v3 declare transaction with:

- `sender_address = 0x424f4f545354524150`
- `nonce = 0`
- `version = 3`
- all `resource_bounds.max_amount = 0` and `resource_bounds.max_price_per_unit = 0`

The OS Cairo program itself imposes no further restriction. Gateway-level filtering is a separate layer not enforced by the OS proof, and the OS is the authoritative validity check in the proof system. Any block that includes such a transaction and is proven will have the class hash accepted as legitimately declared.

---

### Recommendation

1. **Remove or gate the BOOTSTRAP path behind a verifiable sequencer identity.** If bootstrapping is necessary, require a signature from a known sequencer public key (already available in `os_global_context.starknet_os_config.public_keys_hash`) rather than a plain felt equality check on `sender_address`.
2. **At minimum, verify that the sender_address has a deployed contract** (non-zero `class_hash` in `contract_state_changes`) before entering the BOOTSTRAP branch, consistent with how all other declare paths behave.
3. **Require `__validate_declare__` to always run** for any class declaration that modifies `contract_class_changes`, mirroring the pattern used for normal declare transactions.

---

### Proof of Concept

1. Construct a valid Sierra contract class `C` with class hash `H` and compiled class hash `CH`.
2. Submit a v3 declare transaction with:
   - `sender_address = 0x424f4f545354524150` (`'BOOTSTRAP'`)
   - `nonce = 0`
   - `version = 3`
   - `resource_bounds` for L1_GAS, L2_GAS, L1_DATA_GAS all with `max_amount = 0` and `max_price_per_unit = 0`
   - `class_hash = H`, `compiled_class_hash = CH`
3. The sequencer includes this transaction in a block.
4. The OS evaluates the BOOTSTRAP branch at line 764, finds all conditions satisfied, calls `dict_update` to register `H → CH` in `contract_class_changes`, and returns — **without ever calling `__validate_declare__`**.
5. The class `H` is now declared on-chain. Any contract that subsequently calls `replace_class(H)` will execute the attacker-supplied CASM without the original declarer having authorized the declaration. [1](#0-0) [5](#0-4)

### Citations

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L710-720)
```text
    local sender_address;
    local class_hash_ptr: felt*;
    local compiled_class_hash;
    local account_deployment_data_size;
    local account_deployment_data: felt*;
    %{ DeclareTxFields %}
    let common_tx_fields = get_account_tx_common_fields(
        block_context=block_context,
        tx_hash_prefix=DECLARE_HASH_PREFIX,
        sender_address=sender_address,
    );
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L738-743)
```text
        let expected_class_hash = finalize_class_hash(
            contract_class_component_hashes=contract_class_component_hashes
        );
        with_attr error_message("Invalid class hash pre-image.") {
            assert [class_hash_ptr] = expected_class_hash;
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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/execute_transaction_utils.cairo (L116-158)
```text
func run_validate{
    range_check_ptr,
    remaining_gas: felt,
    builtin_ptrs: BuiltinPointers*,
    contract_state_changes: DictAccess*,
    contract_class_changes: DictAccess*,
    outputs: OsCarriedOutputs*,
}(block_context: BlockContext*, tx_execution_context: ExecutionContext*) {
    alloc_locals;
    local tx_execution_info: ExecutionInfo* = tx_execution_context.execution_info;

    // Do not run "__validate__" for version 0.
    if (tx_execution_info.tx_info.version == 0) {
        return ();
    }

    // "__validate__" is expected to get the same calldata as "__execute__".
    local validate_execution_context: ExecutionContext* = new ExecutionContext(
        entry_point_type=ENTRY_POINT_TYPE_EXTERNAL,
        class_hash=tx_execution_context.class_hash,
        calldata_size=tx_execution_context.calldata_size,
        calldata=tx_execution_context.calldata,
        execution_info=new ExecutionInfo(
            block_info=block_context.block_info_for_validate,
            tx_info=tx_execution_info.tx_info,
            caller_address=tx_execution_info.caller_address,
            contract_address=tx_execution_info.contract_address,
            selector=VALIDATE_ENTRY_POINT_SELECTOR,
        ),
        deprecated_tx_info=tx_execution_context.deprecated_tx_info,
    );

    // The __validate__ function should not revert.
    let (retdata_size, retdata, is_deprecated) = non_reverting_select_execute_entry_point_func(
        block_context=block_context, execution_context=validate_execution_context
    );
    if (is_deprecated == 0) {
        %{ CheckRetdataForDebug %}
        assert retdata_size = 1;
        assert retdata[0] = VALIDATED;
    }

    return ();
```
