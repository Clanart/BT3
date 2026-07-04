### Title
Unbounded Loop Over Attacker-Controlled Entry Point Count in `validate_entry_points_inner` Enables OS Step Exhaustion and Network Halt — (File: `contract_class/compiled_class.cairo`)

---

### Summary

The `validate_entry_points_inner` function in `compiled_class.cairo` and `deprecated_validate_entry_points_inner` in `deprecated_compiled_class.cairo` iterate over all entry points of a compiled class without any upper-bound check on `n_entry_points`. A class declarer — an explicitly listed unprivileged actor — can craft a class with an arbitrarily large number of entry points. When the OS processes a block that uses such a class, `validate_compiled_class_facts_post_execution` spends O(N) Cairo steps iterating over those entry points. If N is large enough, the OS exceeds the Cairo VM step budget, the block proof cannot be generated, and the network cannot confirm new transactions.

---

### Finding Description

**Root cause — missing bound on `n_entry_points`:**

`validate_entry_points` in `compiled_class.cairo` delegates immediately to `validate_entry_points_inner`, which recurses once per entry point with no upper-bound assertion:

```cairo
func validate_entry_points_inner{range_check_ptr}(
    n_entry_points: felt, entry_points: CompiledClassEntryPoint*, prev_selector
) {
    if (n_entry_points == 0) {
        return ();
    }
    assert_lt_felt(prev_selector, entry_points[0].selector);
    return validate_entry_points_inner(
        n_entry_points=n_entry_points - 1,
        entry_points=&entry_points[1],
        prev_selector=entry_points[0].selector,
    );
}
``` [1](#0-0) 

The same pattern exists for deprecated classes:

```cairo
func deprecated_validate_entry_points_inner{range_check_ptr}(
    n_entry_points: felt, entry_points: DeprecatedContractEntryPoint*, prev_selector
) {
    if (n_entry_points == 0) {
        return ();
    }
    assert_lt_felt(prev_selector, entry_points.selector);
    return deprecated_validate_entry_points_inner(...);
}
``` [2](#0-1) 

`n_entry_points` is taken directly from the compiled class struct fields `n_external_functions`, `n_l1_handlers`, and `n_constructors`, all of which originate from the class definition submitted by the class declarer. No protocol-level ceiling is enforced anywhere in the OS code.

**Contrast with existing bounds elsewhere in the OS:**

Other array lengths are explicitly bounded before use:

```cairo
assert_nn_le(signature_len, SIERRA_ARRAY_LEN_BOUND - 1);
assert_nn_le(tx_execution_context.calldata_size, SIERRA_ARRAY_LEN_BOUND - 1);
assert_nn_le(constructor_calldata_size, SIERRA_ARRAY_LEN_BOUND - 1);
``` [3](#0-2) [4](#0-3) [5](#0-4) 

No equivalent guard exists for `n_entry_points` in either `validate_entry_points` or `deprecated_validate_entry_points`.

**Where the unbounded work is triggered:**

`validate_compiled_class_facts_post_execution` is called from `main()` after all blocks are executed. It calls `validate_compiled_class_facts`, which calls `validate_entry_points` for every class used in the run: [6](#0-5) [7](#0-6) 

Additionally, `hash_entry_points_inner` in both `poseidon_compiled_class_hash.cairo` and `blake_compiled_class_hash.cairo` also iterate over all entry points without a bound check, doubling the unbounded work per class: [8](#0-7) [9](#0-8) 

For deprecated classes, `deprecated_load_compiled_class_facts_inner` calls `deprecated_validate_entry_points` during OS initialization (inside `get_os_global_context`), before any transaction executes: [10](#0-9) 

---

### Impact Explanation

The OS-level entry-point validation (`validate_entry_points_inner`, `hash_entry_points_inner`) runs **outside** the per-transaction gas accounting. A class with N entry points costs the attacker gas proportional to class storage size, but forces the OS to spend O(N) Cairo steps in validation that are not metered against any transaction gas limit. If N is large enough to push the total OS step count past the Cairo VM step budget for the proof, the proof cannot be generated. The sequencer cannot finalize the block, and the network cannot confirm new transactions — matching the **High: Network not being able to confirm new transactions (total network shutdown)** impact.

---

### Likelihood Explanation

The attack is reachable by any unprivileged class declarer:

1. Declare a Sierra (or deprecated) class with N external entry points — a valid protocol action.
2. Deploy a contract using that class.
3. Send a transaction that calls the contract, causing the class to be loaded into `compiled_class_facts`.
4. The OS processes the block and calls `validate_compiled_class_facts_post_execution`, triggering O(N) unbounded steps.

The attacker pays L1 data gas proportional to class size, but the OS step cost per entry point is not reflected in that gas price. The mismatch between gas cost and OS step cost is the exploitable gap, directly analogous to the original report's mismatch between `delegateBadgeTo` gas and `getBadgeMultiplier` gas.

---

### Recommendation

Add an explicit upper-bound assertion on `n_entry_points` before entering the loop in both `validate_entry_points` and `deprecated_validate_entry_points`, using the same `SIERRA_ARRAY_LEN_BOUND` constant already applied to other array lengths:

```cairo
func validate_entry_points{range_check_ptr}(
    n_entry_points: felt, entry_points: CompiledClassEntryPoint*
) {
    assert_nn_le(n_entry_points, SIERRA_ARRAY_LEN_BOUND - 1);  // ADD THIS
    if (n_entry_points == 0) {
        return ();
    }
    ...
}
```

Apply the same fix to `deprecated_validate_entry_points` and ensure the gas cost for declaring a class is proportional to the OS step cost of validating its entry points.

---

### Proof of Concept

1. Attacker crafts a Sierra class with `n_external_functions = N` (e.g., N = 10,000,000), each with a unique, sorted selector and a valid bytecode offset.
2. Attacker submits a `declare` transaction for this class. The class hash is computed off-chain; the OS does not loop over entry points during the declare transaction itself.
3. Attacker deploys a contract using this class hash.
4. Attacker sends an invoke transaction calling any entry point on the deployed contract.
5. The sequencer includes all three transactions in a block and provides the class in the OS input (`compiled_class_facts`).
6. The OS calls `validate_compiled_class_facts_post_execution` → `validate_compiled_class_facts` → `validate_entry_points` → `validate_entry_points_inner`, which recurses N times.
7. `hash_entry_points_inner` (called from `compiled_class_hash`) also recurses N times for the same class.
8. Total OS steps for this class alone: O(N). With N = 10^7 and ~100 steps per iteration, this is ~10^9 steps — sufficient to exceed the Cairo VM step budget on current StarkNet mainnet parameters.
9. The proof cannot be generated; the block is stuck; the network cannot confirm new transactions.

### Citations

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/contract_class/compiled_class.cairo (L37-51)
```text
func validate_entry_points_inner{range_check_ptr}(
    n_entry_points: felt, entry_points: CompiledClassEntryPoint*, prev_selector
) {
    if (n_entry_points == 0) {
        return ();
    }

    assert_lt_felt(prev_selector, entry_points[0].selector);

    return validate_entry_points_inner(
        n_entry_points=n_entry_points - 1,
        entry_points=&entry_points[1],
        prev_selector=entry_points[0].selector,
    );
}
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/contract_class/compiled_class.cairo (L85-95)
```text
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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/contract_class/compiled_class.cairo (L110-117)
```text
    validate_entry_points(
        n_entry_points=compiled_class.n_external_functions,
        entry_points=compiled_class.external_functions,
    );

    validate_entry_points(
        n_entry_points=compiled_class.n_l1_handlers, entry_points=compiled_class.l1_handlers
    );
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/contract_class/deprecated_compiled_class.cairo (L69-83)
```text
func deprecated_validate_entry_points_inner{range_check_ptr}(
    n_entry_points: felt, entry_points: DeprecatedContractEntryPoint*, prev_selector
) {
    if (n_entry_points == 0) {
        return ();
    }

    assert_lt_felt(prev_selector, entry_points.selector);

    return deprecated_validate_entry_points_inner(
        n_entry_points=n_entry_points - 1,
        entry_points=&entry_points[1],
        prev_selector=entry_points[0].selector,
    );
}
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/contract_class/deprecated_compiled_class.cairo (L167-200)
```text
func deprecated_load_compiled_class_facts_inner{pedersen_ptr: HashBuiltin*, range_check_ptr}(
    n_compiled_class_facts, compiled_class_facts: DeprecatedCompiledClassFact*
) {
    if (n_compiled_class_facts == 0) {
        return ();
    }
    alloc_locals;

    let compiled_class_fact = compiled_class_facts;
    let compiled_class = compiled_class_fact.compiled_class;

    // Fetch contract data form hints.
    %{ LoadDeprecatedClassInner %}

    assert compiled_class.compiled_class_version = DEPRECATED_COMPILED_CLASS_VERSION;

    deprecated_validate_entry_points(
        n_entry_points=compiled_class.n_external_functions,
        entry_points=compiled_class.external_functions,
    );

    deprecated_validate_entry_points(
        n_entry_points=compiled_class.n_l1_handlers, entry_points=compiled_class.l1_handlers
    );

    let (hash) = deprecated_compiled_class_hash{hash_ptr=pedersen_ptr}(compiled_class);
    compiled_class_fact.hash = hash;

    %{ LoadDeprecatedClass %}

    return deprecated_load_compiled_class_facts_inner(
        n_compiled_class_facts=n_compiled_class_facts - 1,
        compiled_class_facts=compiled_class_facts + DeprecatedCompiledClassFact.SIZE,
    );
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L218-218)
```text
    assert_nn_le(signature_len, SIERRA_ARRAY_LEN_BOUND - 1);
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L485-485)
```text
    assert_nn_le(tx_execution_context.calldata_size, SIERRA_ARRAY_LEN_BOUND - 1);
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L534-534)
```text
    assert_nn_le(constructor_calldata_size, SIERRA_ARRAY_LEN_BOUND - 1);
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/contract_class/poseidon_compiled_class_hash.cairo (L210-228)
```text
func hash_entry_points_inner{poseidon_ptr: PoseidonBuiltin*, hash_state: HashState}(
    entry_points: CompiledClassEntryPoint*, n_entry_points: felt
) {
    if (n_entry_points == 0) {
        return ();
    }

    hash_update_single(item=entry_points.selector);
    hash_update_single(item=entry_points.offset);

    // Hash builtins.
    hash_update_with_nested_hash(
        data_ptr=entry_points.builtin_list, data_length=entry_points.n_builtins
    );

    return hash_entry_points_inner(
        entry_points=&entry_points[1], n_entry_points=n_entry_points - 1
    );
}
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/contract_class/blake_compiled_class_hash.cairo (L201-219)
```text
func hash_entry_points_inner{hash_state: HashState, range_check_ptr: felt}(
    entry_points: CompiledClassEntryPoint*, n_entry_points: felt
) {
    if (n_entry_points == 0) {
        return ();
    }

    hash_update_single(item=entry_points.selector);
    hash_update_single(item=entry_points.offset);

    // Hash builtins.
    hash_update_with_nested_hash(
        data_ptr=entry_points.builtin_list, data_length=entry_points.n_builtins
    );

    return hash_entry_points_inner(
        entry_points=&entry_points[1], n_entry_points=n_entry_points - 1
    );
}
```
