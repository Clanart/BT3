### Title
Unbounded `sierra_program_length` Iteration in `hash_class_components` Enables OS Prover DoS — (File: `contract_class/contract_class.cairo`)

---

### Summary

The `hash_class_components` function in the StarkNet OS iterates over `contract_class.sierra_program_length` (and `n_external_functions`, `n_l1_handlers`, `n_constructors`) without any upper-bound enforcement. An attacker who submits a `declare` transaction with an extremely large Sierra program can force the OS prover to perform unbounded Poseidon hashing, exhausting prover step capacity and preventing the block from being proved — causing a network halt.

---

### Finding Description

In `contract_class/contract_class.cairo`, the function `hash_class_components` calls `poseidon_hash_many` with lengths taken directly from the attacker-supplied `ContractClass` struct, with no upper-bound assertion:

```cairo
// Hash Sierra program.
let (local sierra_program_hash) = poseidon_hash_many(
    n=contract_class.sierra_program_length, elements=contract_class.sierra_program_ptr
);
``` [1](#0-0) 

The same pattern applies to the entry-point arrays:

```cairo
let (local external_functions_hash) = poseidon_hash_many(
    n=contract_class.n_external_functions * ContractEntryPoint.SIZE,
    elements=contract_class.external_functions,
);
``` [2](#0-1) [3](#0-2) 

None of `sierra_program_length`, `n_external_functions`, `n_l1_handlers`, or `n_constructors` are range-checked against any maximum before being passed as the loop count to `poseidon_hash_many`.

**Contrast with calldata:** The OS *does* enforce an upper bound on calldata length in `execute_entry_point.cairo` using `SIERRA_ARRAY_LEN_BOUND`:

```cairo
assert [range_check_ptr] = calldata_size;
assert [range_check_ptr + 1] = calldata_size + 2 ** 128 - SIERRA_ARRAY_LEN_BOUND;
``` [4](#0-3) 

No equivalent guard exists for contract class field lengths. The `CompiledClass` struct similarly exposes `bytecode_length` without a bound: [5](#0-4) 

And `validate_compiled_class_facts` recurses over `n_compiled_class_facts` and calls `blake_compiled_class_hash` → `bytecode_hash_internal_node`, which iterates over `data_length` without an upper bound: [6](#0-5) [7](#0-6) 

---

### Impact Explanation

The StarkNet OS program is executed by the prover to generate a validity proof for each block. Cairo proofs have a hard cap on the number of VM steps and builtin invocations. If `sierra_program_length` (or `bytecode_length`) is set to a value large enough that the resulting `poseidon_hash_many` loop exhausts the prover's step budget, the proof generation fails. A block that cannot be proved cannot be finalized. If the sequencer has already committed to that block, the network stalls — matching the **High: Network not being able to confirm new transactions (total network shutdown)** impact category.

---

### Likelihood Explanation

A `declare` transaction is submitted by any unprivileged user. The OS program is the authoritative on-chain validator; if it does not enforce a bound, the bound is not enforced at the protocol level. Even if a gateway applies an off-chain size limit, the OS's lack of an in-circuit assertion means:

1. A sequencer that relaxes or bypasses gateway filtering (e.g., a future sequencer implementation, a permissionless sequencer in a decentralized setting) would pass the oversized class through.
2. The OS would then attempt to hash an unbounded number of field elements, exhausting prover resources.

The asymmetry — calldata is bounded by `SIERRA_ARRAY_LEN_BOUND` in the OS, but Sierra program length is not — confirms this is an oversight rather than an intentional design choice.

---

### Recommendation

Add an upper-bound range check on `sierra_program_length`, `n_external_functions`, `n_l1_handlers`, `n_constructors`, and `bytecode_length` inside `hash_class_components` and `validate_compiled_class_facts`, mirroring the existing `SIERRA_ARRAY_LEN_BOUND` pattern used for calldata in `execute_entry_point.cairo`. For example:

```cairo
// Before calling poseidon_hash_many:
assert [range_check_ptr] = contract_class.sierra_program_length;
assert [range_check_ptr + 1] = contract_class.sierra_program_length + 2 ** 128 - MAX_SIERRA_PROGRAM_LENGTH;
let range_check_ptr = range_check_ptr + 2;
```

Define `MAX_SIERRA_PROGRAM_LENGTH` (and analogous constants for entry-point counts and bytecode length) in `constants.cairo`, consistent with the protocol's declared class-size limits.

---

### Proof of Concept

1. Construct a `ContractClass` with `sierra_program_length = L` where `L` is chosen so that `poseidon_hash_many(n=L, ...)` requires more Cairo VM steps than the prover's block step budget.
2. Submit a `declare` transaction carrying this class. The OS's `hash_class_components` function accepts `L` without assertion.
3. The sequencer includes the transaction in a block.
4. The prover invokes the OS program; `poseidon_hash_many` loops `L` times, exhausting the step budget.
5. Proof generation fails; the block cannot be finalized; the network halts. [8](#0-7)

### Citations

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/contract_class/contract_class.cairo (L55-93)
```text
func hash_class_components{poseidon_ptr: PoseidonBuiltin*}(
    contract_class: ContractClass*
) -> ContractClassComponentHashes* {
    alloc_locals;
    assert contract_class.contract_class_version = CONTRACT_CLASS_VERSION;

    // Hash external entry points.
    let (local external_functions_hash) = poseidon_hash_many(
        n=contract_class.n_external_functions * ContractEntryPoint.SIZE,
        elements=contract_class.external_functions,
    );

    // Hash L1 handler entry points.
    let (local l1_handlers_hash) = poseidon_hash_many(
        n=contract_class.n_l1_handlers * ContractEntryPoint.SIZE,
        elements=contract_class.l1_handlers,
    );

    // Hash constructor entry points.
    let (local constructors_hash) = poseidon_hash_many(
        n=contract_class.n_constructors * ContractEntryPoint.SIZE,
        elements=contract_class.constructors,
    );

    // Hash Sierra program.
    let (local sierra_program_hash) = poseidon_hash_many(
        n=contract_class.sierra_program_length, elements=contract_class.sierra_program_ptr
    );

    tempvar contract_class_component_hashes = new ContractClassComponentHashes(
        contract_class_version=contract_class.contract_class_version,
        external_functions_hash=external_functions_hash,
        l1_handlers_hash=l1_handlers_hash,
        constructors_hash=constructors_hash,
        abi_hash=contract_class.abi_hash,
        sierra_program_hash=sierra_program_hash,
    );
    return contract_class_component_hashes;
}
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/execute_entry_point.cairo (L222-224)
```text
    assert [range_check_ptr] = calldata_size;
    assert [range_check_ptr + 1] = calldata_size + 2 ** 128 - SIERRA_ARRAY_LEN_BOUND;
    let range_check_ptr = range_check_ptr + 2;
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/contract_class/compiled_class_struct.cairo (L30-33)
```text
    // The length and pointer of the bytecode.
    bytecode_length: felt,
    bytecode_ptr: felt*,
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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/contract_class/blake_compiled_class_hash.cairo (L127-185)
```text
func bytecode_hash_internal_node{range_check_ptr, hash_state: HashState}(
    data_ptr: felt*, data_length: felt, full_contract: felt
) {
    if (data_length == 0) {
        %{ AssertEndOfBytecodeSegments %}
        return ();
    }

    alloc_locals;
    local is_leaf_and_loaded;
    local load_segment;
    local segment_length;

    %{ IterCurrentSegmentInfo %}

    if (is_leaf_and_loaded != FALSE) {
        // Repeat the code of bytecode_hash_node() for performance reasons, instead of calling it.
        let (current_segment_hash) = encode_felt252_data_and_calc_blake_hash(
            data_len=segment_length, data=data_ptr
        );
        tempvar range_check_ptr = range_check_ptr;
        tempvar current_segment_hash = current_segment_hash;
    } else {
        // The segment is at least partially loaded, and it is not a leaf.
        if (load_segment != FALSE) {
            let current_segment_hash = bytecode_hash_node(
                data_ptr=data_ptr, data_length=segment_length, full_contract=full_contract
            );
        } else {
            // If `full_contract` is true, this flow is not allowed.
            assert full_contract = FALSE;

            // Set the first felt of the bytecode to -1 to make sure that the execution cannot jump
            // to this segment (-1 is an invalid opcode).
            // The hash in this case is guessed and the actual bytecode is unconstrained (except for
            // the first felt).
            %{ DeleteMemoryData %}

            assert data_ptr[0] = -1;

            assert [range_check_ptr] = segment_length;
            tempvar range_check_ptr = range_check_ptr + 1;
            let current_segment_hash = [ap];
            %{ SetApToSegmentHashBlake %}
            ap += 1;
        }
    }

    // Add the segment length and hash to the hash state.
    hash_update_single(item=segment_length);
    hash_update_single(item=current_segment_hash);

    %{ vm_exit_scope() %}

    return bytecode_hash_internal_node(
        data_ptr=&data_ptr[segment_length],
        data_length=data_length - segment_length,
        full_contract=full_contract,
    );
```
