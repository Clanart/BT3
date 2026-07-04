### Title
Unbounded Recursive Entry Point Iteration Without Count Validation in `validate_entry_points_inner` — (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/contract_class/compiled_class.cairo`)

---

### Summary

The `validate_entry_points_inner` function (and its mirror `hash_entry_points_inner` in both hash backends) recursively iterates over a compiled class's entry points using an attacker-influenced `n_entry_points` felt value with **no upper bound check**. This is the direct Cairo analog of the reported "Lack of Restriction on Seed Length" class: unbounded recursive processing of an attacker-controlled length field. A class declarer can submit a `CompiledClass` with an arbitrarily large `n_external_functions` (or `n_l1_handlers`, `n_constructors`) value, forcing the OS to recurse for an astronomically large number of steps, exhausting the Cairo VM step budget and making it impossible to generate a valid proof for the block.

---

### Finding Description

In `compiled_class.cairo`, `validate_entry_points` delegates immediately to `validate_entry_points_inner` with `n_entry_points - 1` and no prior range check:

```cairo
func validate_entry_points{range_check_ptr}(
    n_entry_points: felt, entry_points: CompiledClassEntryPoint*
) {
    if (n_entry_points == 0) {
        return ();
    }
    return validate_entry_points_inner(
        n_entry_points=n_entry_points - 1,
        ...
    );
}
```

`validate_entry_points_inner` then tail-recurses, decrementing `n_entry_points` by 1 each iteration, with no maximum bound enforced:

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
```

The `n_entry_points` value is sourced directly from `compiled_class.n_external_functions`, `compiled_class.n_l1_handlers`, and `compiled_class.n_constructors` — all fields of the `CompiledClass` struct loaded from the prover hint `LoadClassesAndBuildBytecodeSegmentStructures`, which must faithfully represent the class submitted by the class declarer.

The identical pattern exists in both hash backends:

- `hash_entry_points_inner` in `poseidon_compiled_class_hash.cairo` — tail-recurses over `n_entry_points` with no bound check.
- `hash_entry_points_inner` in `blake_compiled_class_hash.cairo` — same structure.

Contrast this with how other array lengths **are** bounded in the same OS: `calldata_size`, `signature_len`, and `constructor_calldata_size` are all checked against `SIERRA_ARRAY_LEN_BOUND` (2^32). Entry point counts receive no equivalent protection. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) 

---

### Impact Explanation

In Cairo VM, tail recursion compiles to a jump instruction, so there is no native call-stack overflow. However, each recursive iteration consumes a fixed number of Cairo VM steps. The proof system enforces a hard step budget per block. If `n_external_functions` is set to a value such as `2^40` or larger, the OS will attempt `2^40` recursive iterations of `validate_entry_points_inner` (and separately of `hash_entry_points_inner`), far exceeding the step budget of any realistic proof. The proof generation fails, the block cannot be finalized, and the network cannot confirm new transactions.

**Impact: High — Network not being able to confirm new transactions (total network shutdown).**

---

### Likelihood Explanation

A class declarer is an unprivileged protocol participant. The OS itself imposes no ceiling on `n_external_functions`, `n_l1_handlers`, or `n_constructors`. The only defense is off-chain sequencer-side validation, which is not enforced by the OS Cairo program and is therefore not part of the provable protocol invariants. If the sequencer omits or misconfigures this check, a single malicious declare transaction is sufficient to make the block unprovable. [6](#0-5) 

---

### Recommendation

Add an explicit upper bound assertion on `n_entry_points` inside `validate_entry_points` before delegating to the recursive inner function, mirroring the existing `SIERRA_ARRAY_LEN_BOUND` pattern used for calldata and signatures:

```cairo
func validate_entry_points{range_check_ptr}(
    n_entry_points: felt, entry_points: CompiledClassEntryPoint*
) {
    // Add: enforce a protocol-defined maximum.
    assert_nn_le(n_entry_points, MAX_ENTRY_POINTS);
    if (n_entry_points == 0) {
        return ();
    }
    return validate_entry_points_inner(...);
}
```

Apply the same guard at the top of `hash_entry_points` in both `poseidon_compiled_class_hash.cairo` and `blake_compiled_class_hash.cairo`. Define `MAX_ENTRY_POINTS` as a constant in `constants.cairo`, consistent with the Sierra compiler's own limits.

---

### Proof of Concept

1. A class declarer crafts a `CompiledClass` with `n_external_functions = 2**40` (or any value exceeding the block step budget divided by the per-iteration step cost).
2. The declare transaction is submitted and included in a block by the sequencer (which has no OS-enforced ceiling to reject it).
3. The OS calls `validate_compiled_class_facts` → `validate_entry_points` → `validate_entry_points_inner`, recursing `2**40` times.
4. Simultaneously, `blake_compiled_class_hash` → `hash_entry_points` → `hash_entry_points_inner` recurses the same number of times.
5. The combined step count far exceeds the proof system's budget; proof generation aborts.
6. The block cannot be proven; the network cannot advance to the next block — total network shutdown. [7](#0-6) [8](#0-7) [9](#0-8)

### Citations

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/contract_class/compiled_class.cairo (L22-51)
```text
func validate_entry_points{range_check_ptr}(
    n_entry_points: felt, entry_points: CompiledClassEntryPoint*
) {
    if (n_entry_points == 0) {
        return ();
    }

    return validate_entry_points_inner(
        n_entry_points=n_entry_points - 1,
        entry_points=&entry_points[1],
        prev_selector=entry_points[0].selector,
    );
}

// Inner function for validate_entry_points.
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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/contract_class/compiled_class.cairo (L99-137)
```text
func validate_compiled_class_facts{poseidon_ptr: PoseidonBuiltin*, range_check_ptr}(
    n_compiled_class_facts, compiled_class_facts: CompiledClassFact*, builtin_costs: felt*
) {
    if (n_compiled_class_facts == 0) {
        return ();
    }
    alloc_locals;

    let compiled_class_fact = &compiled_class_facts[0];
    let compiled_class = compiled_class_fact.compiled_class;

    validate_entry_points(
        n_entry_points=compiled_class.n_external_functions,
        entry_points=compiled_class.external_functions,
    );

    validate_entry_points(
        n_entry_points=compiled_class.n_l1_handlers, entry_points=compiled_class.l1_handlers
    );
    // Compiled classes are expected to end with a `ret` opcode followed by a pointer to the
    // builtin costs.
    assert compiled_class.bytecode_ptr[compiled_class.bytecode_length] = 0x208b7fff7fff7ffe;
    assert compiled_class.bytecode_ptr[compiled_class.bytecode_length + 1] = cast(
        builtin_costs, felt
    );

    // Calculate the compiled class hash.
    // This hint enters a new scope that contains the bytecode segment structure of the class.
    %{ EnterScopeWithBytecodeSegmentStructure %}
    let (hash) = blake_compiled_class_hash(compiled_class, full_contract=FALSE);
    %{ LoadClass %}

    assert compiled_class_fact.hash = hash;

    return validate_compiled_class_facts(
        n_compiled_class_facts=n_compiled_class_facts - 1,
        compiled_class_facts=&compiled_class_facts[1],
        builtin_costs=builtin_costs,
    );
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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/contract_class/blake_compiled_class_hash.cairo (L188-219)
```text
func hash_entry_points{hash_state: HashState, range_check_ptr: felt}(
    entry_points: CompiledClassEntryPoint*, n_entry_points: felt
) {
    let inner_hash_state = hash_init();
    hash_entry_points_inner{hash_state=inner_hash_state}(
        entry_points=entry_points, n_entry_points=n_entry_points
    );
    let hash: felt = hash_finalize(hash_state=inner_hash_state);
    hash_update_single(item=hash);

    return ();
}

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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L485-485)
```text
    assert_nn_le(tx_execution_context.calldata_size, SIERRA_ARRAY_LEN_BOUND - 1);
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/constants.cairo (L22-22)
```text
const SIERRA_ARRAY_LEN_BOUND = 4294967296;  // 2^32
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/contract_class/compiled_class_struct.cairo (L15-33)
```text
struct CompiledClass {
    compiled_class_version: felt,

    // The length and pointer to the external entry point table of the contract.
    n_external_functions: felt,
    external_functions: CompiledClassEntryPoint*,

    // The length and pointer to the L1 handler entry point table of the contract.
    n_l1_handlers: felt,
    l1_handlers: CompiledClassEntryPoint*,

    // The length and pointer to the constructor entry point table of the contract.
    n_constructors: felt,
    constructors: CompiledClassEntryPoint*,

    // The length and pointer of the bytecode.
    bytecode_length: felt,
    bytecode_ptr: felt*,
}
```
