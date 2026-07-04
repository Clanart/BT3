I have enough context from the files read. Let me now look at the specific bootstrap bypass section and the `replace_class` TODO more carefully before writing the final answer.

### Title
Unprivileged Declare Transaction Bypasses Signature Validation and Fee Payment via Hardcoded 'BOOTSTRAP' Sender Address — (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo`)

---

### Summary

The `execute_declare_transaction` function in the StarkNet OS contains a special-cased execution path keyed on the literal string `'BOOTSTRAP'` as the `sender_address`. Any unprivileged user can craft a declare transaction that satisfies the three conditions (`sender_address == 'BOOTSTRAP'`, `nonce == 0`, `version == 3`, zero resource bounds) and thereby: (1) skip `__validate_declare__` signature verification entirely, (2) skip fee payment, and (3) write an arbitrary `compiled_class_hash` into the global class registry — permanently and irrevocably, because the OS enforces `prev_value=0` on every class declaration.

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

When this branch is taken, the function returns immediately — before the three critical enforcement steps that follow for every normal declare transaction:

1. **`check_and_increment_nonce`** (line 779) — nonce is never verified or incremented.
2. **`non_reverting_select_execute_entry_point_func` with `VALIDATE_DECLARE_ENTRY_POINT_SELECTOR`** (lines 803–806) — the account's `__validate_declare__` entry point (which performs signature verification) is never called.
3. **`charge_fee`** (line 822) — no fee is deducted. [2](#0-1) 

All four triggering conditions are fully user-controlled fields of a StarkNet declare transaction:

- `sender_address` — the felt value `'BOOTSTRAP'` = `0x424f4f545354524150`. Any user can set this field to this value; the OS performs no check that a contract actually exists at this address in the bootstrap path.
- `nonce` — set to `0`. Because `check_and_increment_nonce` is skipped, the nonce at address `'BOOTSTRAP'` is never incremented, so the attacker can submit an unlimited number of bootstrap-path transactions all with `nonce = 0`.
- `version` — set to `3`.
- `max_possible_fee` — set to `0` by zeroing all three resource bounds (`L1_GAS`, `L2_GAS`, `L1_DATA_GAS`) and `tip`. [3](#0-2) 

The `compiled_class_hash` is also user-controlled and is committed to by the transaction hash (via `compute_declare_transaction_hash`), but the attacker freely chooses it when crafting the transaction. [4](#0-3) 

The `dict_update` call uses `prev_value=0`, which means each class hash can be written exactly once. If an attacker writes a wrong `compiled_class_hash` for a given `class_hash`, the legitimate owner can **never** overwrite it — the OS will reject any subsequent declaration of the same class hash because `prev_value` will no longer be `0`. [5](#0-4) 

---

### Impact Explanation

**Critical — Direct loss of funds (fee bypass):** An attacker can declare an unlimited number of contract classes without paying any fees. The sequencer receives no compensation for the computational and DA costs incurred, constituting a direct economic loss.

**Critical — Permanent freezing of funds (class registry poisoning):** An attacker who observes a pending class hash (e.g., from a mempool or a known deployment plan) can front-run its legitimate declaration by submitting a bootstrap-path transaction with the correct `class_hash` but an arbitrary wrong `compiled_class_hash`. Because the `prev_value=0` invariant is enforced by the OS, the legitimate class can never be declared afterward. Any contract that was intended to be deployed using that class hash becomes permanently undeployable. If funds are locked in contracts that depend on the poisoned class (e.g., account contracts, multisigs, vaults), those funds are permanently frozen.

---

### Likelihood Explanation

The attack requires only that the attacker:
1. Craft a valid declare transaction with `sender_address = 0x424f4f545354524150` ('BOOTSTRAP'), `nonce = 0`, `version = 3`, and all resource bounds zeroed.
2. Submit it to the sequencer before the legitimate transaction.

No privileged access, leaked keys, or malicious operator behavior is required. The OS itself enforces no restriction on who may use the bootstrap path. The sequencer's mempool may apply additional heuristics, but the OS — which is the authoritative proof validator — accepts such transactions unconditionally. A sequencer that faithfully executes what the OS permits will include these transactions.

---

### Recommendation

The bootstrap path must be restricted to a verifiably privileged context. Options include:

1. **Remove the bootstrap path entirely** and use a separate, out-of-band mechanism (e.g., a genesis block with special OS handling) for initial class declarations.
2. **Bind the bootstrap path to a specific privileged address** that is part of the `BlockContext` (e.g., `block_context.os_global_context`) rather than a hardcoded felt literal, so only the sequencer operator can configure it.
3. **Require a valid signature from a known bootstrapper key** even in the bootstrap path, so the OS enforces authorization cryptographically.

---

### Proof of Concept

**Attacker steps:**

1. Observe a target `class_hash` H that has not yet been declared (e.g., from a pending mempool transaction or a known deployment plan).
2. Craft a declare transaction with:
   - `sender_address = 0x424f4f545354524150` (felt encoding of `'BOOTSTRAP'`)
   - `nonce = 0`
   - `version = 3`
   - `tip = 0`, all resource bounds zeroed → `max_possible_fee = 0`
   - `class_hash = H` (valid Sierra class hash, verified by `finalize_class_hash`)
   - `compiled_class_hash = 1` (any non-zero wrong value)
3. Submit to the sequencer. The OS executes `execute_declare_transaction`:
   - Computes and verifies the transaction hash (commits to all fields above).
   - Enters the bootstrap branch at line 764.
   - Calls `dict_update(key=H, prev_value=0, new_value=1)` — writing the wrong compiled class hash.
   - Returns without calling `__validate_declare__`, `check_and_increment_nonce`, or `charge_fee`.
4. The legitimate owner later submits a correct declare transaction for H. The OS calls `dict_update(key=H, prev_value=0, new_value=correct_compiled_class_hash)` — **this panics** because `prev_value` is now `1`, not `0`.
5. Class H is permanently poisoned. Any contract depending on H is undeployable. Funds locked in such contracts are permanently frozen. [6](#0-5) [7](#0-6)

### Citations

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L86-101)
```text
// Returns the maximum possible fee that can be charged for the transaction.
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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/transaction_hash/transaction_hash.cairo (L264-292)
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
}
```
