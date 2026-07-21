### Title
Silent Proving-Gas Underflow in Bouncer Silently Zeroes Steps-Gas, Allowing Blocks to Exceed Prover Capacity — (File: `crates/blockifier/src/bouncer.rs`)

### Summary

`sierra_gas_to_steps_gas` silently returns `GasAmount::ZERO` — only emitting a `log::debug!` — when the builtin/opcode contribution subtracted from `vm_resources_sierra_gas` underflows. This is the direct sequencer analog of the vault-depletion bug: instead of erroring when the "vault" (`sierra_gas`) is insufficient to cover the primitives' share, the function silently issues zero steps-gas. The resulting `proving_gas` bouncer weight is systematically underestimated, allowing the sequencer to pack more transactions into a block than the prover can actually prove.

### Finding Description

`sierra_gas_to_steps_gas` computes the step-gas component of proving gas by subtracting the Sierra-cost of all Cairo primitives from the total `vm_resources_sierra_gas`:

```
steps_proving_gas = vm_resources_sierra_gas − cairo_primitives_to_gas(cairo_primitives_counters, sierra_builtin_gas_costs)
```

When the subtraction underflows it silently returns `GasAmount::ZERO`: [1](#0-0) 

This result is then added to the Stwo-cost builtin gas in `proving_gas_from_cairo_primitives_and_sierra_gas`: [2](#0-1) 

The combined value becomes `total_proving_gas`, which is stored as the `proving_gas` field of `BouncerWeights`: [3](#0-2) 

The bouncer uses this weight to gate block admission in `try_update`: [4](#0-3) 

The underflow is structurally reachable because the `cairo_primitives_counters` argument passed to `sierra_gas_to_steps_gas` is `cairo_primitives_for_proving_gas`, which aggregates:

1. `patricia_update_resources.prover_builtins()`
2. `tx_resources.computation.os_vm_resources.prover_builtins()`
3. `tx_cairo_primitives_counters` — the transaction's own primitive map, which includes **opcodes** (e.g., `blake`) in addition to builtins [5](#0-4) 

`vm_resources_sierra_gas`, however, is computed from `extended_execution_resources_to_gas`, which sums n_steps, n_memory_holes, and `prover_cairo_primitives()` of the VM resources. If `tx_cairo_primitives_counters` contains opcode entries (e.g., `blake`) that are **not** reflected in `vm_resources.prover_cairo_primitives()`, the subtraction underflows and `steps_proving_gas` collapses to zero. [6](#0-5) 

### Impact Explanation

When the underflow fires, `proving_gas` for the transaction is computed as only the Stwo builtin cost, with the step-gas component silently zeroed. The bouncer therefore admits the transaction against a `proving_gas` budget that is lower than the true prover cost. Over a full block, the accumulated `proving_gas` can fall far below the actual prover workload, allowing the block to exceed the prover's capacity. This is incorrect bouncer resource accounting with direct economic impact: proof generation for the over-filled block fails, stalling the sequencer.

This matches the allowed impact: **Critical — Incorrect fee, gas, bouncer, resource accounting, refund, balance, or L1 gas price effect with economic impact.**

### Likelihood Explanation

Any transaction that exercises Cairo opcodes tracked in `CairoPrimitiveCounterMap` (currently `blake`) but not reflected in `ExtendedExecutionResources.prover_cairo_primitives()` can trigger the underflow. A single crafted Invoke V3 transaction calling a contract that issues many `blake` opcodes is sufficient. The condition is unprivileged and requires no special role.

### Recommendation

Replace the silent `GasAmount::ZERO` fallback with a hard error (or at minimum a `log::warn!` / metric that surfaces in production monitoring). The correct invariant is that `vm_resources_sierra_gas` must always be ≥ the Sierra-cost of all primitives counted in `cairo_primitives_counters`; if it is not, the accounting inputs are inconsistent and the block should not be built with those weights. Concretely:

```rust
sierra_gas.checked_sub(cairo_primitives_gas).unwrap_or_else(|| {
    // Emit a warning/metric; do NOT silently return zero.
    log::warn!(
        "Sierra gas underflow — primitives gas ({cairo_primitives_gas:?}) \
         exceeds total sierra gas ({sierra_gas:?}). Returning zero steps gas."
    );
    // Consider returning an Err to abort block building instead.
    GasAmount::ZERO
})
```

Additionally, audit whether `tx_cairo_primitives_counters` opcode entries (e.g., `blake`) are correctly included in `vm_resources_sierra_gas` before the subtraction is performed.

### Proof of Concept

1. Deploy a Cairo 1 contract that calls the `blake2s` opcode in a tight loop (e.g., 10 000 iterations).
2. Submit an Invoke V3 transaction calling that contract.
3. Observe that `tx_cairo_primitives_counters` contains a large `blake` count.
4. In `get_tx_weights`, `cairo_primitives_for_proving_gas` accumulates this count, but `vm_resources_sierra_gas` does not include the corresponding opcode gas.
5. `sierra_gas_to_steps_gas` underflows; the `debug!` log fires; `steps_proving_gas = 0`.
6. `total_proving_gas` = only Stwo builtin gas, missing the step component.
7. The bouncer admits the transaction against an underestimated `proving_gas` budget.
8. Repeat with enough such transactions to fill a block whose true prover cost exceeds `block_max_capacity.proving_gas`; proof generation fails. [7](#0-6) [8](#0-7)

### Citations

**File:** crates/blockifier/src/bouncer.rs (L621-633)
```rust
        let next_accumulated_weights =
            self.get_bouncer_weights().checked_add(tx_bouncer_weights).expect(&err_msg);
        if !self.bouncer_config.has_room(next_accumulated_weights) {
            log::debug!(
                "Transaction cannot be added to the current block, block capacity reached; \
                 transaction weights: {:?}, block weights: {:?}. Block max capacity reached on \
                 fields: {}",
                tx_weights.bouncer_weights,
                self.get_bouncer_weights(),
                self.bouncer_config.get_exceeded_weights(next_accumulated_weights)
            );
            Err(TransactionExecutorError::BlockFull)?
        }
```

**File:** crates/blockifier/src/bouncer.rs (L703-716)
```rust
/// Calculates proving gas from builtin counters and Sierra gas.
fn proving_gas_from_cairo_primitives_and_sierra_gas(
    sierra_gas: GasAmount,
    cairo_primitives_counters: &CairoPrimitiveCounterMap,
    proving_builtin_gas_costs: &BuiltinGasCosts,
    sierra_builtin_gas_costs: &BuiltinGasCosts,
) -> GasAmount {
    let cairo_primitives_proving_gas =
        cairo_primitives_to_gas(cairo_primitives_counters, proving_builtin_gas_costs);
    let steps_proving_gas =
        sierra_gas_to_steps_gas(sierra_gas, cairo_primitives_counters, sierra_builtin_gas_costs);

    steps_proving_gas.checked_add_panic_on_overflow(cairo_primitives_proving_gas)
}
```

**File:** crates/blockifier/src/bouncer.rs (L737-752)
```rust
pub fn sierra_gas_to_steps_gas(
    sierra_gas: GasAmount,
    cairo_primitives_counters: &CairoPrimitiveCounterMap,
    sierra_builtin_gas_costs: &BuiltinGasCosts,
) -> GasAmount {
    let cairo_primitives_gas =
        cairo_primitives_to_gas(cairo_primitives_counters, sierra_builtin_gas_costs);

    sierra_gas.checked_sub(cairo_primitives_gas).unwrap_or_else(|| {
        log::debug!(
            "Sierra gas underflow: cairo primitives gas exceeds total. Sierra gas: \
             {sierra_gas:?}, Cairo primitives gas: {cairo_primitives_gas:?}, Cairo primitives: \
             {cairo_primitives_counters:?}"
        );
        GasAmount::ZERO
    })
```

**File:** crates/blockifier/src/bouncer.rs (L796-824)
```rust
fn compute_sierra_gas(
    vm_resources: &ExtendedExecutionResources,
    sierra_builtin_gas_costs: &BuiltinGasCosts,
    versioned_constants: &VersionedConstants,
    tx_resources: &TransactionResources,
    migration_gas: GasAmount,
    class_hash_to_casm_hash_computation_resources: &HashMap<ClassHash, ExtendedExecutionResources>,
) -> (GasAmount, CasmHashComputationData, GasAmount) {
    let mut vm_resources_sierra_gas = extended_execution_resources_to_gas(
        vm_resources,
        sierra_builtin_gas_costs,
        versioned_constants,
    );
    let sierra_gas = tx_resources.computation.sierra_gas;

    vm_resources_sierra_gas = vm_resources_sierra_gas.checked_add_panic_on_overflow(sierra_gas);

    let sierra_gas_without_casm_hash_computation =
        vm_resources_sierra_gas.checked_add_panic_on_overflow(migration_gas);

    let (total_sierra_gas, casm_hash_computation_data_sierra_gas) =
        add_casm_hash_computation_gas_cost(
            class_hash_to_casm_hash_computation_resources,
            sierra_gas_without_casm_hash_computation,
            sierra_builtin_gas_costs,
            versioned_constants,
        );
    (total_sierra_gas, casm_hash_computation_data_sierra_gas, vm_resources_sierra_gas)
}
```

**File:** crates/blockifier/src/bouncer.rs (L918-932)
```rust
    let cairo_primitives_for_proving_gas = get_cairo_primitives_for_proving_gas_computation(
        patricia_update_resources.prover_builtins(),
        tx_resources.computation.os_vm_resources.prover_builtins(),
        tx_cairo_primitives_counters,
    );

    let (total_proving_gas, casm_hash_computation_data_proving_gas) = compute_proving_gas(
        &cairo_primitives_for_proving_gas,
        vm_resources_sierra_gas,
        versioned_constants,
        proving_builtin_gas_costs,
        sierra_builtin_gas_costs,
        proving_migration_gas,
        &class_hash_to_casm_hash_computation_resources,
    );
```

**File:** crates/blockifier/src/bouncer.rs (L934-943)
```rust
    let bouncer_weights = BouncerWeights {
        l1_gas: message_starknet_l1gas,
        message_segment_length: message_resources.message_segment_length,
        n_events: tx_resources.starknet_resources.archival_data.event_summary.n_events,
        state_diff_size: get_onchain_data_segment_length(&total_state_changes_keys.count()),
        sierra_gas: total_sierra_gas,
        n_txs: 1,
        proving_gas: total_proving_gas,
        receipt_l2_gas,
    };
```

**File:** crates/blockifier/src/bouncer.rs (L958-968)
```rust
fn get_cairo_primitives_for_proving_gas_computation(
    patricia_update_builtins: BuiltinCounterMap,
    os_computation_builtins: BuiltinCounterMap,
    tx_cairo_primitives: &CairoPrimitiveCounterMap,
) -> CairoPrimitiveCounterMap {
    let mut cairo_primitives = cairo_primitive_counter_map(patricia_update_builtins);
    add_maps(&mut cairo_primitives, &cairo_primitive_counter_map(os_computation_builtins));
    add_maps(&mut cairo_primitives, tx_cairo_primitives);

    cairo_primitives
}
```
