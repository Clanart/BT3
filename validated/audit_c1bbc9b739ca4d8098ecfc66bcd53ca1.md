### Title
Missing Signature Verification Guard in Bootstrap Declare Path Allows Unauthorized Class Declaration - (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo`)

### Summary

`execute_declare_transaction` contains a special "bootstrap" code path that unconditionally skips `run_validate` (the signature-verification step). Any unprivileged transaction sender can craft a declare transaction satisfying the four felt-level conditions and have the OS accept it without any cryptographic authorization, permanently writing an attacker-controlled `compiled_class_hash` into the class registry.

### Finding Description

In `execute_declare_transaction`, after the transaction hash is computed and the Sierra class hash is verified, the following branch is evaluated:

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

When this branch is taken the function returns immediately, bypassing:

- `check_and_increment_nonce` (line 779) — nonce replay protection
- The `validate_declare_execution_context` setup and `non_reverting_select_execute_entry_point_func` call (lines 782–812) — **the `__validate_declare__` entry point that performs signature verification**
- `charge_fee` (line 822) [2](#0-1) 

The four conditions that gate this path are pure felt comparisons:

| Condition | Value |
|---|---|
| `sender_address` | felt `'BOOTSTRAP'` = `0x424f4f545354524150` |
| `nonce` | `0` |
| `version` | `3` |
| all resource bounds | `0` (so `max_possible_fee == 0`) |

None of these conditions require knowledge of a private key or any privileged secret. The transaction hash is computed correctly from these fields (so `%{ AssertTransactionHash %}` passes), but because `run_validate` is never called, the OS never invokes the account contract's `__validate_declare__` entry point and never checks a signature.

The normal validation path for comparison: [3](#0-2) 

The `run_validate` function that is skipped: [4](#0-3) 

### Impact Explanation

The `dict_update` call in the bootstrap path writes `compiled_class_hash` into `contract_class_changes` with `prev_value=0`, meaning:

1. **Each class hash can be declared exactly once** (the `prev_value=0` constraint prevents re-declaration).
2. An attacker who submits a bootstrap declare transaction **before** a legitimate protocol transaction can permanently bind a target `class_hash` to an attacker-chosen `compiled_class_hash`.

Any contract that subsequently calls `replace_class` to upgrade to that `class_hash` will execute the attacker's CASM. Because `replace_class` is used by upgradeable contracts that hold user funds, this leads to **direct, permanent loss of funds** — matching the Critical impact tier. [5](#0-4) 

### Likelihood Explanation

The four triggering conditions require no privileged access, no leaked key, and no operator cooperation. Any user who can submit a transaction to the sequencer's mempool can craft the required fields. The OS Cairo program is the authoritative verifier; if the sequencer's gateway does not independently enforce signature checks for this specific felt-address, the malicious transaction reaches the OS and is accepted. The attack is a straightforward front-run of any publicly observable class declaration.

### Recommendation

Add a call to `run_validate` (or an equivalent signature-verification step) inside the bootstrap branch before writing to `contract_class_changes`, or remove the bootstrap path entirely and replace it with an explicit privileged-operator mechanism that is enforced at the OS level (e.g., checking against a known sequencer public key stored in `os_global_context`).

```diff
 if (sender_address == 'BOOTSTRAP' and tx_info.nonce == 0 and tx_info.version == 3) {
     let max_possible_fee = compute_max_possible_fee(tx_info=tx_info);
     if (max_possible_fee == 0) {
+        // Verify that the bootstrap sender authorized this declaration.
+        run_validate(block_context=block_context, tx_execution_context=bootstrap_execution_context);
         assert_not_zero(compiled_class_hash);
         dict_update{dict_ptr=contract_class_changes}(
             key=[class_hash_ptr], prev_value=0, new_value=compiled_class_hash
         );
         %{ SkipTx %}
         return ();
     }
 }
```

### Proof of Concept

1. Attacker observes that protocol P is about to declare Sierra class `C` (class hash `H`) with legitimate CASM hash `L`.
2. Attacker crafts a declare transaction:
   - `sender_address = 'BOOTSTRAP'` (felt `0x424f4f545354524150`)
   - `nonce = 0`, `version = 3`, all resource bounds = `0`
   - `class_hash = H` (same target class hash)
   - `compiled_class_hash = M` (attacker's malicious CASM hash)
   - Sierra class pre-image for `H` is valid (passes `finalize_class_hash`)
3. Attacker submits the transaction; sequencer includes it before P's transaction.
4. OS executes the bootstrap branch: `run_validate` is never called; `contract_class_changes[H] = M` is written.
5. P's legitimate declare transaction fails (`prev_value=0` constraint violated — slot already occupied).
6. Any contract that later calls `replace_class(H)` executes attacker CASM `M`, draining its funds. [1](#0-0)

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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L779-825)
```text
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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo (L877-915)
```text
// Replaces the class.
func execute_replace_class{
    range_check_ptr,
    syscall_ptr: felt*,
    contract_state_changes: DictAccess*,
    revert_log: RevertLogEntry*,
}(contract_address: felt) {
    alloc_locals;
    let request = cast(syscall_ptr + RequestHeader.SIZE, ReplaceClassRequest*);

    // Reduce gas.
    let success = reduce_syscall_gas_and_write_response_header(
        total_gas_cost=REPLACE_CLASS_GAS_COST, request_struct_size=ReplaceClassRequest.SIZE
    );
    if (success == FALSE) {
        // Not enough gas to execute the syscall.
        return ();
    }

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
