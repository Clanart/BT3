### Title
Unauthenticated Bootstrap Declare Path Bypasses Signature Verification and Fee Enforcement, Enabling Unauthorized Class Declaration — (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo`)

---

### Summary

`execute_declare_transaction` contains a special "bootstrap" path that skips signature verification, nonce enforcement, and fee charging for any declare transaction where `sender_address == 'BOOTSTRAP'`, `nonce == 0`, `version == 3`, and `max_fee == 0`. The guard is a plain felt-literal equality check — not a cryptographic access control. Any unprivileged user can craft a transaction satisfying these conditions and have it included in a block, allowing them to declare arbitrary class hashes without owning the `'BOOTSTRAP'` account, without a valid signature, and without paying fees.

---

### Finding Description

In `execute_declare_transaction`, after computing the transaction hash, the OS checks:

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

When this branch is taken, the OS returns immediately, skipping:

1. **`check_and_increment_nonce`** — no nonce enforcement
2. **`run_validate`** — no `__validate_declare__` entry point is called, so no signature verification occurs
3. **`charge_fee`** — no fee is deducted [2](#0-1) 

The guard `sender_address == 'BOOTSTRAP'` is a comparison against the felt encoding of the ASCII string `"BOOTSTRAP"`. This is not a cryptographic check. There is no deployed contract at that address, no key pair, and no signature required. Any user can set `sender_address` to this felt value in a declare transaction.

`compute_max_possible_fee` returns 0 when all three resource bound `max_amount` fields are zero — a valid V3 transaction format: [3](#0-2) 

The `sender_address` field is loaded from the hint variable `tx` via `%{ DeclareTxFields %}` and is directly user-controlled: [4](#0-3) 

---

### Impact Explanation

The `dict_update` call enforces `prev_value=0`, meaning a class hash can only be declared once: [5](#0-4) 

An attacker who observes a pending legitimate declare transaction (class hash `X`, correct `compiled_class_hash C`) can front-run it by submitting a bootstrap declare transaction for the same class hash `X` with an arbitrary non-zero garbage `compiled_class_hash G ≠ C`. Once included in a block, the OS records `class_hash_changes[X] = G`. All subsequent legitimate declare attempts for `X` will fail the `prev_value=0` constraint, permanently and irrecoverably.

Any contract whose deployment or upgrade depends on class hash `X` with compiled class hash `C` can never be instantiated. Funds held in contracts that require upgrading to class `X`, or funds in contracts whose constructor depends on deploying a sub-contract of class `X`, are permanently frozen. This satisfies **Critical — Permanent freezing of funds**.

---

### Likelihood Explanation

The attack is straightforward:
- The attacker monitors the public mempool for pending declare transactions.
- They craft a V3 declare transaction with `sender_address = felt('BOOTSTRAP')`, `nonce = 0`, all resource bound `max_amount = 0`, and the target class hash with a garbage `compiled_class_hash`.
- The transaction is valid at the OS level and will be proven correctly.
- No privileged key, no leaked secret, and no malicious operator is required — only the ability to submit a transaction, which is available to any network participant.

The only sequencer-side friction is that fee-less transactions may be deprioritized in the mempool. However, since the bootstrap path is an intentional OS feature, a sequencer following the protocol spec would include such a transaction as valid. The OS proof will accept it unconditionally.

---

### Recommendation

Replace the felt-literal equality check with a proper cryptographic access control mechanism. Options include:

1. **Remove the bootstrap path entirely** and use a privileged sequencer-signed initialization transaction that is verified against a known public key committed in the OS config.
2. **Bind the bootstrap privilege to the `public_keys_hash`** already present in `StarknetOsConfig` — require the bootstrap declare transaction to carry a valid signature verifiable against one of the registered public keys.
3. **Restrict bootstrap declares to block number 0** (genesis block only), enforced by a Cairo assertion against `block_context.block_info_for_execute.block_number`. [6](#0-5) 

---

### Proof of Concept

1. Attacker observes a pending declare transaction for class hash `X` with correct compiled class hash `C` in the mempool.
2. Attacker constructs a declare transaction:
   - `sender_address = felt_from_ascii("BOOTSTRAP")` (the felt literal `'BOOTSTRAP'`)
   - `nonce = 0`
   - `version = 3`
   - `class_hash = X`
   - `compiled_class_hash = 1` (any non-zero garbage value)
   - All resource bounds: `max_amount = 0`, `max_price_per_unit = 0`
3. The OS computes the transaction hash over these fields and enters `execute_declare_transaction`.
4. The condition at line 764 evaluates to true; `compute_max_possible_fee` returns 0.
5. The OS executes `dict_update(key=X, prev_value=0, new_value=1)` and returns — no signature checked, no fee charged.
6. The legitimate declare transaction for `X` with `compiled_class_hash = C` is now permanently blocked: `dict_update(key=X, prev_value=0, new_value=C)` will fail because `prev_value` is now `1`, not `0`.
7. All contracts depending on class `X` with compiled class `C` can never be deployed; any funds contingent on those contracts are permanently frozen.

### Citations

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L87-102)
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
