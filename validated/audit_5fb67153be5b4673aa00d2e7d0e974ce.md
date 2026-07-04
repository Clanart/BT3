### Title
Unauthenticated Bootstrap Path in `execute_declare_transaction` Bypasses Signature Validation and Fee Charging — (`crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo`)

---

### Summary

`execute_declare_transaction` contains a special "bootstrap" code path that, when triggered, skips `check_and_increment_nonce`, `run_validate` (signature verification), and `charge_fee`. The trigger conditions are entirely user-controlled felt-field comparisons with no cryptographic access control. Any unprivileged transaction sender can craft a valid declare transaction that satisfies these conditions, causing the OS to accept a class declaration with zero fee payment and zero signature verification.

---

### Finding Description

Inside `execute_declare_transaction`, after the transaction hash is computed and `tx_info` is filled, the following branch appears:

```cairo
// Do not run validate or perform any account-related actions for declare transactions that
// meet the following conditions.
// This flow is used for the sequencer to bootstrap a new system.
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

When this branch is taken, the function returns immediately after writing to `contract_class_changes`. The three critical steps that follow in the normal path are entirely skipped:

1. **`check_and_increment_nonce`** (line 779) — nonce is never verified or incremented.
2. **`run_validate`** (called via `non_reverting_select_execute_entry_point_func` at line 804) — the account contract's `__validate_declare__` entry point, which performs signature verification, is never executed.
3. **`charge_fee`** (line 822) — no ERC-20 transfer is made to the sequencer. [2](#0-1) 

The four trigger conditions are:

| Condition | Source |
|---|---|
| `sender_address == 'BOOTSTRAP'` | Felt literal comparison; `sender_address` is loaded from the hint `DeclareTxFields` |
| `tx_info.nonce == 0` | Field of the transaction, user-supplied |
| `tx_info.version == 3` | Field of the transaction, user-supplied |
| `max_possible_fee == 0` | All three resource-bound `max_amount` fields set to zero |

None of these conditions involve a cryptographic check (e.g., a signature from a privileged key, a Merkle proof, or a hash preimage). The felt literal `'BOOTSTRAP'` is simply the ASCII encoding `0x424F4F545354524150`. Any user can submit a declare transaction whose `sender_address` field equals this value.

Crucially, the OS does **not** verify that a contract is deployed at the `sender_address` before entering the bootstrap branch. The `dict_read` that fetches the sender's `StateEntry` (line 782) is located **after** the early return, so the bootstrap path never checks whether the 'BOOTSTRAP' address has any on-chain state. [3](#0-2) 

The normal declare flow for comparison — showing what is bypassed: [4](#0-3) 

---

### Impact Explanation

**Direct loss of funds (Critical):** The sequencer receives zero fee for every bootstrap-path declare transaction. Because `charge_fee` is skipped entirely, the ERC-20 transfer to the sequencer address never executes. An attacker can spam arbitrarily many class declarations (each with a distinct `class_hash`) at zero cost, draining sequencer revenue and potentially exhausting class-declaration namespace.

**Invalid transaction acceptance (High):** The `__validate_declare__` entry point — the account contract's signature-verification hook — is never called. The OS proof therefore attests that a class was validly declared by `sender_address = 'BOOTSTRAP'` without any cryptographic proof that the submitter controls that address. This constitutes acceptance of an invalid transaction at the protocol level.

---

### Likelihood Explanation

The attacker-controlled entry path is a standard declare transaction submitted to the sequencer. The attacker sets:
- `sender_address = 0x424F4F545354524150` (felt encoding of `'BOOTSTRAP'`)
- `nonce = 0`
- `version = 3`
- All resource bounds `max_amount = 0`

These are ordinary transaction fields. No privileged access, leaked key, or operator collusion is required. The OS Cairo code enforces no additional gate on this path. The sequencer's off-chain mempool may apply its own heuristics, but the OS proof system — which is the authoritative validity oracle — accepts such transactions unconditionally.

---

### Recommendation

1. **Remove the bootstrap path entirely** if it is no longer needed for production. The comment states it is for bootstrapping a new system; if that phase is complete, the dead code creates permanent attack surface.
2. **If the path must be retained**, gate it with a cryptographic check: require a valid signature from a well-known privileged key (e.g., one of the `public_keys` already loaded in `get_os_global_context`) over the transaction hash, and verify it inside the Cairo program — not in a hint.
3. **At minimum**, add a Cairo assertion that the `sender_address` has a non-zero `class_hash` in `contract_state_changes` before entering the bootstrap branch, so that a contract must actually be deployed at that address.

---

### Proof of Concept

1. Construct a declare transaction with:
   - `sender_address = 0x424F4F545354524150` (felt `'BOOTSTRAP'`)
   - `nonce = 0`
   - `version = 3`
   - `resource_bounds[L1_GAS].max_amount = 0`, `resource_bounds[L2_GAS].max_amount = 0`, `resource_bounds[L1_DATA_GAS].max_amount = 0`
   - Any valid Sierra `class_hash` / `compiled_class_hash` pair not yet declared on-chain

2. Submit the transaction to the sequencer. The sequencer includes it in a block.

3. The OS executes `execute_declare_transaction`:
   - Computes the transaction hash over the provided fields (including `sender_address = 'BOOTSTRAP'`).
   - Fills `tx_info` with `nonce = 0`, `version = 3`, `max_fee = 0`.
   - Evaluates the branch at line 764: all four conditions are satisfied.
   - Calls `compute_max_possible_fee` → returns `0` (all bounds are zero).
   - Executes `dict_update` to register the class in `contract_class_changes`.
   - Returns immediately — **no nonce check, no `__validate_declare__`, no fee transfer**.

4. The resulting proof is valid. The class is declared on-chain. The sequencer received no fee. No signature was verified. [5](#0-4)

### Citations

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
