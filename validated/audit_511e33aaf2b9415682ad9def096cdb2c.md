### Title
Unprivileged Bootstrap Declare Bypass: Missing Cryptographic Access Control Allows Anyone to Declare Classes Without Signature Verification - (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo`)

---

### Summary

The `execute_declare_transaction` function in the StarkNet OS contains a "bootstrap" code path that completely bypasses signature verification, fee charging, and nonce management. The guard condition is a plain felt comparison (`sender_address == 'BOOTSTRAP'`), which any unprivileged user can satisfy by setting their declare transaction's `sender_address` field to the felt encoding of the ASCII string `'BOOTSTRAP'` (`0x424f4f545354524150`). This is directly analogous to the reported `BurnableToken` bug: a function intended for a privileged actor (the sequencer bootstrapper) is callable by anyone because the access control is a publicly-known constant with no cryptographic enforcement.

---

### Finding Description

In `execute_declare_transaction`, after computing the transaction hash and filling `tx_info`, the OS checks:

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

When this branch is taken, the OS:
- **Skips `check_and_increment_nonce`** — the sender's nonce is never verified or incremented.
- **Skips `run_validate` / `__validate_declare__`** — no signature is verified.
- **Skips `charge_fee`** — no fee is deducted.
- **Directly writes `compiled_class_hash` into `contract_class_changes`** — the class is declared as if it were legitimate.

The only guard is the felt comparison `sender_address == 'BOOTSTRAP'`. The felt value of `'BOOTSTRAP'` is `0x424f4f545354524150`, a publicly known constant. Any user can craft a V3 declare transaction with:
- `sender_address = 0x424f4f545354524150`
- `nonce = 0`
- `version = 3`
- All resource bounds set to `0` (so `max_possible_fee == 0`)
- Any `class_hash` and `compiled_class_hash`

The transaction hash is computed deterministically from these fields and checked by `%{ AssertTransactionHash %}`. Since the attacker controls all fields, they can compute the correct hash themselves. No cryptographic secret is required. [2](#0-1) 

The normal declare path (lines 778–827) enforces `check_and_increment_nonce`, `__validate_declare__`, and `charge_fee`, none of which apply to the bootstrap path. [3](#0-2) 

---

### Impact Explanation

**Direct loss of funds (Critical).**

The `dict_update` call enforces `prev_value=0`, meaning a given `class_hash` can only be declared once. An attacker who submits a bootstrap-path declare transaction before a legitimate project can:

1. **Front-run** a legitimate class declaration for a known `class_hash` (e.g., one announced publicly before deployment).
2. Bind that `class_hash` to an **attacker-controlled `compiled_class_hash`** — a malicious CASM implementation.
3. The legitimate project's subsequent declare transaction will fail (the `prev_value=0` constraint is violated).
4. Any user who deploys a contract using that `class_hash` will instantiate the **malicious compiled class**.
5. The malicious class can drain funds from any contract deployed under it.

Additionally, the fee bypass means the attacker pays nothing for the class declaration, removing the economic deterrent.

---

### Likelihood Explanation

The attack requires:
1. The attacker submits a syntactically valid declare transaction with `sender_address = 'BOOTSTRAP'`, `nonce = 0`, `version = 3`, and zero resource bounds.
2. The sequencer includes it. The sequencer's mempool validation may not specifically reject transactions with `sender_address = 'BOOTSTRAP'` from non-bootstrap contexts, since the OS is the intended final arbiter of validity.
3. The OS processes it through the bootstrap path without any cryptographic check.

All three conditions are achievable by an unprivileged user. The felt value `'BOOTSTRAP'` is a public constant. No key material, privileged role, or operator cooperation is needed. The only uncertainty is whether the sequencer's off-chain mempool layer independently rejects such transactions — but the OS itself provides no such guarantee, making this a protocol-level flaw.

---

### Recommendation

Replace the felt-literal comparison with a cryptographically enforced mechanism. Options include:

1. **Remove the bootstrap path entirely** once the network is live, enforcing it only during a one-time genesis block via a separate, audited mechanism outside the general transaction flow.
2. **Require a valid signature from a hardcoded privileged key** (e.g., a Starknet Foundation key) for bootstrap declares, rather than relying on a publicly known address constant.
3. **Restrict the bootstrap path to block number 0** (or a configurable genesis block range), so it cannot be triggered on a live network.

---

### Proof of Concept

An attacker constructs the following declare transaction fields:

```
sender_address  = 0x424f4f545354524150  // felt('BOOTSTRAP')
nonce           = 0
version         = 3
tip             = 0
l1_gas          = ResourceBounds { max_amount: 0, max_price_per_unit: 0 }
l2_gas          = ResourceBounds { max_amount: 0, max_price_per_unit: 0 }
l1_data_gas     = ResourceBounds { max_amount: 0, max_price_per_unit: 0 }
class_hash      = <target class hash to front-run>
compiled_class_hash = <attacker's malicious CASM hash>
```

The attacker computes the Poseidon transaction hash over these fields (matching `compute_declare_transaction_hash`), submits the transaction to the sequencer, and the OS processes it through the bootstrap branch at lines 764–776, writing the malicious `compiled_class_hash` into `contract_class_changes` without any signature check, fee deduction, or nonce increment. [1](#0-0)

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
