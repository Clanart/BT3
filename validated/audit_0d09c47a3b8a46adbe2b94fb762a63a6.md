### Title
Unauthorized Class Declaration via Unauthenticated BOOTSTRAP Path Bypasses Signature and Fee Checks — (`crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo`)

---

### Summary

`execute_declare_transaction` contains a special-case path that completely skips signature verification, nonce enforcement, and fee charging when `sender_address == 'BOOTSTRAP'`, `nonce == 0`, `version == 3`, and all resource bounds are zero. The gate condition is a plain felt comparison against a transaction-supplied field. No cryptographic proof of authorization is required. Any party who can get such a transaction included in a block can declare an arbitrary `class_hash → compiled_class_hash` mapping without owning a deployed account contract, without paying fees, and without providing a valid signature.

---

### Finding Description

In `execute_declare_transaction`, after computing and verifying the transaction hash and verifying the Sierra class hash pre-image, the OS checks:

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

When this branch is taken, the function returns immediately, skipping:

1. **`check_and_increment_nonce`** — nonce validation and increment
2. **`non_reverting_select_execute_entry_point_func`** for `__validate_declare__` — the account contract's signature verification
3. **`charge_fee`** — fee deduction from the account [2](#0-1) 

The `sender_address` value is loaded from the hint `%{ DeclareTxFields %}` and is a field of the transaction submitted by the user. The transaction hash commits to `sender_address`, `compiled_class_hash`, and `class_hash`, but the hash commitment only proves internal consistency — it does not prove that the sender is authorized to use the BOOTSTRAP path. The felt literal `'BOOTSTRAP'` is a fixed numeric value that any user can place in the `sender_address` field of a declare transaction.

The Sierra `class_hash` is verified via `finalize_class_hash` before the BOOTSTRAP branch is reached, so the class hash must be a valid Sierra class hash. However, `compiled_class_hash` (the CASM hash) is **not** verified against `class_hash` anywhere in the OS. The OS simply stores the mapping `class_hash → compiled_class_hash` in `contract_class_changes`. [3](#0-2) 

---

### Impact Explanation

**Direct loss of funds (Critical).**

An attacker who successfully triggers the BOOTSTRAP path can declare a valid Sierra `class_hash` paired with a malicious `compiled_class_hash`. The `dict_update` call uses `prev_value=0`, meaning it succeeds only if the class has not yet been declared. The attacker can therefore:

1. **Front-run a legitimate class declaration**: observe a pending declare transaction for `class_hash=X` with correct `compiled_class_hash=Z`, and race to declare `class_hash=X` with a malicious `compiled_class_hash=Y`. The legitimate declaration then fails (prev_value is no longer 0), and all subsequent contract deployments using `class_hash=X` execute the malicious CASM.
2. **Declare a new class with a malicious CASM**: declare any previously undeclared `class_hash` with a malicious `compiled_class_hash`. Any user who later deploys a contract using that class hash will execute the attacker's CASM, which can drain the contract's funds.

The `validate_compiled_class_facts_post_execution` call at the end of OS execution validates only the CASM facts that were *used during execution*, not the correctness of the `class_hash → compiled_class_hash` mapping stored in state. So a maliciously declared mapping passes post-execution validation. [4](#0-3) 

---

### Likelihood Explanation

The attacker-controlled entry path is a standard declare transaction with four field values set:
- `sender_address = 'BOOTSTRAP'` (the felt encoding of the ASCII string)
- `nonce = 0`
- `version = 3`
- all resource bounds `max_amount = 0` (so `max_possible_fee = 0`)

The OS Cairo code imposes no further restriction. Whether such a transaction reaches the OS depends on the sequencer's gateway validation. If the gateway does not enforce that `sender_address` corresponds to a deployed account contract (or does not block the felt value `'BOOTSTRAP'` specifically), the transaction passes through. In a decentralized sequencer environment, a sequencer node controlled by the attacker can include the transaction directly. The OS proof would be valid, and the L1 verifier would accept the resulting state transition.

---

### Recommendation

Replace the plain felt comparison with a cryptographically enforced authorization mechanism. Options include:

1. **Remove the BOOTSTRAP path entirely** from the production OS and handle bootstrapping through a separate, off-chain mechanism or a genesis block with special handling outside the normal transaction flow.
2. **Bind the BOOTSTRAP privilege to a specific key**: require the BOOTSTRAP declare transaction to carry a valid signature from a designated bootstrapping key (e.g., one of the `public_keys` already present in `OsGlobalContext`), and verify that signature inside the OS before entering the bypass path.
3. **Restrict by block number**: allow the BOOTSTRAP path only in block 0 or below a configurable threshold block number, enforced by an `assert` in the OS. [5](#0-4) 

---

### Proof of Concept

**Attacker constructs a declare transaction with:**
```
sender_address  = felt('BOOTSTRAP')   // 0x424f4f545354524150
nonce           = 0
version         = 3
tip             = 0
resource_bounds = [
    { token: L1_GAS,      max_amount: 0, max_price: 0 },
    { token: L2_GAS,      max_amount: 0, max_price: 0 },
    { token: L1_DATA_GAS, max_amount: 0, max_price: 0 },
]
class_hash      = <any valid Sierra class hash>
compiled_class_hash = <hash of attacker-controlled CASM>
```

**OS execution path:**

1. `execute_declare_transaction` is called.
2. `compute_declare_transaction_hash` computes a hash committing to all fields above. `%{ AssertTransactionHash %}` passes (the sequencer provides the matching hash).
3. `finalize_class_hash` verifies `class_hash` is a valid Sierra class hash — passes if the attacker provides a valid Sierra class.
4. The condition `sender_address == 'BOOTSTRAP' and tx_info.nonce == 0 and tx_info.version == 3` evaluates to true.
5. `compute_max_possible_fee` returns 0 (all bounds are zero).
6. `dict_update` stores `class_hash → attacker_compiled_class_hash` in `contract_class_changes`.
7. `%{ SkipTx %}` and `return ()` — no `__validate_declare__`, no nonce increment, no fee.

**Result:** The state now maps `class_hash` to the attacker's malicious CASM hash. Any contract subsequently deployed with that `class_hash` executes the attacker's CASM, enabling arbitrary fund extraction. [6](#0-5)

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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L778-827)
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

    return ();
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/os.cairo (L114-120)
```text
    // Validate the guessed compile class facts.
    let compiled_class_facts_bundle = os_global_context.compiled_class_facts_bundle;
    validate_compiled_class_facts_post_execution(
        n_compiled_class_facts=compiled_class_facts_bundle.n_compiled_class_facts,
        compiled_class_facts=compiled_class_facts_bundle.compiled_class_facts,
        builtin_costs=compiled_class_facts_bundle.builtin_costs,
    );
```
