### Title
Missing Declared-Class Validation in `execute_replace_class` Allows Permanent Contract Bricking - (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

### Summary

The `execute_replace_class` syscall handler in the StarkNet OS does not verify that the new class hash supplied by a contract corresponds to a previously declared class. The OS unconditionally writes the caller-supplied class hash into the contract state. If the hash is undeclared, every subsequent call to that contract will fail to resolve a class, permanently freezing any funds held by it.

### Finding Description

In `syscall_impls.cairo`, the `execute_replace_class` function processes the `replace_class` syscall:

```cairo
// Replaces the class.
func execute_replace_class{...}(contract_address: felt) {
    ...
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
    ...
}
``` [1](#0-0) 

The developer-acknowledged TODO at line 898 explicitly states the missing check: `// TODO(Yoni, 1/1/2026): Check that there is a declared contract class with the given hash.` [2](#0-1) 

By contrast, `execute_declare_transaction` correctly enforces that a class hash must be backed by a valid Sierra class pre-image before it is written into `contract_class_changes`:

```cairo
let expected_class_hash = finalize_class_hash(...);
with_attr error_message("Invalid class hash pre-image.") {
    assert [class_hash_ptr] = expected_class_hash;
}
...
dict_update{dict_ptr=contract_class_changes}(
    key=[class_hash_ptr], prev_value=0, new_value=compiled_class_hash
);
``` [3](#0-2) 

`execute_replace_class` performs no equivalent lookup against `contract_class_changes` (the dict tracking declared classes) before committing the new class hash to `contract_state_changes`. The two dicts are entirely separate: `contract_class_changes` tracks declared class hashes, while `contract_state_changes` tracks per-contract state (including `class_hash`). Writing an arbitrary value into the latter without cross-referencing the former breaks the invariant that every contract's `class_hash` must point to a declared class. [4](#0-3) 

### Impact Explanation

Once a contract's `class_hash` field is set to an undeclared hash, the OS cannot resolve the class for any subsequent entry-point call. Every future transaction targeting that contract will fail at class resolution, making the contract permanently inoperable. All tokens or assets held in that contract's storage become permanently frozen. This matches the **Critical — Permanent freezing of funds** impact category.

### Likelihood Explanation

The `replace_class` syscall is the standard StarkNet upgrade primitive, analogous to `upgradeToAndCall()` in EVM UUPS proxies. Any contract that exposes an upgrade path (a common pattern) is exposed. Realistic triggering scenarios include:

1. **Accidental:** A contract owner calls their upgrade function with a class hash that was not yet declared (e.g., declared in a later transaction, or a typo in the hash). The OS silently accepts it.
2. **Adversarial:** A contract whose upgrade function lacks strict access control can be called by any user with an arbitrary (undeclared) class hash, permanently bricking the contract and freezing its funds.

Because `replace_class` is a standard, widely-used syscall and the missing validation is explicitly noted as a known TODO, the likelihood of accidental or deliberate triggering is high.

### Recommendation

Before committing the new class hash to `contract_state_changes`, `execute_replace_class` must verify that the supplied `class_hash` exists in `contract_class_changes` (i.e., has been declared in the current block or a prior block). Concretely:

1. Perform a `dict_read` on `contract_class_changes` with `key=class_hash`.
2. Assert the returned `compiled_class_hash` is non-zero (indicating a prior declaration).
3. Only then proceed with the `dict_update` on `contract_state_changes`.

This mirrors the invariant already enforced by `execute_declare_transaction`, which uses `prev_value=0` to guarantee a class is declared at most once and always with a valid pre-image.

### Proof of Concept

1. Deploy contract `A` holding funds, whose class implements a `upgrade(new_class_hash)` function that calls `replace_class(new_class_hash)`.
2. Call `upgrade(0xdeadbeef)` where `0xdeadbeef` is never declared via a `declare` transaction.
3. The OS executes `execute_replace_class`, skips the missing validation, and writes `class_hash=0xdeadbeef` into `contract_state_changes` for contract `A`.
4. The block is proven and finalized with this state transition accepted.
5. Any subsequent invoke transaction targeting contract `A` fails: the OS reads `class_hash=0xdeadbeef` from state, finds no entry in the class tree, and cannot execute any entry point.
6. All funds in contract `A` are permanently frozen.

### Citations

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo (L877-916)
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
}
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L738-819)
```text
        let expected_class_hash = finalize_class_hash(
            contract_class_component_hashes=contract_class_component_hashes
        );
        with_attr error_message("Invalid class hash pre-image.") {
            assert [class_hash_ptr] = expected_class_hash;
        }
    }
    update_poseidon_in_builtin_ptrs(poseidon_ptr=poseidon_ptr);

    // Get the account transaction info.
    let (tx_info: TxInfo*) = alloc();
    let (deprecated_tx_info: DeprecatedTxInfo*) = alloc();
    fill_account_tx_info(
        transaction_hash=transaction_hash,
        common_tx_fields=common_tx_fields,
        account_deployment_data_size=account_deployment_data_size,
        account_deployment_data=account_deployment_data,
        proof_facts_size=0,
        proof_facts=cast(0, felt*),
        tx_info_dst=tx_info,
        deprecated_tx_info_dst=deprecated_tx_info,
    );

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
```
