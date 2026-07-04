### Title
Bootstrap Declare Path Bypasses Signature Verification and Nonce Enforcement — (`File: crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo`)

---

### Summary

In `execute_declare_transaction`, a special "bootstrap" code path skips both `run_validate` (signature verification) and `check_and_increment_nonce` (replay protection) when `sender_address == 'BOOTSTRAP'`, `nonce == 0`, `version == 3`, and `max_fee == 0`. The only authorization check is a plain felt-value comparison against the ASCII encoding of the string `'BOOTSTRAP'`. Any unprivileged transaction sender can craft a declare transaction satisfying these conditions and have the OS accept it — declaring arbitrary class hashes into the protocol state without any cryptographic proof of identity.

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

The branch returns **before** reaching either `check_and_increment_nonce` or the `run_validate` call that executes the account contract's `__validate_declare__` entry point. [2](#0-1) 

The normal (non-bootstrap) declare flow reads the sender's on-chain state entry and calls `non_reverting_select_execute_entry_point_func` with `VALIDATE_DECLARE_ENTRY_POINT_SELECTOR`, which enforces that the account contract cryptographically approves the declaration. The bootstrap path entirely omits this step.

`run_validate` in `execute_transaction_utils.cairo` is the function responsible for calling `__validate__` / `__validate_declare__`: [3](#0-2) 

The authorization guard is solely `sender_address == 'BOOTSTRAP'`. In Cairo, `'BOOTSTRAP'` is the felt value `0x424F4F545354524150` — a plain integer. There is no PDA, no cryptographic commitment, and no on-chain contract existence check for this address in the bootstrap path. Any transaction sender can set `sender_address` to this felt value in a declare transaction.

Additionally, because `check_and_increment_nonce` is never called in the bootstrap path, the nonce stored in the account state is never incremented. The condition `nonce == 0` is checked against the **transaction field**, not the on-chain state nonce. This means the same bootstrap path can be triggered in every block indefinitely (once per new class hash, since `prev_value=0` prevents re-declaration of the same hash). [4](#0-3) 

---

### Impact Explanation

An attacker who can submit a declare transaction to the sequencer with:
- `sender_address = 0x424F4F545354524150` (`'BOOTSTRAP'`)
- `nonce = 0`
- `version = 3`
- `max_fee = 0` (all resource bounds set to zero)

will have the OS accept the declaration of an **arbitrary** `compiled_class_hash` without any signature. The attacker can then:

1. Declare a malicious Sierra class whose `__validate__` always returns `VALIDATED` and whose `__execute__` drains caller balances or re-routes fee transfers.
2. Deploy contracts under that class hash.
3. Lure or front-run users into interacting with those contracts, resulting in **direct loss of funds**.

Because the nonce is never incremented, the attacker can repeat this across multiple blocks to declare multiple malicious classes. Each declared class becomes a permanent, proven part of the L2 state committed to L1.

**Allowed impact matched**: Critical — Direct loss of funds.

---

### Likelihood Explanation

The conditions are entirely attacker-controlled fields in a standard declare transaction. No privileged role, leaked key, or operator cooperation is required. The sequencer's mempool is the only off-chain barrier; a sequencer that is itself compromised, bribed, or running a modified client would include such a transaction. The OS Cairo code — which is what the proof verifies — imposes no cryptographic barrier. Likelihood is **Medium** (requires sequencer inclusion, but the OS itself provides no defense).

---

### Recommendation

1. **Remove or gate the bootstrap path behind a verifiable on-chain mechanism.** If bootstrapping is genuinely needed, require the transaction to be signed by a well-known, hardcoded public key whose signature is verified inside the OS (analogous to requiring a `dtf_program_signer` PDA in the Solana report).
2. **Always call `check_and_increment_nonce`** before any early return in `execute_declare_transaction`, so that even bootstrap transactions consume a nonce and cannot be replayed.
3. **Always call `run_validate`** (or an equivalent signature check) for every declare transaction, including the bootstrap path. If the bootstrap address has no deployed contract, the OS should reject the transaction rather than silently skip validation.

---

### Proof of Concept

1. Attacker constructs a declare transaction with:
   - `sender_address = 0x424F4F545354524150` (felt encoding of `'BOOTSTRAP'`)
   - `nonce = 0`
   - `version = 3`
   - All resource bounds set to zero (so `max_possible_fee == 0`)
   - `class_hash` pointing to a malicious Sierra class
   - `compiled_class_hash` = hash of the malicious CASM

2. The OS computes the transaction hash correctly (it includes `sender_address`), so `%{ AssertTransactionHash %}` passes — this is a hint, not a proof constraint.

3. The OS reaches the bootstrap branch at line 764 of `transaction_impls.cairo`. Both inner conditions (`sender_address == 'BOOTSTRAP'` and `max_possible_fee == 0`) are satisfied.

4. `dict_update` writes `compiled_class_hash` into `contract_class_changes` with `prev_value=0`. The function returns immediately — **no `run_validate`, no `check_and_increment_nonce`**.

5. The block is proven. The malicious class hash is now part of the committed L2 state on L1.

6. Attacker deploys a contract under the malicious class hash and drains funds from users who interact with it.

7. Because the nonce in the `'BOOTSTRAP'` account state was never incremented, the attacker repeats steps 1–6 in the next block with a different `class_hash`, declaring additional malicious classes. [5](#0-4)

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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L778-806)
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
