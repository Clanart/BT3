### Title
Missing Signature Verification in Bootstrap Declare Path Allows Unauthorized Class Declaration — (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo`)

### Summary
`execute_declare_transaction` contains a special "bootstrap" code path that unconditionally skips the account signature verification step (`__validate_declare__`), the nonce check, and fee payment when `sender_address == 'BOOTSTRAP'`, `nonce == 0`, `version == 3`, and `max_possible_fee == 0`. Any unprivileged user can craft a declare transaction satisfying these conditions and have an arbitrary Sierra class accepted into the protocol state without providing any valid signature.

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

When this branch is taken, the function returns immediately — bypassing:

1. `check_and_increment_nonce` — no nonce enforcement
2. `run_validate` — no call to `__validate_declare__`, which is the sole entry point responsible for signature verification
3. `charge_fee` — no fee deduction [2](#0-1) 

The normal declare path (lines 778–827) does call `run_validate` → `non_reverting_select_execute_entry_point_func` with `VALIDATE_DECLARE_ENTRY_POINT_SELECTOR`, which enforces the account contract's signature check. [3](#0-2) 

The condition `sender_address == 'BOOTSTRAP'` is a plain felt comparison against the ASCII encoding of the string `'BOOTSTRAP'` (felt value `0x424F4F545354524150`). In StarkNet, `sender_address` is a user-supplied field element in the declare transaction. There is no Cairo-enforced constraint that it must be a hash-derived contract address. An attacker can freely set this field to the felt value of `'BOOTSTRAP'` when constructing a declare transaction.

The `compiled_class_hash` in this path is loaded from hints and only checked to be non-zero — there is no verification that it corresponds to the declared `class_hash`. [4](#0-3) 

### Impact Explanation

An attacker can declare any valid Sierra class into the protocol's class registry without owning any account contract and without providing a valid signature. Concretely:

1. The attacker constructs a Sierra class whose `__validate__` and `__validate_deploy__` entry points unconditionally return `VALIDATED`.
2. The attacker submits a declare transaction with `sender_address = 'BOOTSTRAP'`, `nonce = 0`, `version = 3`, and all resource bounds set to zero. The OS accepts this through the bootstrap path, writing `class_hash → compiled_class_hash` into `contract_class_changes` without any signature check.
3. The attacker then submits a `deploy_account` transaction for a contract of this class. Because `__validate_deploy__` always returns `VALIDATED`, the deploy succeeds regardless of the signature provided.
4. The attacker now controls an account contract that accepts any (or no) signature for all future `__validate__` calls, enabling them to execute arbitrary transactions from that account — including draining ERC-20 balances or interacting with DeFi protocols to steal funds.

This constitutes **direct loss of funds** reachable from an unprivileged transaction sender.

### Likelihood Explanation

The triggering conditions are entirely attacker-controlled at transaction construction time: `sender_address` is a user-supplied felt, `nonce = 0` is the default for a fresh address, `version = 3` is the current transaction version, and setting all resource bounds to zero makes `max_possible_fee = 0`. No privileged access, leaked key, or operator cooperation is required. The attacker only needs to submit a well-formed declare transaction to the sequencer's mempool.

### Recommendation

Remove the bootstrap path entirely, or gate it behind a sequencer-level access control that is enforced in Cairo (not just in hints). At minimum, the bootstrap path must call `run_validate` to enforce signature verification before writing to `contract_class_changes`. If the bootstrap path is intended only for genesis/system initialization, it should be guarded by a block-number or a cryptographic commitment to an authorized bootstrap key that is verified in Cairo code.

### Proof of Concept

```
1. Construct a Sierra class C with:
     __validate__(...) -> (felt,) { return ('VALID',); }
     __validate_deploy__(...) -> (felt,) { return ('VALID',); }
   Compute class_hash(C) = H and compiled_class_hash(C) = K.

2. Submit declare transaction:
     sender_address  = 0x424F4F545354524150  // felt('BOOTSTRAP')
     nonce           = 0
     version         = 3
     resource_bounds = {l1_gas: 0, l2_gas: 0, l1_data_gas: 0}
     class_hash      = H
     compiled_class_hash = K
     signature       = []   // empty — never checked

   The OS enters the bootstrap branch, skips run_validate, and writes
   contract_class_changes[H] = K.

3. Submit deploy_account transaction for class H at any salt.
   __validate_deploy__ returns VALIDATED unconditionally.
   Account is deployed at address A.

4. From account A, submit invoke transactions with empty signatures.
   __validate__ returns VALIDATED unconditionally.
   Attacker can call any contract (e.g., transfer ERC-20 tokens to themselves).
``` [5](#0-4)

### Citations

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L693-776)
```text
func execute_declare_transaction{
    range_check_ptr,
    builtin_ptrs: BuiltinPointers*,
    contract_state_changes: DictAccess*,
    contract_class_changes: DictAccess*,
    outputs: OsCarriedOutputs*,
}(block_context: BlockContext*) {
    alloc_locals;

    local tx_version;
    %{ TxVersion %}
    if (tx_version == 0) {
        %{ SkipTx %}
        return ();
    }

    // Guess transaction fields.
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

    let poseidon_ptr = builtin_ptrs.selectable.poseidon;
    with poseidon_ptr {
        // Compute transaction hash.
        let transaction_hash = compute_declare_transaction_hash(
            common_fields=common_tx_fields,
            class_hash=[class_hash_ptr],
            compiled_class_hash=compiled_class_hash,
            account_deployment_data_size=account_deployment_data_size,
            account_deployment_data=account_deployment_data,
        );
        %{ AssertTransactionHash %}

        // Ensure the given class hash is a result of a Sierra class hash calculation.
        local contract_class_component_hashes: ContractClassComponentHashes*;
        %{ SetComponentHashes %}

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
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L799-812)
```text
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
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/execute_transaction_utils.cairo (L110-158)
```text
// Runs the account contract's "__validate__" entry point, which is responsible for
// signature verification.
//
// Arguments:
// block_context - a global context that is fixed throughout the block.
// tx_execution_context - The execution context of the underlying invoke transaction.
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
