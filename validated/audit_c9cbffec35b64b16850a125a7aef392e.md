### Title
Unauthorized Bootstrap Declare Path Bypasses Signature Validation, Nonce Check, and Fee Payment — (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo`)

---

### Summary

`execute_declare_transaction` contains a special "bootstrap" code path that completely skips signature validation (`run_validate`), nonce increment (`check_and_increment_nonce`), and fee charging (`charge_fee`). The only guard is a felt-equality check against the literal `'BOOTSTRAP'`. Because `'BOOTSTRAP'` is a publicly known felt value and no cryptographic proof of ownership is required, any unprivileged transaction sender can trigger this path and declare arbitrary class hashes for free, with no authorization.

---

### Finding Description

Inside `execute_declare_transaction`, before the normal validation flow, the OS checks:

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

When all four conditions are met, the function returns immediately after writing to `contract_class_changes`, skipping:

- `check_and_increment_nonce` (line 779) — nonce stays at 0 permanently for this sender
- `run_validate` (line 804) — the account's `__validate_declare__` entry point is never called; no signature is verified
- `charge_fee` (line 822) — no fee is deducted [2](#0-1) 

The conditions an attacker must satisfy are entirely under their control:

| Field | Required Value | Attacker Control |
|---|---|---|
| `sender_address` | `felt('BOOTSTRAP')` | Freely chosen in tx fields |
| `nonce` | `0` | Freely chosen |
| `version` | `3` | Freely chosen |
| All resource bounds | `max_amount * max_price = 0` | Freely chosen |

`'BOOTSTRAP'` in Cairo is a felt literal (ASCII encoding of the string). It is a fixed, publicly known integer. There is no deployed account contract at that address, no private key, and no signature required. The OS proof system enforces the felt equality but does not enforce that the sender owns or controls that address.

Because `check_and_increment_nonce` is skipped, the nonce for `sender_address = 'BOOTSTRAP'` remains `0` after each bootstrap declare. An attacker can submit an unbounded sequence of declare transactions across blocks, each with `nonce = 0`, each declaring a different class hash, each paying zero fees. [3](#0-2) 

---

### Impact Explanation

**High — Network not being able to confirm new transactions (total network shutdown).**

Because class declarations through this path cost zero fees and consume no nonce, an attacker can flood the sequencer with an unlimited stream of zero-cost declare transactions. Each transaction is provably valid from the OS's perspective (the proof verifies correctly). Block space is consumed by these free declarations, crowding out legitimate fee-paying transactions. A sustained attack can prevent the network from confirming new user transactions, constituting a total network shutdown.

A secondary impact is **direct loss of funds**: every legitimate declare transaction pays fees to the sequencer/fee token contract. The bootstrap path allows an attacker to declare classes for free, depriving the protocol of fee revenue that would otherwise be collected.

---

### Likelihood Explanation

Likelihood is **high**:

- No secret knowledge, private key, or privileged access is required.
- The felt value of `'BOOTSTRAP'` is a compile-time constant derivable from the public source code.
- The attacker only needs to craft a standard declare transaction with four specific field values.
- The attack is repeatable across every block because the nonce is never incremented.
- A malicious or compromised sequencer can include these transactions; the OS proof will accept them as valid.

---

### Recommendation

Remove the bootstrap path entirely, or protect it with a cryptographic access control mechanism:

1. **Remove the path**: If bootstrapping is no longer needed (the system is live), delete the `if (sender_address == 'BOOTSTRAP' ...)` block.
2. **Enforce signature validation**: If the path must exist, do not skip `run_validate`. Require the sender to prove ownership of the `'BOOTSTRAP'` address via a valid signature, just like any other declare transaction.
3. **Enforce nonce increment**: Never skip `check_and_increment_nonce`. Even in a bootstrap scenario, the nonce must advance to prevent replay.
4. **Use a privileged address with a known key**: If a bootstrap address is needed, derive it from a key held by a trusted party and enforce signature validation.

---

### Proof of Concept

An attacker constructs and submits the following declare transaction to the sequencer:

```
sender_address  = 0x424f4f545354524150  // felt('BOOTSTRAP')
version         = 3
nonce           = 0
resource_bounds = [
    { token: L1_GAS,      max_amount: 0, max_price_per_unit: 0 },
    { token: L2_GAS,      max_amount: 0, max_price_per_unit: 0 },
    { token: L1_DATA_GAS, max_amount: 0, max_price_per_unit: 0 },
]
class_hash         = <any valid Sierra class hash>
compiled_class_hash = <corresponding CASM hash>
signature          = []  // empty — never checked
```

The OS executes `execute_declare_transaction`:

1. `compute_declare_transaction_hash` computes a valid hash from the above fields. [4](#0-3) 
2. The bootstrap condition `sender_address == 'BOOTSTRAP' and nonce == 0 and version == 3` evaluates to `TRUE`. [5](#0-4) 
3. `compute_max_possible_fee` returns `0` because all resource bounds are zero. [6](#0-5) 
4. `dict_update` writes `class_hash → compiled_class_hash` into `contract_class_changes`. [7](#0-6) 
5. The function returns. No signature checked. No nonce incremented. No fee paid.

The attacker repeats this with a different `class_hash` in the next block (nonce is still 0). The OS generates a valid proof for each block. The network fills with zero-cost class declarations, blocking legitimate transactions.

### Citations

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L87-101)
```text
func compute_max_possible_fee(tx_info: TxInfo*) -> felt {
    tempvar resource_bounds: ResourceBounds* = tx_info.resource_bounds_start;
    let n_resource_bounds = (tx_info.resource_bounds_end - resource_bounds) / ResourceBounds.SIZE;

    // Only V3 transactions with all resource bounds are supported.
    assert tx_info.version = 3;
    assert n_resource_bounds = 3;

    tempvar l1_gas_bounds: ResourceBounds = resource_bounds[L1_GAS_INDEX];
    tempvar l2_gas_bounds: ResourceBounds = resource_bounds[L2_GAS_INDEX];
    tempvar l1_data_gas_bounds = resource_bounds[L1_DATA_GAS_INDEX];

    return l1_gas_bounds.max_amount * l1_gas_bounds.max_price_per_unit + l2_gas_bounds.max_amount *
        (l2_gas_bounds.max_price_per_unit + tx_info.tip) + l1_data_gas_bounds.max_amount *
        l1_data_gas_bounds.max_price_per_unit;
```

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
