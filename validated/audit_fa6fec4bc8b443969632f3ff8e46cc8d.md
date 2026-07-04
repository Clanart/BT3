### Title
Unauthenticated Class Declaration via BOOTSTRAP Sender Bypass Allows Arbitrary `compiled_class_hash` Injection - (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo`)

---

### Summary

`execute_declare_transaction` contains a special-case bypass at line 764 that, when `sender_address == 'BOOTSTRAP'`, `nonce == 0`, `version == 3`, and `max_fee == 0`, skips **all** account authorization checks (signature validation, nonce enforcement, fee payment) and directly writes an attacker-supplied `compiled_class_hash` into `contract_class_changes`. Because the `compiled_class_hash` is only checked to be non-zero — and is never validated against the actual CASM content in this path — an unprivileged user can permanently bind any Sierra class hash to an arbitrary compiled class hash, corrupting the class registry irreversibly.

---

### Finding Description

In `execute_declare_transaction`, the normal declare flow requires:
1. `check_and_increment_nonce` — enforces account ownership via nonce state
2. `non_reverting_select_execute_entry_point_func` with `VALIDATE_DECLARE_ENTRY_POINT_SELECTOR` — runs the account's `__validate_declare__` for signature verification
3. `charge_fee` — deducts payment from the sender's account

The bootstrap branch at lines 764–776 skips all three:

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

The only guard on `compiled_class_hash` in this path is `assert_not_zero`. The post-execution validator `validate_compiled_class_facts_post_execution` only validates compiled class facts that were **loaded for execution** (i.e., present in the `compiled_class_facts` bundle and actually called). A class that is merely declared via the bootstrap path but never executed in the same block is never subjected to CASM hash verification. [2](#0-1) 

The `dict_update` call uses `prev_value=0`, meaning a class hash can only be declared once. Once an attacker writes a poisoned `compiled_class_hash` for a given Sierra class hash, no legitimate declaration of that class hash can ever succeed. [3](#0-2) 

The normal declare path (lines 816–819) has the same `prev_value=0` constraint, confirming the one-time-only invariant. [4](#0-3) 

---

### Impact Explanation

**Critical — Direct loss of funds / Permanent freezing of funds.**

An attacker who front-runs a legitimate class declaration with a poisoned `compiled_class_hash` permanently corrupts the class registry entry for that Sierra class hash. Any contract subsequently deployed with that class hash will have the OS resolve its CASM to the attacker-supplied hash. Execution of such a contract will either:

- Fail entirely (if the hash resolves to no known CASM), freezing any funds held by or routed through that contract permanently, or
- Execute attacker-chosen CASM if the attacker also arranges for a compiled class fact with that hash to be present, enabling arbitrary logic substitution and direct fund theft.

Because the `prev_value=0` constraint makes the poisoning irreversible at the OS level, recovery requires a protocol-level intervention.

---

### Likelihood Explanation

The trigger conditions are entirely attacker-controlled and require no privileged access:

- `sender_address = 'BOOTSTRAP'` is a plain felt literal (ASCII encoding of "BOOTSTRAP"); any user can set this as the sender field of a declare transaction.
- `nonce = 0` is a free field in the transaction.
- `version = 3` is the standard current version.
- All resource bounds set to zero satisfies `max_possible_fee == 0`.

The transaction hash commits to all these fields, so the attacker constructs a well-formed, hash-consistent transaction. No account contract needs to exist at address `'BOOTSTRAP'` because the bootstrap path returns before reading any account state. The sequencer, following OS rules, would include such a transaction. The attack is executable by any user who can submit a transaction to the network. [5](#0-4) 

---

### Recommendation

1. **Validate `compiled_class_hash` in the bootstrap path** against the actual CASM content using the same `validate_compiled_class_facts` machinery used for the normal path, or require the compiled class fact to be present in the `compiled_class_facts` bundle.
2. **Restrict the bootstrap sender address** to a value that cannot be submitted by an external user — for example, by requiring it to be the zero address (which is reserved) or by gating the bypass on a sequencer-controlled flag that is not part of the user-submitted transaction fields.
3. **Add a nonce check even in the bootstrap path**, or document and enforce that bootstrap transactions are only valid in block 0 / genesis context, with an explicit block-number guard.

---

### Proof of Concept

1. Attacker identifies a Sierra class `C` with valid class hash `H_sierra` that is expected to be declared by the system (e.g., a system contract class).
2. Attacker constructs a declare transaction:
   - `sender_address = 0x424f4f545354524150` (felt encoding of `'BOOTSTRAP'`)
   - `nonce = 0`
   - `version = 3`
   - `resource_bounds = [(0,0), (0,0), (0,0)]` → `max_fee = 0`
   - `class_hash = H_sierra` (valid Sierra hash, verified by `finalize_class_hash`)
   - `compiled_class_hash = 1` (arbitrary non-zero value, not the real CASM hash)
3. The OS processes the transaction, enters the bootstrap branch at line 764, skips `check_and_increment_nonce`, `__validate_declare__`, and `charge_fee`.
4. `dict_update` writes `contract_class_changes[H_sierra] = 1` with `prev_value=0` — succeeding because the class was not previously declared.
5. The legitimate system declare transaction for `H_sierra` is now submitted. `dict_update` with `prev_value=0` fails because the entry is already `1`. The legitimate class can never be declared.
6. Any contract deployed with `class_hash = H_sierra` resolves to `compiled_class_hash = 1`, which has no valid CASM, causing all calls to that contract to fail and permanently freezing any funds held by it.

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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L814-819)
```text
    // Declare the class hash.
    // Note that prev_value=0 enforces that a class may be declared only once.
    assert_not_zero(compiled_class_hash);
    dict_update{dict_ptr=contract_class_changes}(
        key=[class_hash_ptr], prev_value=0, new_value=compiled_class_hash
    );
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/contract_class/compiled_class.cairo (L83-95)
```text
// Validates the compiled class facts structure and hash after the execution.
// Uses the execution info to optimize hash computation.
func validate_compiled_class_facts_post_execution{poseidon_ptr: PoseidonBuiltin*, range_check_ptr}(
    n_compiled_class_facts, compiled_class_facts: CompiledClassFact*, builtin_costs: felt*
) {
    validate_compiled_class_facts(
        n_compiled_class_facts=n_compiled_class_facts,
        compiled_class_facts=compiled_class_facts,
        builtin_costs=builtin_costs,
    );

    return ();
}
```
