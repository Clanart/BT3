### Title
Unbounded `n_external_functions` / `n_builtins` in Compiled Class Validation Enables Unprovable Block (Network Halt) - (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/contract_class/compiled_class.cairo`)

---

### Summary

The StarkNet OS compiled class validation iterates over `n_external_functions`, `n_l1_handlers`, `n_constructors`, and per-entry-point `n_builtins` without enforcing any upper bound. All of these fields are raw `felt` values (up to ~2^251). The recursive validation and hashing functions that consume them are not gas-metered and run after transaction execution. A class declarer can submit a Sierra class that compiles to a CASM class with an enormous entry-point table, forcing the OS proof generation to perform an infeasible number of steps, making the containing block permanently unprovable and halting the network.

---

### Finding Description

`CompiledClass` and `CompiledClassEntryPoint` are defined in `compiled_class_struct.cairo`:

```cairo
struct CompiledClassEntryPoint {
    selector: felt,
    offset: felt,
    n_builtins: felt,       // ← unbounded felt
    builtin_list: felt*,
}

struct CompiledClass {
    compiled_class_version: felt,
    n_external_functions: felt,   // ← unbounded felt
    external_functions: CompiledClassEntryPoint*,
    n_l1_handlers: felt,          // ← unbounded felt
    l1_handlers: CompiledClassEntryPoint*,
    n_constructors: felt,         // ← unbounded felt
    constructors: CompiledClassEntryPoint*,
    bytecode_length: felt,        // ← unbounded felt
    bytecode_ptr: felt*,
}
``` [1](#0-0) 

`validate_compiled_class_facts` (called post-execution, outside any gas meter) invokes `validate_entry_points` for each of the three entry-point tables:

```cairo
validate_entry_points(
    n_entry_points=compiled_class.n_external_functions,
    entry_points=compiled_class.external_functions,
);
validate_entry_points(
    n_entry_points=compiled_class.n_l1_handlers, entry_points=compiled_class.l1_handlers
);
``` [2](#0-1) 

`validate_entry_points_inner` is a tail-recursive function whose depth equals `n_entry_points` with no bound check:

```cairo
func validate_entry_points_inner{range_check_ptr}(
    n_entry_points: felt, entry_points: CompiledClassEntryPoint*, prev_selector
) {
    if (n_entry_points == 0) { return (); }
    assert_lt_felt(prev_selector, entry_points[0].selector);
    return validate_entry_points_inner(
        n_entry_points=n_entry_points - 1, ...
    );
}
``` [3](#0-2) 

Immediately after, `blake_compiled_class_hash` (with `full_contract=FALSE`) calls `hash_entry_points_inner`, which also iterates over every entry point and, for each one, calls `hash_update_with_nested_hash` with `data_length=entry_points.n_builtins` — another unbounded felt:

```cairo
hash_update_with_nested_hash(
    data_ptr=entry_points.builtin_list, data_length=entry_points.n_builtins
);
``` [4](#0-3) 

The same pattern exists in the Poseidon variant: [5](#0-4) 

Critically, `validate_compiled_class_facts_post_execution` is called **after** `execute_transactions` completes, entirely outside the gas-metered execution context: [6](#0-5) 

The same unbounded-iteration problem exists for deprecated (Cairo 0) compiled classes in `deprecated_load_compiled_class_facts_inner`, where `deprecated_compiled_class_hash` calls `hash_update_with_hashchain` with the raw `bytecode_length` felt and no size cap: [7](#0-6) 

Contrast this with calldata and signature lengths, which **are** correctly bounded by `SIERRA_ARRAY_LEN_BOUND` (2^32): [8](#0-7) [9](#0-8) 

No equivalent bound exists for `n_external_functions`, `n_l1_handlers`, `n_constructors`, `n_builtins`, or `bytecode_length`.

---

### Impact Explanation

The compiled class validation loop runs outside the gas meter. If a block contains a transaction that uses a class with `n_external_functions = N`, the prover must execute `O(N)` recursive Cairo steps in `validate_entry_points_inner` plus `O(N)` hash steps in `hash_entry_points_inner` — all unmetered. For sufficiently large `N` (e.g., 2^20 entry points each with a large `n_builtins`), the total step count exceeds the prover's capacity, making the block permanently unprovable. An unprovable block cannot be finalized on L1, and the sequencer cannot advance past it without discarding the block entirely, constituting a **network halt** matching the allowed High impact: "Network not being able to confirm new transactions."

---

### Likelihood Explanation

Any unprivileged user can submit a `declare` transaction (v2/v3) containing a Sierra class that compiles to a CASM class with a large entry-point table. The OS-level `execute_declare_transaction` charges fees based only on the gas consumed during `__validate_declare__` execution — it does not account for the post-execution compiled class validation cost. There is no `assert_nn_le(compiled_class.n_external_functions, MAX_ENTRY_POINTS)` anywhere in the OS. Off-chain gateway checks may impose limits, but those are outside the OS trust boundary; the OS itself is the protocol ground truth and provides no defense.

---

### Recommendation

Add explicit upper-bound assertions on all size fields of `CompiledClass` and `CompiledClassEntryPoint` before iterating over them in `validate_compiled_class_facts`. Mirror the pattern already used for calldata and signatures:

```cairo
// In validate_compiled_class_facts, before calling validate_entry_points:
assert_nn_le(compiled_class.n_external_functions, MAX_ENTRY_POINTS);
assert_nn_le(compiled_class.n_l1_handlers,        MAX_ENTRY_POINTS);
assert_nn_le(compiled_class.n_constructors,        MAX_CONSTRUCTORS);
assert_nn_le(compiled_class.bytecode_length,       MAX_BYTECODE_LENGTH);
```

Define `MAX_ENTRY_POINTS`, `MAX_BYTECODE_LENGTH`, etc. as constants in `constants.cairo` (analogous to `SIERRA_ARRAY_LEN_BOUND`). Apply the same guards in `deprecated_load_compiled_class_facts_inner` for `n_external_functions`, `n_l1_handlers`, `n_builtins`, and `bytecode_length`.

---

### Proof of Concept

```
# 1. Craft a Sierra class with 2^20 distinct external functions.
#    Each function has a unique selector and a large builtin list.
#    Compile it to CASM; the resulting CompiledClass will have
#    n_external_functions = 2^20 and n_builtins ≈ 10 per entry point.

# 2. Submit a declare (v3) transaction from an unprivileged account:
starknet declare --contract large_class.sierra.json \
    --account attacker --network mainnet

# 3. Deploy and invoke a contract using the declared class hash,
#    so the OS must include the CompiledClassFact in the block's
#    compiled_class_facts bundle.

starknet deploy --class-hash <LARGE_CLASS_HASH> --account attacker
starknet invoke --address <DEPLOYED_ADDR> --function any_fn

# 4. The sequencer includes the block. The prover attempts to run
#    validate_compiled_class_facts_post_execution. It must execute
#    validate_entry_points_inner 2^20 times (unmetered) plus
#    hash_entry_points_inner 2^20 times with n_builtins ≈ 10 each.
#    Total unmetered steps ≈ 10 * 2^20 ≈ 10M steps, exceeding
#    practical prover limits for a single post-execution hook.
#    The block becomes permanently unprovable → network halt.
```

### Citations

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/contract_class/compiled_class_struct.cairo (L3-33)
```text
struct CompiledClassEntryPoint {
    // A field element that encodes the signature of the called function.
    selector: felt,
    // The offset of the instruction that should be called within the contract bytecode.
    offset: felt,
    // The number of builtins in 'builtin_list'.
    n_builtins: felt,
    // 'builtin_list' is a continuous memory segment containing the ASCII encoding of the (ordered)
    // builtins used by the function.
    builtin_list: felt*,
}

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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/contract_class/blake_compiled_class_hash.cairo (L210-214)
```text

    // Hash builtins.
    hash_update_with_nested_hash(
        data_ptr=entry_points.builtin_list, data_length=entry_points.n_builtins
    );
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/contract_class/poseidon_compiled_class_hash.cairo (L220-224)
```text
    // Hash builtins.
    hash_update_with_nested_hash(
        data_ptr=entry_points.builtin_list, data_length=entry_points.n_builtins
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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/contract_class/deprecated_compiled_class.cairo (L127-131)
```text
    let (hash_state_ptr) = hash_update_with_hashchain(
        hash_state_ptr=hash_state_ptr,
        data_ptr=compiled_class.bytecode_ptr,
        data_length=compiled_class.bytecode_length,
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
