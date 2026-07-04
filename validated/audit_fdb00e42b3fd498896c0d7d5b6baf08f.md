### Title
Unverified `compiled_class_hash` in Declare Transaction Permanently Corrupts Class-to-CASM Mapping — (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo`)

---

### Summary

`execute_declare_transaction` stores the caller-supplied `compiled_class_hash` into the global class-change dictionary with `prev_value=0`, enforcing a **write-once** semantic. The OS verifies the Sierra `class_hash` but performs **no verification** that `compiled_class_hash` is the actual CASM hash of the declared class. If an incorrect value is committed, the mapping is permanently corrupted: the class cannot be re-declared, and every contract deployed under that `class_hash` becomes permanently un-executable, freezing any funds held within.

---

### Finding Description

Inside `execute_declare_transaction`, the `compiled_class_hash` is loaded from the transaction via hint and committed to state:

```cairo
// Note that prev_value=0 enforces that a class may be declared only once.
assert_not_zero(compiled_class_hash);
dict_update{dict_ptr=contract_class_changes}(
    key=[class_hash_ptr], prev_value=0, new_value=compiled_class_hash
);
``` [1](#0-0) 

The Sierra `class_hash` is correctly verified against the declared class components:

```cairo
let expected_class_hash = finalize_class_hash(
    contract_class_component_hashes=contract_class_component_hashes
);
assert [class_hash_ptr] = expected_class_hash;
``` [2](#0-1) 

However, `compiled_class_hash` — the CASM hash — is **never verified** against any compiled class artifact. The only check is `assert_not_zero(compiled_class_hash)`. [3](#0-2) 

By contrast, the class migration path in `os_utils.cairo` **does** verify the CASM hash before committing it:

```cairo
assert compiled_class_fact.hash = casm_hash_v2;
dict_update{dict_ptr=contract_class_changes}(
    key=class_hash, prev_value=casm_hash_v1, new_value=casm_hash_v2
);
``` [4](#0-3) 

This asymmetry confirms the OS is capable of verifying CASM hashes but omits the check at declaration time.

The `validate_compiled_class_facts_post_execution` call in `os.cairo` validates only compiled classes **used during execution** of the current block — it does not retroactively validate `compiled_class_hash` values committed by declare transactions, because the declared class itself is never executed during declaration. [5](#0-4) 

The `CommitmentUpdate` produced by `state_update` then permanently encodes the corrupted `class_hash → compiled_class_hash` mapping into the global state root. [6](#0-5) 

---

### Impact Explanation

**Critical — Permanent freezing of funds.**

When any contract deployed under the corrupted `class_hash` is invoked, the OS resolves the CASM to execute via the stored `compiled_class_hash`. Because no compiled class with the incorrect hash exists in the prover's fact set, no valid STARK proof can be generated for that transaction. The transaction can never be included in a provable block. Any ETH, STRK, or ERC-20 tokens held in contracts under the broken class are permanently inaccessible with no on-chain recovery path, because:

1. The class cannot be re-declared (`prev_value=0` is enforced at the Cairo level).
2. There is no upgrade or override mechanism for the `class_hash → compiled_class_hash` mapping once committed.
3. The corrupted root propagates into every subsequent `CommitmentUpdate`, making the damage irreversible without a protocol upgrade.

---

### Likelihood Explanation

The attacker entry point is an **unprivileged class declarer** — explicitly listed as an in-scope actor. Two realistic paths exist:

1. **Implementation bug**: A class-declaration tool or SDK computes or encodes `compiled_class_hash` incorrectly (e.g., wrong hash version, wrong serialization). The declarer submits the transaction in good faith; the OS accepts it; the class is permanently broken.
2. **Griefing**: An attacker front-runs a legitimate class declaration (the Sierra `class_hash` is deterministic and publicly computable from the source) and submits a declare transaction with a valid `class_hash` but a fabricated `compiled_class_hash`. The legitimate declarer's subsequent declaration fails (`prev_value` is no longer 0), and any contracts deployed under that class are permanently un-executable.

Both paths require only a standard signed declare transaction — no privileged access, no key compromise.

---

### Recommendation

Verify `compiled_class_hash` against the actual compiled class artifact during declaration, mirroring the pattern already used in `migrate_classes_to_v2_casm_hash`:

```cairo
// Compute the CASM hash from the provided compiled class and assert equality.
let (expected_compiled_class_hash) = compiled_class_hash(compiled_class, full_contract=TRUE);
assert compiled_class_hash = expected_compiled_class_hash;
```

Alternatively, if on-chain CASM verification is too expensive, enforce it off-chain in the sequencer's pre-execution simulation and add a Cairo assertion that the `compiled_class_hash` appears in the block's `compiled_class_facts_bundle` before committing it to state.

---

### Proof of Concept

1. Attacker observes a Sierra class with deterministic `class_hash = H_sierra`.
2. Attacker submits a v3 declare transaction: `class_hash = H_sierra`, `compiled_class_hash = 0xdeadbeef` (arbitrary nonzero value).
3. The OS executes `execute_declare_transaction`:
   - `finalize_class_hash` confirms `H_sierra` is a valid Sierra hash. ✓
   - `assert_not_zero(0xdeadbeef)` passes. ✓
   - `dict_update(key=H_sierra, prev_value=0, new_value=0xdeadbeef)` commits the corrupted mapping. ✓
4. The corrupted mapping is squashed into the Patricia tree via `compute_class_commitment` and included in the final `CommitmentUpdate`.
5. A victim deploys a contract with `class_hash = H_sierra`.
6. A transaction invokes the victim's contract. The OS resolves `compiled_class_hash = 0xdeadbeef` from state. No compiled class with hash `0xdeadbeef` exists in `compiled_class_facts_bundle`. Proof generation fails.
7. The victim's contract is permanently un-executable. Funds are frozen. The legitimate declarer's attempt to re-declare fails because `prev_value` is now `0xdeadbeef ≠ 0`.

### Citations

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L738-743)
```text
        let expected_class_hash = finalize_class_hash(
            contract_class_component_hashes=contract_class_component_hashes
        );
        with_attr error_message("Invalid class hash pre-image.") {
            assert [class_hash_ptr] = expected_class_hash;
        }
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L815-819)
```text
    // Note that prev_value=0 enforces that a class may be declared only once.
    assert_not_zero(compiled_class_hash);
    dict_update{dict_ptr=contract_class_changes}(
        key=[class_hash_ptr], prev_value=0, new_value=compiled_class_hash
    );
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/os_utils.cairo (L111-115)
```text
    assert compiled_class_fact.hash = casm_hash_v2;
    // Update the casm hash from v1 to v2.
    dict_update{dict_ptr=contract_class_changes}(
        key=class_hash, prev_value=casm_hash_v1, new_value=casm_hash_v2
    );
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/os.cairo (L114-120)
```text
    // Validate the guessed compile class facts.
    let compiled_class_facts_bundle = os_global_context.compiled_class_facts_bundle;
    validate_compiled_class_facts_post_execution(
        n_compiled_class_facts=compiled_class_facts_bundle.n_compiled_class_facts,
        compiled_class_facts=compiled_class_facts_bundle.compiled_class_facts,
        builtin_costs=compiled_class_facts_bundle.builtin_costs,
    );
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/state/state.cairo (L107-113)
```text
    tempvar state_update_output = new CommitmentUpdate(
        initial_root=initial_global_root, final_root=final_global_root
    );

    return (
        squashed_os_state_update=squashed_os_state_update, state_update_output=state_update_output
    );
```
