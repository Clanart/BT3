### Title
Silent Underflow in `sierra_gas_to_steps_gas` Produces Underestimated `proving_gas` Bouncer Weight, Allowing Blocks to Exceed Proving Capacity — (`File: crates/blockifier/src/bouncer.rs`)

---

### Summary

In `sierra_gas_to_steps_gas`, when the computed `cairo_primitives_gas` exceeds `sierra_gas`, a `checked_sub` underflow is silently absorbed by returning `GasAmount::ZERO` (with only a `log::debug!` message). This zero is then used as the `steps_proving_gas` component in `proving_gas_from_cairo_primitives_and_sierra_gas`, causing `total_proving_gas` in the bouncer weights to be underestimated by the entire steps-gas contribution. The bouncer then admits transactions whose true proving cost exceeds the block's `block_max_capacity.proving_gas` limit, producing a block that cannot be proven within the configured budget.

---

### Finding Description

**Root cause — `sierra_gas_to_steps_gas` (bouncer.rs:786–802)**

```rust
pub fn sierra_gas_to_steps_gas(
    sierra_gas: GasAmount,
    cairo_primitives_counters: &CairoPrimitiveCounterMap,
    sierra_builtin_gas_costs: &BuiltinGasCosts,
) -> GasAmount {
    let cairo_primitives_gas =
        cairo_primitives_to_gas(cairo_primitives_counters, sierra_builtin_gas_costs);

    sierra_gas.checked_sub(cairo_primitives_gas).unwrap_or_else(|| {
        log::debug!("Sierra gas underflow: ...");
        GasAmount::ZERO          // ← steps component silently dropped
    })
}
``` [1](#0-0) 

The invariant the function relies on is `sierra_gas ≥ cairo_primitives_gas` (i.e., the total sierra gas covers both the steps component and the builtins component). When that invariant is violated the subtraction underflows. Instead of propagating an error, the function returns `GasAmount::ZERO`, silently discarding the steps-gas contribution.

**Propagation into `proving_gas_from_cairo_primitives_and_sierra_gas` (bouncer.rs:753–765)**

```rust
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
``` [2](#0-1) 

When `steps_proving_gas = 0` (due to the silent underflow), the returned value is `cairo_primitives_proving_gas` alone — the entire steps-gas contribution to proving cost is missing.

**Effect on `compute_proving_gas` and `get_tx_weights` (bouncer.rs:875–992)**

`compute_proving_gas` calls `proving_gas_from_cairo_primitives_and_sierra_gas` with `vm_resources_sierra_gas` and the aggregated `cairo_primitives_for_proving_gas`. The underestimated result flows directly into `BouncerWeights::proving_gas`. [3](#0-2) [4](#0-3) 

**Bouncer admission check (bouncer.rs:667–682)**

`try_update` adds the (underestimated) `tx_bouncer_weights` to the accumulated block weights and checks `bouncer_config.has_room(next_accumulated_weights)`. Because `proving_gas` is too low, the check passes for transactions that would actually push the block over the proving-gas budget. [5](#0-4) 

**Analog to the external `withdrawInterest` bug**

| External bug | Sequencer analog |
|---|---|
| `require(amount ≤ totalInterest + totalInterestFromLiquidation)` passes | `sierra_gas` is supposed to cover `steps_gas + builtins_gas` |
| `totalInterest -= amount` underflows when `totalInterest < amount` | `sierra_gas.checked_sub(cairo_primitives_gas)` underflows when `cairo_primitives_gas > sierra_gas` |
| Underflow reverts (DoS) | Underflow silently returns `ZERO` (wrong value propagated) |
| Only one pool decremented | Only `cairo_primitives_proving_gas` counted; `steps_proving_gas` zeroed out |

**When can the invariant be violated?**

`vm_resources_sierra_gas` is computed as:

```
extended_execution_resources_to_gas(vm_resources, sierra_costs, vc)
  + tx_resources.computation.sierra_gas
``` [6](#0-5) 

`cairo_primitives_counters` for proving gas aggregates patricia-update builtins, OS-VM builtins, **and** `tx_cairo_primitives_counters` (which includes opcodes such as `blake`): [7](#0-6) 

If `tx_cairo_primitives_counters` contains opcodes (e.g., `blake`) whose sierra cost is not fully reflected in `vm_resources_sierra_gas` (because the opcode cost is tracked in the Sierra gas counter separately from the VM builtin counter), `cairo_primitives_to_gas(cairo_primitives_counters, sierra_costs)` can exceed `vm_resources_sierra_gas`, triggering the underflow. The code itself acknowledges this is reachable via the `log::debug!` guard.

---

### Impact Explanation

The `BouncerWeights::proving_gas` field stored in `accumulated_weights` is the authoritative resource-accounting value used to decide whether a block has room for more transactions. [8](#0-7) 

When `proving_gas` is underestimated, the bouncer's `has_room` check passes for transactions that would actually push the block over `block_max_capacity.proving_gas` (default 5 × 10⁹ gas units in production config). [9](#0-8) 

The resulting block carries more proving work than the prover budget allows, which is an **incorrect bouncer/resource accounting result with direct economic impact** (delayed or failed proving, potential sequencer penalties, or forced block re-sequencing).

---

### Likelihood Explanation

The underflow path is explicitly acknowledged in the production code with a `log::debug!` message, confirming the developers know it is reachable. Any transaction whose `cairo_primitives_counters` (including opcode entries such as `blake`) produce a sierra-cost sum exceeding `vm_resources_sierra_gas` will trigger it. A transaction that uses many `blake` opcodes (which have a high sierra gas cost per unit) relative to its step count is a concrete trigger. No privileged access is required; any user-submitted transaction can reach `try_update`.

---

### Recommendation

Replace the silent `GasAmount::ZERO` fallback with one of:

1. **Saturating at zero with a warning/metric** — acceptable only if the invariant is expected to be violated by design (e.g., rounding), but the `proving_gas` must then be computed via an alternative formula that does not lose the steps contribution.
2. **Propagate an error** — return `Err` from `sierra_gas_to_steps_gas` and let `get_tx_weights` / `try_update` reject the transaction.
3. **Clamp and compensate** — if `cairo_primitives_gas > sierra_gas`, set `steps_proving_gas = 0` but add the deficit `(cairo_primitives_gas - sierra_gas)` back into the proving-gas estimate so the total is never underestimated.

The analogous correct pattern (from the external report's mitigation) is sequential/proportional deduction rather than silent truncation.

---

### Proof of Concept

1. Configure a block with `block_max_capacity.proving_gas = P`.
2. Submit a transaction that uses `N` `blake` opcodes such that `N × blake_sierra_cost > vm_resources_sierra_gas` (achievable because `blake_sierra_cost` is large and `vm_resources_sierra_gas` for a minimal-step transaction is small).
3. `sierra_gas_to_steps_gas` returns `GasAmount::ZERO`; `total_proving_gas` = `cairo_primitives_proving_gas` only (missing the steps component, call it `D`).
4. The bouncer records `proving_gas = total_proving_gas` (underestimated by `D`) and admits the transaction.
5. Repeat until the accumulated `proving_gas` in the bouncer reaches `P`, but the true proving cost of the block is `P + k×D` for `k` such transactions.
6. The block is sealed with a true proving cost exceeding `P`, violating the `block_max_capacity.proving_gas` invariant.

### Citations

**File:** crates/blockifier/src/bouncer.rs (L155-168)
```rust
pub struct BouncerWeights {
    pub l1_gas: usize,
    pub message_segment_length: usize,
    pub n_events: usize,
    pub state_diff_size: usize,
    pub sierra_gas: GasAmount,
    pub n_txs: usize,
    pub proving_gas: GasAmount,
    /// Receipt-based L2 gas, including execution gas + state allocation costs + DA costs.
    /// Used to close blocks on the economic gas metric. Diverges from sierra_gas because
    /// it includes allocation_cost for new storage keys and other non-execution costs.
    // NOTE: Must stay in sync with orchestrator_versioned_constants' max_block_size.
    pub receipt_l2_gas: GasAmount,
}
```

**File:** crates/blockifier/src/bouncer.rs (L667-682)
```rust
        let next_accumulated_weights =
            self.get_bouncer_weights().checked_add(tx_bouncer_weights).expect(&err_msg);
        if !self.bouncer_config.has_room(next_accumulated_weights) {
            let exceeded_weights =
                self.bouncer_config.get_exceeded_weights(next_accumulated_weights);
            log::debug!(
                "Transaction cannot be added to the current block, block capacity reached; \
                 transaction weights: {:?}, block weights: {:?}. Block max capacity reached on \
                 fields: {}",
                tx_weights.bouncer_weights,
                self.get_bouncer_weights(),
                exceeded_weights
            );
            record_exceeded_bouncer_resources(&exceeded_weights);
            Err(TransactionExecutorError::BlockFull)?
        }
```

**File:** crates/blockifier/src/bouncer.rs (L753-765)
```rust
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

**File:** crates/blockifier/src/bouncer.rs (L786-802)
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
}
```

**File:** crates/blockifier/src/bouncer.rs (L853-873)
```rust
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

**File:** crates/blockifier/src/bouncer.rs (L875-900)
```rust
fn compute_proving_gas(
    cairo_primitives_counters: &CairoPrimitiveCounterMap,
    vm_resources_sierra_gas: GasAmount,
    versioned_constants: &VersionedConstants,
    proving_builtin_gas_costs: &BuiltinGasCosts,
    sierra_builtin_gas_costs: &BuiltinGasCosts,
    migration_gas: GasAmount,
    class_hash_to_casm_hash_computation_resources: &HashMap<ClassHash, ExtendedExecutionResources>,
) -> (GasAmount, CasmHashComputationData) {
    let vm_resources_proving_gas = proving_gas_from_cairo_primitives_and_sierra_gas(
        vm_resources_sierra_gas,
        cairo_primitives_counters,
        proving_builtin_gas_costs,
        sierra_builtin_gas_costs,
    );

    let proving_gas_without_casm_hash_computation =
        vm_resources_proving_gas.checked_add_panic_on_overflow(migration_gas);

    add_casm_hash_computation_gas_cost(
        class_hash_to_casm_hash_computation_resources,
        proving_gas_without_casm_hash_computation,
        proving_builtin_gas_costs,
        versioned_constants,
    )
}
```

**File:** crates/blockifier/src/bouncer.rs (L983-992)
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

**File:** crates/blockifier/src/bouncer.rs (L1007-1017)
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

**File:** crates/apollo_node/resources/config_schema.json (L97-101)
```json
  "batcher_config.static_config.block_builder_config.bouncer_config.block_max_capacity.proving_gas": {
    "description": "An upper bound on the total builtins and steps gas usage used in a block.",
    "privacy": "Public",
    "value": 5000000000
  },
```
