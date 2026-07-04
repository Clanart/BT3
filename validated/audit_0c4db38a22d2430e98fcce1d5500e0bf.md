### Title
Unauthenticated BOOTSTRAP Bypass in `execute_declare_transaction` Skips Signature Verification, Nonce Enforcement, and Fee Payment — (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo`)

---

### Summary

The `execute_declare_transaction` function contains a hardcoded bypass path triggered when `sender_address == 'BOOTSTRAP'`, `nonce == 0`, `version == 3`, and `max_possible_fee == 0`. When all four conditions are satisfied, the OS skips `run_validate` (signature verification), `check_and_increment_nonce` (replay protection), and `charge_fee` (fee payment), and directly commits a class hash to state. Because `sender_address` is an attacker-supplied felt loaded from transaction data, any unprivileged declare transaction sender can craft these conditions. The OS program — which is the protocol ground truth — accepts the resulting state transition as valid, and a proof over it will pass the verifier.

---

### Finding Description

In `execute_declare_transaction` (`transaction_impls.cairo`, lines 764–776):

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
```

`'BOOTSTRAP'` is a Cairo felt literal — the ASCII encoding of the string "BOOTSTRAP" — not a privileged system address protected by any key or role. It is not a deployed contract address with enforced ownership.

`sender_address` is loaded from the transaction payload via the hint `%{ DeclareTxFields %}` at line 715, which reads directly from the user-submitted transaction. An attacker sets it to the felt value of `'BOOTSTRAP'`.

The three bypassed security steps are:

1. **`run_validate`** (`execute_transaction_utils.cairo`, line 116) — calls the account contract's `__validate_declare__` entry point, which performs signature verification. Skipping this means no cryptographic proof of ownership is required.
2. **`check_and_increment_nonce`** (`execute_transaction_utils.cairo`, line 63) — verifies the transaction nonce matches the account's on-chain nonce and increments it. Skipping this means the nonce is never incremented, so the same `nonce=0` condition can be reused across multiple declare transactions for different class hashes.
3. **`charge_fee`** (`transaction_impls.cairo`, line 111) — executes an ERC-20 transfer from the sender to the sequencer. Skipping this means no fee token is transferred.

The only remaining constraint is `prev_value=0` in `dict_update`, which prevents re-declaring an already-declared class hash. This does not prevent declaring new, previously-unseen class hashes.

The Sierra class hash validity check (`finalize_class_hash`, line 738–743) runs before the bypass and is not skipped — so only valid Sierra class hashes can be declared. However, an attacker controls the Sierra class content and can declare any valid Sierra program they choose.

---

### Impact Explanation

**Direct loss of funds (Critical).**

The fee payment step (`charge_fee`) transfers ERC-20 fee tokens (STRK/ETH) from the sender's account to the sequencer. By bypassing this step, the attacker declares classes without transferring any fee tokens. The fee tokens that the protocol mandates must be paid are never deducted. This constitutes a direct loss of funds: the protocol's fee accounting is violated, and the attacker retains tokens that the protocol requires them to spend.

Additionally, because `check_and_increment_nonce` is skipped, the BOOTSTRAP account's nonce is never incremented. The attacker can repeat this for an unbounded number of distinct class hashes, each time with `nonce=0`, declaring an unlimited number of classes for free. Each declaration is a separate fee-free state write accepted by the verifier.

---

### Likelihood Explanation

**Medium.** The attacker submits a standard declare transaction with four specific field values: `sender_address = 'BOOTSTRAP'` (a known felt constant), `nonce = 0`, `version = 3`, and all resource bounds set to zero. No privileged access, leaked key, or operator collusion is required. The OS program — not the sequencer's mempool — is the protocol enforcement layer. A proof generated over a block containing such a transaction is cryptographically valid and will be accepted by the L1 verifier. A sequencer (honest or malicious) that includes such a transaction produces a valid proof. Sequencer-level mempool filtering is not a protocol guarantee and is outside the OS program's enforcement scope.

---

### Recommendation

Remove the `'BOOTSTRAP'` bypass entirely, or gate it behind a verifiable on-chain condition (e.g., a governance-controlled flag in the OS config, or a check that the block number is below a specific bootstrapping threshold). If bootstrapping is a legitimate operational need, it must be enforced through a mechanism that is part of the verifiable state — not a hardcoded felt string comparison on an attacker-supplied field.

---

### Proof of Concept

1. Compute the felt encoding of `'BOOTSTRAP'`: this is the ASCII bytes of "BOOTSTRAP" packed into a single felt (`0x424f4f545354524150`).
2. Prepare a valid Sierra contract class and compute its class hash via the standard Sierra hashing procedure (satisfying the `finalize_class_hash` check at line 738).
3. Submit a declare transaction to the sequencer with:
   - `sender_address = 0x424f4f545354524150` (`'BOOTSTRAP'`)
   - `nonce = 0`
   - `version = 3`
   - `L1_GAS.max_amount = 0`, `L1_GAS.max_price_per_unit = 0`
   - `L2_GAS.max_amount = 0`, `L2_GAS.max_price_per_unit = 0`
   - `L1_DATA_GAS.max_amount = 0`, `L1_DATA_GAS.max_price_per_unit = 0`
   - `compiled_class_hash` = the compiled class hash of the Sierra class
4. The OS program evaluates the bypass condition at line 764: all four conditions are true.
5. `compute_max_possible_fee` returns 0 (all bounds are zero).
6. The OS executes `dict_update` to write `class_hash → compiled_class_hash` into `contract_class_changes` and returns — no signature check, no nonce increment, no fee transfer.
7. The resulting state transition is included in the block output. A STARK proof over this execution is valid and accepted by the L1 verifier.
8. Repeat with a different class hash and `nonce=0` again — the nonce was never incremented, so the bypass fires again. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5)

### Citations

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L111-125)
```text
func charge_fee{
    range_check_ptr,
    builtin_ptrs: BuiltinPointers*,
    contract_state_changes: DictAccess*,
    contract_class_changes: DictAccess*,
    outputs: OsCarriedOutputs*,
}(block_context: BlockContext*, tx_execution_context: ExecutionContext*) {
    alloc_locals;

    local tx_info: TxInfo* = tx_execution_context.execution_info.tx_info;
    let max_fee = compute_max_possible_fee(tx_info=tx_info);

    if (max_fee == 0) {
        return ();
    }
```

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
