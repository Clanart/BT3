### Title
Bootstrap Declare Path Bypasses Signature Verification, Nonce Check, and Fee Enforcement — (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo`)

---

### Summary

The `execute_declare_transaction` function in the StarkNet OS Cairo program contains a hardcoded "BOOTSTRAP" code path that, when triggered, completely skips signature verification (`run_validate`), nonce checking (`check_and_increment_nonce`), and fee charging (`charge_fee`). Any declare transaction crafted with `sender_address == 'BOOTSTRAP'`, `nonce == 0`, `version == 3`, and `max_fee == 0` is accepted by the OS prover without any account authorization, enabling fee-free, signature-free class declaration that produces a valid ZK proof accepted by the L1 verifier.

---

### Finding Description

In `execute_declare_transaction`, the following branch exists:

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

When this branch is taken, the function returns **before** reaching:

1. **`check_and_increment_nonce`** (line 779) — nonce is never verified against the on-chain state, and never incremented. [2](#0-1) 

2. **`run_validate`** (called via `non_reverting_select_execute_entry_point_func` at line 804) — the account contract's `__validate_declare__` entry point is never executed, so no signature is checked. [3](#0-2) 

3. **`charge_fee`** (line 822) — no ERC-20 transfer is performed, so no fee is deducted from any account. [4](#0-3) 

The `sender_address` field is a plain felt loaded from the hint `%{ DeclareTxFields %}`. The value `'BOOTSTRAP'` is the ASCII-encoded felt `0x424f4f545354524150`. The OS does **not** verify that this address corresponds to a deployed contract — the state entry lookup for `sender_address` only occurs on the normal path (line 782), which is never reached in the BOOTSTRAP branch. [5](#0-4) 

The class hash pre-image check (`finalize_class_hash`) is still enforced, so only a well-formed Sierra class can be declared. However, **who** declares it and **whether they paid** is entirely unenforced.

This is structurally identical to the ContextModules bypass: a specific code path (BOOTSTRAP declare) is not wrapped by the security enforcement layer (validate + nonce + fee) that applies to every other declare transaction.

---

### Impact Explanation

**Critical — Direct loss of funds.**

A sequencer that includes a BOOTSTRAP declare transaction in a block can generate a fully valid ZK proof for that block. The L1 verifier accepts the proof. The result committed on-chain includes the new class hash mapped to its compiled class hash, with:

- **Zero fee paid** — the sequencer (and by extension the protocol) receives no compensation for the block resources consumed. At scale, a sequencer can declare an unbounded number of classes across successive blocks at zero cost, draining the economic model that funds network operation.
- **No authorized account** — the class is declared without any account's signature, meaning the class registry can be populated with classes that no legitimate account owner authorized. If a malicious class is subsequently deployed (via a normal `deploy` syscall referencing the newly registered class hash), it can execute arbitrary logic against user funds.

---

### Likelihood Explanation

**Medium.** The exploit requires the sequencer to include a transaction with `sender_address = 'BOOTSTRAP'`. An honest sequencer's mempool layer would reject such a transaction (no deployed account at that address, no valid signature). However:

- The OS Cairo program is the **ground truth** for proof validity. The L1 verifier has no visibility into mempool-level checks.
- A sequencer operator who controls block production (the current StarkNet architecture has a single sequencer) can craft and self-include such a transaction without routing it through the public mempool at all.
- The OS code itself imposes no constraint preventing this — the BOOTSTRAP branch is a provably satisfiable execution path with no cryptographic barrier.

The attack surface is therefore any party with block-production rights, which in the current network is the sequencer operator.

---

### Recommendation

Remove the BOOTSTRAP special-case entirely from the OS Cairo code. System bootstrapping (declaring pre-deployed classes at genesis) should be handled at the genesis state construction level — by directly populating the initial `contract_class_changes` dictionary in the genesis block's state — rather than by carving out an unauthenticated transaction path in the live OS prover. If a bootstrapping transaction type is genuinely required, it must be gated by a verifiable on-chain condition (e.g., block number == 0, enforced as a Cairo `assert`, not a hint) and must still enforce fee payment and a valid signature from a designated bootstrap key whose public key is committed in the OS config.

---

### Proof of Concept

```
Attacker (sequencer operator) constructs a declare transaction with:
  sender_address  = 0x424f4f545354524150  ('BOOTSTRAP' as felt)
  nonce           = 0
  version         = 3
  resource_bounds = [0, 0, 0]  (all max_amounts = 0 → max_fee = 0)
  class_hash      = <any valid Sierra class hash>
  compiled_class_hash = <corresponding CASM hash>

The OS executes execute_declare_transaction():
  1. tx_version != 0 → does not skip early.
  2. Transaction hash is computed and constrained (includes 'BOOTSTRAP' as sender).
  3. fill_account_tx_info() fills TxInfo with nonce=0, version=3, max_fee=0.
  4. Condition at line 764 evaluates TRUE:
       sender_address == 'BOOTSTRAP' ✓
       tx_info.nonce == 0            ✓
       tx_info.version == 3          ✓
  5. compute_max_possible_fee() returns 0 (all bounds are 0) → inner condition TRUE.
  6. dict_update writes class_hash → compiled_class_hash into contract_class_changes.
  7. Returns. check_and_increment_nonce, run_validate, charge_fee are never called.

The OS produces a valid proof. L1 verifier accepts it.
Result: arbitrary class declared on-chain, zero fee paid, no signature verified.
```

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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L778-779)
```text
    // Increment nonce.
    check_and_increment_nonce(tx_info=tx_info);
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L782-782)
```text
    let (state_entry: StateEntry*) = dict_read{dict_ptr=contract_state_changes}(key=sender_address);
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L800-807)
```text
    with remaining_gas {
        cap_remaining_gas(max_gas=VALIDATE_MAX_SIERRA_GAS);
        // Run the account contract's "__validate_declare__" entry point.
        %{ StartTx %}
        let (retdata_size, retdata, is_deprecated) = non_reverting_select_execute_entry_point_func(
            block_context=block_context, execution_context=validate_declare_execution_context
        );
    }
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L821-824)
```text
    // Charge fee.
    charge_fee(
        block_context=block_context, tx_execution_context=validate_declare_execution_context
    );
```
