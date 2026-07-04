### Title
Unrestricted BOOTSTRAP Bypass in `execute_declare_transaction` Allows Unauthorized Class Declaration Without Validation - (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo`)

---

### Summary
The `execute_declare_transaction` function in the StarkNet OS contains a hardcoded "BOOTSTRAP" escape hatch that unconditionally skips all transaction validation — including signature verification, nonce enforcement, and fee payment — whenever a declare transaction is submitted with `sender_address == 'BOOTSTRAP'`, `nonce == 0`, `version == 3`, and `max_fee == 0`. There is no time-lock, block-number guard, or one-time-use flag preventing this path from being triggered after the system has been bootstrapped. This is directly analogous to the `kill()` vulnerability: a privileged state-transition bypass callable without proper authorization at any point in the system's lifetime.

---

### Finding Description

In `transaction_impls.cairo` lines 764–776, the OS contains the following logic:

```cairo
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
``` [1](#0-0) 

When this path is taken, the OS:

1. **Skips `__validate_declare__`** — no signature or account ownership check is performed.
2. **Skips `check_and_increment_nonce`** — the nonce at address `'BOOTSTRAP'` is never incremented, so the `nonce == 0` condition is permanently satisfiable for any subsequent declare transaction targeting a different class hash.
3. **Skips fee payment** — `charge_fee` is never called.
4. **Directly writes to `contract_class_changes`** with `prev_value=0`, permanently registering the caller-supplied `compiled_class_hash` for the given `class_hash`. [2](#0-1) 

The `'BOOTSTRAP'` value is a Cairo felt literal (ASCII encoding of the string "BOOTSTRAP"). There is no contract at this address in a live system, meaning no legitimate signature can be produced for it — yet the OS accepts such transactions unconditionally. Crucially, there is **no guard** (no block-number check, no one-time flag, no state sentinel) preventing this path from being exercised after the initial bootstrapping phase.

Compare this to the normal declare flow, which enforces:
- `check_and_increment_nonce` [3](#0-2) 
- `non_reverting_select_execute_entry_point_func` for `__validate_declare__` [4](#0-3) 
- `charge_fee` [5](#0-4) 

None of these are invoked in the BOOTSTRAP path.

---

### Impact Explanation

**Critical — Permanent freezing of funds.**

The `dict_update` call enforces `prev_value=0`, meaning each class hash can only be declared once:

```cairo
dict_update{dict_ptr=contract_class_changes}(
    key=[class_hash_ptr], prev_value=0, new_value=compiled_class_hash
);
``` [6](#0-5) 

An attacker who front-runs a legitimate class declaration can register an arbitrary (e.g., zeroed or malformed) `compiled_class_hash` for a given `class_hash`. Once written, this entry is **permanent and irrevocable** — any subsequent attempt to declare the same class hash will fail the `prev_value=0` constraint. If the targeted class hash belongs to a critical system contract (e.g., the fee token, an account contract standard, or a bridge contract), the contract becomes permanently non-functional, freezing all funds that depend on it.

Additionally, because the nonce at `'BOOTSTRAP'` is never incremented, the attacker can repeat this attack for an unlimited number of class hashes across multiple blocks.

---

### Likelihood Explanation

In the current centralized sequencer model, exploiting this requires the sequencer to include a declare transaction with `sender_address == 'BOOTSTRAP'`. A malicious or compromised sequencer can craft and include such a transaction at any time without user cooperation. In a decentralized sequencer/validator model (which StarkNet is moving toward), any block proposer can exploit this path. The conditions (`sender_address = 'BOOTSTRAP'`, `nonce = 0`, `version = 3`, `max_fee = 0`) are trivially constructible. The absence of any bootstrapping-phase guard means the window of exploitation is the entire lifetime of the network.

---

### Recommendation

Add a guard that restricts the BOOTSTRAP path to the initial bootstrapping phase only. Options include:

1. **Block-number guard**: Only allow the BOOTSTRAP path if `block_number == 0` (or within a predefined bootstrapping window).
2. **One-time flag**: Introduce a sentinel value in the state (e.g., a storage slot in a system contract) that is set after bootstrapping is complete, and assert it is unset before entering the BOOTSTRAP path.
3. **Remove the bypass entirely**: Require all declare transactions, including bootstrapping ones, to go through the standard validation flow using a pre-deployed system account.

---

### Proof of Concept

1. Attacker identifies a class hash `C` that is about to be legitimately declared (e.g., by monitoring the mempool).
2. Attacker (or a malicious block proposer) constructs a declare transaction:
   - `sender_address = 'BOOTSTRAP'` (felt literal)
   - `nonce = 0`
   - `version = 3`
   - `max_fee = 0` (all resource bounds set to 0)
   - `class_hash = C`
   - `compiled_class_hash = <arbitrary malicious value>`
3. The transaction is included in a block. The OS enters the BOOTSTRAP path at line 764, skips all validation, and calls `dict_update` with `prev_value=0, new_value=<malicious compiled_class_hash>`.
4. The class hash `C` is now permanently registered with the wrong compiled class hash.
5. Any subsequent attempt to declare `C` legitimately fails because `prev_value` is no longer `0`.
6. Contracts depending on class `C` are permanently broken; funds held in or dependent on those contracts are frozen.
7. The attacker repeats steps 1–6 for additional class hashes, since the nonce at `'BOOTSTRAP'` is never incremented.

### Citations

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L761-779)
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

    // Increment nonce.
    check_and_increment_nonce(tx_info=tx_info);
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L804-806)
```text
        let (retdata_size, retdata, is_deprecated) = non_reverting_select_execute_entry_point_func(
            block_context=block_context, execution_context=validate_declare_execution_context
        );
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L822-824)
```text
    charge_fee(
        block_context=block_context, tx_execution_context=validate_declare_execution_context
    );
```
