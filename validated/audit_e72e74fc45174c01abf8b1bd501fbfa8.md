### Title
Insufficient Authentication for BOOTSTRAP Declare Transactions Allows Unauthorized Class Declaration - (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo`)

---

### Summary

The `execute_declare_transaction` function contains a privileged "BOOTSTRAP" path that bypasses all signature verification, nonce enforcement, and fee charging. The sole authentication is a simple felt comparison `sender_address == 'BOOTSTRAP'`. Because `sender_address` is an attacker-controlled field in the transaction data (loaded from a hint), a malicious sequencer can craft a declare transaction that passes this check and registers an arbitrary `compiled_class_hash` for a valid Sierra class, permanently corrupting the class registry.

---

### Finding Description

In `execute_declare_transaction`, lines 764–776, a special BOOTSTRAP path exists:

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

This path:
1. **Skips `__validate_declare__`** — no signature verification is performed.
2. **Skips `check_and_increment_nonce`** — no nonce enforcement.
3. **Skips `charge_fee`** — no fee payment required.
4. **Directly writes to `contract_class_changes`** with the caller-supplied `compiled_class_hash`.

The `sender_address` is loaded from the hint `%{ DeclareTxFields %}` and is part of the transaction data the sequencer controls: [2](#0-1) 

The `compiled_class_hash` is only checked to be non-zero (`assert_not_zero(compiled_class_hash)`), not verified against actual CASM bytecode. The Sierra class hash is verified via `finalize_class_hash`, but the CASM mapping is entirely attacker-controlled. [3](#0-2) 

The `dict_update` uses `prev_value=0`, meaning a class can only be declared once — so a poisoned declaration is **permanent and irreversible**. [4](#0-3) 

This is directly analogous to the Adrena vulnerability: instead of a cryptographic proof of identity (PDA), the code checks only a simple felt value (`sender_address == 'BOOTSTRAP'`) that any actor controlling the transaction data can set.

---

### Impact Explanation

A malicious sequencer can:
1. Craft a declare transaction: `sender_address = 'BOOTSTRAP'`, `nonce = 0`, `version = 3`, all resource bounds = 0.
2. Provide a valid Sierra class hash `X` (passes `finalize_class_hash` check).
3. Provide an incorrect `compiled_class_hash = Y` (any non-zero felt not corresponding to valid CASM).
4. The OS accepts the transaction — no signature verification occurs.
5. `contract_class_changes` is updated: `X → Y` permanently.
6. Any future legitimate declaration of class `X` fails (`prev_value` is now `Y`, not `0`).
7. Any contract whose class hash is `X` fails to execute (CASM for `Y` does not exist in `compiled_class_facts`).
8. Funds held by contracts using class `X` are **permanently frozen**.

**Impact: Critical — Permanent freezing of funds.**

---

### Likelihood Explanation

This requires a malicious sequencer who controls which transactions are included in blocks and what hints are provided to the OS. In the decentralized StarkNet security model, the OS Cairo program is the source of truth for valid state transitions — it is supposed to enforce correctness even against a malicious sequencer. The OS's acceptance of this transaction produces a valid proof that L1 will accept, making the state corruption irreversible on-chain.

---

### Recommendation

Replace the simple felt comparison with a cryptographic proof of identity:
- Require a signature from a known bootstrap public key verified inside the OS.
- Use a contract-address-derived (PDA-equivalent) mechanism: derive the bootstrap address deterministically from a known seed and verify the account exists and has signed.
- Remove the BOOTSTRAP path entirely and require bootstrapping through a properly deployed and signed account transaction.

---

### Proof of Concept

1. Malicious sequencer crafts a declare transaction hint with: `sender_address = 'BOOTSTRAP'`, `nonce = 0`, `version = 3`, `l1_gas_max_amount = 0`, `l2_gas_max_amount = 0`, `l1_data_gas_max_amount = 0`.
2. Sequencer provides a valid Sierra class with `class_hash = X` (passes `finalize_class_hash`).
3. Sequencer provides `compiled_class_hash = Y` where `Y ≠ 0` but does not correspond to any valid CASM.
4. OS evaluates: `sender_address == 'BOOTSTRAP'` ✓, `nonce == 0` ✓, `version == 3` ✓, `max_fee == 0` ✓.
5. OS executes `dict_update(key=X, prev_value=0, new_value=Y)` in `contract_class_changes`.
6. Class `X` is permanently declared with incorrect CASM hash `Y`.
7. Any future legitimate declaration of class `X` fails (squash will find `prev_value = Y ≠ 0`).
8. Any contract with `class_hash = X` fails at `execute_entry_point` when `find_element` cannot locate CASM for hash `Y`. [5](#0-4) 

9. Funds in those contracts are permanently frozen. The proof is valid; L1 accepts the state transition.

### Citations

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L710-716)
```text
    local sender_address;
    local class_hash_ptr: felt*;
    local compiled_class_hash;
    local account_deployment_data_size;
    local account_deployment_data: felt*;
    %{ DeclareTxFields %}
    let common_tx_fields = get_account_tx_common_fields(
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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/execute_entry_point.cairo (L154-166)
```text
    let (compiled_class_hash: felt) = dict_read{dict_ptr=contract_class_changes}(
        key=execution_context.class_hash
    );

    // The key must be at offset 0.
    static_assert CompiledClassFact.hash == 0;
    let compiled_class_facts_bundle = block_context.os_global_context.compiled_class_facts_bundle;
    let (compiled_class_fact: CompiledClassFact*) = find_element(
        array_ptr=compiled_class_facts_bundle.compiled_class_facts,
        elm_size=CompiledClassFact.SIZE,
        n_elms=compiled_class_facts_bundle.n_compiled_class_facts,
        key=compiled_class_hash,
    );
```
