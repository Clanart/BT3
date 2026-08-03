Looking at the actual `join` implementation in `AbstractDomain for AbstractState` at `third_party/move/move-bytecode-verifier/src/reference_safety/abstract_state.rs:707-738`, the order of operations is:

```rust
fn join(&mut self, state: &AbstractState, meter: &mut impl Meter) -> PartialVMResult<JoinResult> {
    let joined = Self::join_(self, state);
    assert!(joined.is_canonical());
    assert!(self.locals.len() == joined.locals.len());
    meter.add(Scope::Function, JOIN_BASE_COST)?;
    meter.add_items(Scope::Function, JOIN_PER_LOCAL_COST, self.locals.len())?;
    meter.add_items(
        Scope::Function,
        JOIN_PER_GRAPH_ITEM_COST,
        self.borrow_graph.graph_size(),
    )?;
    ...
    if locals_unchanged && self.borrow_graph.leq(&joined.borrow_graph) {
        Ok(JoinResult::Unchanged)
    } else {
        *self = joined;
        Ok(JoinResult::Changed)
    }
}
``` [1](#0-0) 

The metering call at line 719-723 uses `self.borrow_graph.graph_size()` — and `self` is not reassigned to `joined` until line 734, inside the `else` branch, which executes *after* the metering has already happened. So the claim is confirmed by the code: `graph_size()` used to compute `JOIN_PER_GRAPH_ITEM_COST` reflects the pre-join graph (`self`), not the just-computed `joined` graph.

However, I was unable to locate the definition of `Self::join_` (the actual underlying borrow-graph join function, likely in `move-borrow-graph` crate) within the indexed context in the remaining iterations, so I cannot conclusively determine the direction of the size discrepancy (whether `joined.graph_size()` is reliably ≥ `self.graph_size()`, and by how much) or whether `graph_size()` is cheap/O(1) vs proportional to actual graph complexity. This matters for the impact assessment: if `join_` monotonically increases or unions borrow edges (which is typical of a merge/upper-bound operator in dataflow join semantics), then `joined.graph_size() >= self.graph_size()` in general, meaning the meter systematically undercharges relative to the graph that actually gets stored into `self` afterwards.

### Title
Reference-safety join meters pre-join borrow-graph size instead of the joined graph, permitting metering bypass - (File: `third_party/move/move-bytecode-verifier/src/reference_safety/abstract_state.rs`)

### Summary
In the reference-safety abstract interpreter's `AbstractDomain::join` implementation, `JOIN_PER_GRAPH_ITEM_COST` is charged based on `self.borrow_graph.graph_size()` — evaluated *before* `*self = joined` is executed — while the actually-adopted state is `joined`, whose borrow graph is the result of merging `self` and `state`. Because dataflow joins at CFG merge points are expected to grow or maintain the borrow graph (union of borrow edges from both predecessor states), the metered quantity does not correspond to the graph that is subsequently retained and iterated over in later fixed-point steps.

### Finding Description
`fn join` first computes `let joined = Self::join_(self, state);` without mutating `self`, then charges `JOIN_BASE_COST`, `JOIN_PER_LOCAL_COST * self.locals.len()`, and `JOIN_PER_GRAPH_ITEM_COST * self.borrow_graph.graph_size()` — all against `self`, the pre-join graph. Only in the `else` branch (when the join produced a different state) does `*self = joined` occur, i.e. after metering. [2](#0-1)  Since fixed-point analysis repeatedly re-joins states along back-edges of loops until convergence, and the meter is the sole gate against unbounded borrow-graph blowup (as documented in the accompanying design notes about "metering hooks... for exactly this class of concern" [3](#0-2) ), charging against the pre-join size rather than the post-join size undercounts the complexity that is actually retained for subsequent steps of the analysis.

### Impact Explanation
If confirmed that `join_`'s resulting borrow graph is generally larger than (or a superset of) the pre-join `self` graph — which is the expected semantics of a dataflow join/upper-bound operator — then a crafted module with loops that repeatedly grow the borrow graph via joins (rather than via `Step`, which is separately metered via `STEP_PER_GRAPH_ITEM_COST`) could accumulate borrow-graph complexity while under-billing the `max_per_fun_meter_units` budget, allowing verification to complete (or take asymmetrically long) for graphs that should have hit the cap. Whether this is exploitable to bypass verification of reference-unsafe or excessively-complex bytecode, or is merely a benign one-iteration lag in a monotonically converging fixed point (in which case the final `self.borrow_graph.graph_size()` after fixed point still gets charged on the *next* join call before convergence, self-correcting over iterations), I could not fully verify without inspecting `Self::join_`'s definition and the `BorrowGraph::join`/`graph_size` implementations in the `move-borrow-graph` crate.

### Likelihood Explanation
Unprivileged package/module bytecode fully controls control-flow structure (loop counts, borrow patterns), so an attacker can construct arbitrary CFGs to exercise this join path repeatedly. Reaching this code requires no special privilege — any published/upgraded Move module goes through bytecode verification including reference safety.

### Recommendation
Verify `Self::join_`'s implementation and `BorrowGraph::graph_size()` cost, then confirm whether metering against `joined.borrow_graph.graph_size()` (post-join) rather than `self.borrow_graph.graph_size()` (pre-join) changes worst-case charged totals across a full fixed-point run. If it does, move the `meter.add_items(..., graph_size)` call to use `joined.borrow_graph.graph_size()`, mirroring the ordering fix already applied correctly for `locals_safety`'s analogous join at `third_party/move/move-bytecode-verifier/src/locals_safety/abstract_state.rs:141-165` (there, `JOIN_PER_LOCAL_COST` is charged against `state.local_states.len()`, the incoming operand, not `self`, before computing `joined`).

### Proof of Concept
Not constructed — I lacked access in this session to the `move-borrow-graph::graph::BorrowGraph::join` and `graph_size` implementations needed to build a concrete bytecode sequence demonstrating a measurable charge differential. This would require: (1) confirming `join_`'s graph-merge semantics, (2) confirming `graph_size()`'s cost model, and (3) constructing a loop CFG where predecessor/successor borrow patterns diverge maximally at the join point, then comparing total `meter` units charged across the fixed-point run against the size of the graph retained at convergence. Given the incomplete verification, this should be treated as a **potential but unconfirmed** metering-accounting bug requiring further code review of `move-borrow-graph`, rather than a definitively proven publish-bypass exploit.

### Citations

**File:** third_party/move/move-bytecode-verifier/src/reference_safety/abstract_state.rs (L707-738)
```rust
impl AbstractDomain for AbstractState {
    /// attempts to join state to self and returns the result
    fn join(
        &mut self,
        state: &AbstractState,
        meter: &mut impl Meter,
    ) -> PartialVMResult<JoinResult> {
        let joined = Self::join_(self, state);
        assert!(joined.is_canonical());
        assert!(self.locals.len() == joined.locals.len());
        meter.add(Scope::Function, JOIN_BASE_COST)?;
        meter.add_items(Scope::Function, JOIN_PER_LOCAL_COST, self.locals.len())?;
        meter.add_items(
            Scope::Function,
            JOIN_PER_GRAPH_ITEM_COST,
            self.borrow_graph.graph_size(),
        )?;
        let locals_unchanged = self
            .locals
            .iter()
            .zip(&joined.locals)
            .all(|(self_value, joined_value)| self_value == joined_value);
        // locals unchanged and borrow graph covered, return unchanged
        // else mark as changed and update the state
        if locals_unchanged && self.borrow_graph.leq(&joined.borrow_graph) {
            Ok(JoinResult::Unchanged)
        } else {
            *self = joined;
            Ok(JoinResult::Changed)
        }
    }
}
```

**File:** third_party/move/brainstorming/rustmv.md (L288-293)
```markdown
tractable: the abstract domain remains finite per function (bounded by
locals × lifetime positions), and joins at control-flow merge points work as
today. The existing gas metering must be extended, since lifetime slots
multiply borrow-graph size — the metering hooks
(`REF_PARAM_EDGE_COST` and friends) already exist for exactly this class of
concern.
```
