### Title
Unauthorized Class Declaration via Unauthenticated Bootstrap Path Bypass — (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo`)

---

### Summary

The `execute_declare_transaction` function in the StarkNet OS contains a special "bootstrap" path that completely bypasses signature verification, nonce checking, and fee payment for declare transactions that meet trivially satisfiable conditions. Any unprivileged transaction sender can trigger this path to declare arbitrary contract classes without authorization, enabling potential deployment of malicious system contracts and direct loss of funds.

---

### Finding Description

In `execute_declare_transaction`, lines 764–776 of `transaction_impls.cairo`, the following special case exists:

```cairo
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
``` [1](#0-0) 

This bootstrap path:

1. **Skips `check_and_increment_nonce`** (called at line 779 in the normal path) — no nonce validation.
2. **Skips `run_validate`** (called at line 804 in the normal path) — no account signature verification.
3. **Skips `charge_fee`** (called at line 822 in the normal path) — no fee payment.
4. **Directly writes to `contract_class_changes`** via `dict_update`, registering any attacker-supplied `compiled_class_hash` under any `class_hash`. [2](#0-1) 

The trigger condition `sender_address == 'BOOTSTRAP'` compares against a Cairo felt literal — the ASCII encoding of the string `"BOOTSTRAP"` — which is a fixed, publicly known felt value (`0x424f4f545354524150`). There is no cryptographic proof of identity required. The remaining conditions (`nonce == 0`, `version == 3`, all resource bounds zero) are trivially satisfiable by any transaction sender.

The `sender_address` field is loaded from the hint `%{ DeclareTxFields %}` and is part of the transaction submitted by the user: [3](#0-2) 

The transaction hash is computed in Cairo and checked only via the hint `%{ AssertTransactionHash %}` (line 732), which is not a Cairo constraint and is not enforced in the proof. The account contract's `__validate__` entry point — which would normally verify the signature against the transaction hash — is never called in the bootstrap path. [4](#0-3) 

The `prev_value=0` guard in `dict_update` prevents overwriting an already-declared class, but does not prevent an attacker from declaring a brand-new malicious class hash for the first time.

---

### Impact Explanation

**Critical — Direct loss of funds / High — Network shutdown.**

A declared class becomes available for deployment across the entire StarkNet network. An attacker who successfully declares a malicious class can:

1. **Deploy it as the fee token**: The fee token address is fixed in `starknet_os_config` and read at `block_context.os_global_context.starknet_os_config.fee_token_address`. If the attacker can arrange for the fee token contract to be upgraded to the malicious class (e.g., via a `replace_class` syscall from a compromised account, or by front-running a legitimate upgrade), all user funds held in the fee token contract are at risk of direct theft. [5](#0-4) 

2. **Front-run legitimate class declarations**: Since `prev_value=0` enforces that a class may be declared only once, an attacker who races to declare a class hash with a malicious `compiled_class_hash` permanently poisons that class hash. Any contract subsequently deployed using that class hash will execute the attacker's bytecode instead of the intended code. This can cause permanent freezing of funds in contracts that depend on the poisoned class.

3. **Bypass fee payment entirely**: The attacker pays zero fees for the class declaration, enabling spam of the class registry and potential state bloat leading to network degradation or shutdown.

---

### Likelihood Explanation

**High.** The four conditions required to trigger the bootstrap path are all trivially satisfiable by any unprivileged user:

| Condition | Satisfiability |
|---|---|
| `sender_address == 'BOOTSTRAP'` | Any user can set this field in a declare transaction |
| `nonce == 0` | Simply use nonce 0 |
| `version == 3` | Standard transaction version |
| `max_possible_fee == 0` | Set all resource bounds to zero |

The sequencer must implement a consistent validation path with the OS. Since the OS accepts bootstrap transactions without signature validation, the sequencer's off-chain validation layer is expected to mirror this behavior. An attacker submitting a declare transaction with these parameters will have it accepted and included in a block, after which the OS processes it through the bypass path with no further checks.

---

### Recommendation

1. **Remove the bootstrap path entirely** from the production OS. Bootstrapping should be handled through a separate, privileged, off-chain mechanism before the OS is deployed.
2. If bootstrapping must remain in the OS, **replace the magic string comparison** with a cryptographic check: require the transaction to be signed by a hardcoded trusted public key, verified using the `ecdsa` builtin, rather than relying on a publicly known felt literal as the sole gating condition.
3. **Add a Cairo-level assertion** (not just a hint) that the computed transaction hash matches the submitted hash, so that the prover cannot silently skip hash verification.

---

### Proof of Concept

**Attacker-controlled inputs:**

```
sender_address  = 0x424f4f545354524150   // felt('BOOTSTRAP')
nonce           = 0
version         = 3
L1_gas_max_amount       = 0
L2_gas_max_amount       = 0
L1_data_gas_max_amount  = 0
// All max_price_per_unit = 0  →  max_possible_fee = 0
class_hash      = <hash of attacker's malicious Sierra class>
compiled_class_hash = <hash of attacker's malicious CASM class>
```

**Execution trace through the OS:**

1. `execute_declare_transaction` is entered. `tx_version != 0`, so execution continues.
2. `sender_address`, `class_hash`, `compiled_class_hash` are loaded from hints.
3. Transaction hash is computed; `%{ AssertTransactionHash %}` is a hint only — not a Cairo constraint.
4. `finalize_class_hash` verifies the Sierra class hash pre-image — this is satisfied by the attacker providing a valid Sierra class structure.
5. The bootstrap condition at line 764 evaluates to true.
6. `compute_max_possible_fee` returns 0 (all bounds are zero).
7. `dict_update` writes `class_hash → compiled_class_hash` into `contract_class_changes` with `prev_value=0`.
8. `%{ SkipTx %}` is called and the function returns — **no nonce check, no signature verification, no fee charge**.
9. The malicious class is now declared on-chain. Any subsequent `deploy_syscall` or `replace_class` syscall referencing this class hash will execute the attacker's bytecode. [1](#0-0)

### Citations

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L138-141)
```text
    local fee_token_address = block_context.os_global_context.starknet_os_config.fee_token_address;
    let (fee_state_entry: StateEntry*) = dict_read{dict_ptr=contract_state_changes}(
        key=fee_token_address
    );
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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L730-744)
```text
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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L779-825)
```text
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
