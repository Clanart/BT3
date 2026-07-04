### Title
Insufficient `compiled_class_hash` Verification in `execute_declare_transaction` Allows Registration of Invalid Class Mappings — (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo`)

---

### Summary

In `execute_declare_transaction`, the `compiled_class_hash` (CASM hash) supplied by the transaction sender is only checked to be non-zero before being permanently written into the `contract_class_changes` state dictionary. There is no Cairo constraint verifying that `compiled_class_hash` corresponds to any validated compiled class fact. This is the direct analog of the external report's pattern: a value is checked only for non-zero-ness instead of being verified against the expected/correct value.

---

### Finding Description

In `execute_declare_transaction`, the OS guesses `compiled_class_hash` from a hint (`%{ DeclareTxFields %}`), includes it in the transaction hash computation, and then stores it in the state with only one Cairo-level constraint:

```cairo
// Declare the class hash.
// Note that prev_value=0 enforces that a class may be declared only once.
assert_not_zero(compiled_class_hash);
dict_update{dict_ptr=contract_class_changes}(
    key=[class_hash_ptr], prev_value=0, new_value=compiled_class_hash
);
``` [1](#0-0) 

The Sierra `class_hash` **is** properly verified — the OS recomputes it from the contract class components and asserts equality:

```cairo
let expected_class_hash = finalize_class_hash(
    contract_class_component_hashes=contract_class_component_hashes
);
with_attr error_message("Invalid class hash pre-image.") {
    assert [class_hash_ptr] = expected_class_hash;
}
``` [2](#0-1) 

But `compiled_class_hash` (the CASM hash) receives no equivalent verification. The `validate_compiled_class_facts` function does verify that each loaded compiled class fact's hash matches the actual compiled class bytecode:

```cairo
assert compiled_class_fact.hash = hash;
``` [3](#0-2) 

However, there is **no Cairo constraint** linking the `compiled_class_hash` written into `contract_class_changes` by `execute_declare_transaction` to any entry in the validated `compiled_class_facts`. The two checks are entirely disconnected at the constraint level.

The same pattern appears in the BOOTSTRAP fast-path:

```cairo
assert_not_zero(compiled_class_hash);
dict_update{dict_ptr=contract_class_changes}(
    key=[class_hash_ptr], prev_value=0, new_value=compiled_class_hash
);
``` [4](#0-3) 

---

### Impact Explanation

Because `dict_update` uses `prev_value=0`, a class can be declared **only once**. Once a `class_hash → compiled_class_hash` mapping is written with an invalid (but non-zero) `compiled_class_hash`, it is **permanent and irrevocable**. Any contract subsequently deployed under that `class_hash` will be permanently non-executable — the OS will be unable to locate a validated compiled class for the stored hash. Any funds (ETH, ERC-20 tokens) sent to such a deployed contract are **permanently frozen**, satisfying the Critical: Permanent freezing of funds impact.

---

### Likelihood Explanation

Any account holder on StarkNet can submit a `Declare v3` transaction. The user signs a transaction hash that commits to `compiled_class_hash` (via `compute_declare_transaction_hash`), so the user fully controls what value is committed. The OS enforces only `assert_not_zero`. A user can deliberately (or a buggy/malicious sequencer can cause) a declare transaction with `compiled_class_hash` set to an arbitrary non-zero felt that does not correspond to any real compiled class. The sequencer's off-chain validation is not a protocol-level guarantee; the Cairo OS program is the authoritative constraint layer. [5](#0-4) 

---

### Recommendation

After computing `compiled_class_hash` from the transaction fields, the OS should verify that it matches the hash of an actual validated compiled class fact — analogous to how `class_hash` is verified against `finalize_class_hash`. Concretely, a lookup into the validated `compiled_class_facts` should be performed and a Cairo `assert` should enforce:

```cairo
assert compiled_class_fact.hash = compiled_class_hash;
```

This mirrors the fix described in the external report: ensure the hash in the request parameters matches the hash in the on-chain/verified data structure, not merely that it is non-zero.

---

### Proof of Concept

1. Attacker holds a StarkNet account and constructs a `Declare v3` transaction where:
   - `class_hash` = valid Sierra class hash (passes `finalize_class_hash` check)
   - `compiled_class_hash` = `1` (non-zero, but not the hash of any real compiled class)
2. Attacker signs the transaction; the transaction hash commits to `compiled_class_hash = 1`.
3. The account's `__validate_declare__` verifies the signature and returns `VALIDATED`.
4. The OS executes `assert_not_zero(1)` — passes.
5. `dict_update` writes `class_hash → 1` into `contract_class_changes` permanently.
6. A victim deploys a contract under `class_hash`. The OS stores the contract with CASM hash `1`.
7. Any call to the deployed contract causes the OS to look up compiled class with hash `1`; no such validated fact exists. Execution permanently fails.
8. Any ETH or tokens sent to the deployed contract address are permanently frozen. [6](#0-5)

### Citations

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L693-828)
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
}
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/contract_class/compiled_class.cairo (L131-131)
```text
    assert compiled_class_fact.hash = hash;
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/transaction_hash/transaction_hash.cairo (L264-291)
```text
func compute_declare_transaction_hash{range_check_ptr, poseidon_ptr: PoseidonBuiltin*}(
    common_fields: CommonTxFields*,
    class_hash: felt,
    compiled_class_hash: felt,
    account_deployment_data_size: felt,
    account_deployment_data: felt*,
) -> felt {
    alloc_locals;

    // TODO(Noa, 01/01/2026): remove the following `assert` once the field is supported.
    assert account_deployment_data_size = 0;
    with_attr error_message("Invalid transaction version: {version}.") {
        assert common_fields.version = 3;
    }

    let hash_state: PoseidonHashState = poseidon_hash_init();
    with hash_state {
        hash_tx_common_fields(common_fields=common_fields);
        poseidon_hash_update_with_nested_hash(
            data_ptr=account_deployment_data, data_length=account_deployment_data_size
        );
        // Add the class hash to the hash state.
        poseidon_hash_update_single(item=class_hash);
        poseidon_hash_update_single(item=compiled_class_hash);
    }
    let transaction_hash = poseidon_hash_finalize(hash_state=hash_state);

    return transaction_hash;
```
