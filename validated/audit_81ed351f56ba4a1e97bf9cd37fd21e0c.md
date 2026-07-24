### Title
One Failing Kickoff/Round State Machine Duty Aborts All State Machine Processing and Empties In-Memory State - (File: core/src/states/mod.rs)

### Summary

`process_block_parallel` in `StateManager` processes all kickoff and round state machines per Bitcoin block. If any single state machine's duty (watchtower challenge, disprove, operator assert) captures an error via `capture_error`, the function returns early. Because `update_machines` calls `std::mem::take` on `self.kickoff_machines` and `self.round_machines` before the error check, the early return leaves both vecs permanently empty in memory. All other state machines — including those tracking unrelated kickoffs — are silently dropped, permanently blocking watchtower challenges and disproves for every active kickoff.

### Finding Description

`update_machines` empties `self.kickoff_machines` and `self.round_machines` via `std::mem::take`:

```rust
for machine in std::mem::take(machines).into_iter() { ... }
```

The machines are moved into local variables `final_kickoff_machines` / `kickoff_futures`. After executing all futures in parallel, `process_block_parallel` checks for captured errors:

```rust
let mut all_errors = Vec::new();
for ctx in kickoff_contexts.iter_mut().chain(round_contexts.iter_mut()) {
    all_errors.extend(std::mem::take(&mut ctx.errors));
}
if !all_errors.is_empty() {
    return Err(eyre::eyre!(
        "Multiple errors occurred during state processing: {:?}",
        all_errors
    ));
}
```

This early return drops `final_kickoff_machines`, `changed_kickoff_machines`, and all futures. The lines that restore the machines:

```rust
self.round_machines = final_round_machines;
self.kickoff_machines = final_kickoff_machines;
self.next_height_to_process = max(block_height + 1, self.next_height_to_process);
```

are never reached. After the failure, `self.kickoff_machines` and `self.round_machines` are empty. All subsequent calls to `process_block_parallel` process zero machines and silently succeed, advancing `next_height_to_process` past the blocks where challenges and disproves were due.

The errors originate from `capture_error` calls inside kickoff state machine handlers such as `disprove_if_ready`, `send_operator_asserts_if_ready`, and `create_matcher_for_latest_blockhash_if_ready`. Any transient Bitcoin RPC failure, DB error, or persistent bad state in one kickoff machine propagates to block all others.

### Impact Explanation

- All N kickoff state machines are dropped from memory after a single duty failure in any one of them.
- Watchtower challenge windows and disprove windows for all other kickoffs are missed.
- A malicious operator whose kickoff is being tracked alongside a kickoff that causes a persistent duty error (e.g., a UTXO already spent, a malformed witness, a persistent RPC failure on one duty path) can prevent the watchtower from ever sending a disprove transaction.
- Without a disprove, the operator's fraudulent BitVM assertion goes unchallenged, allowing theft of the bridged BTC collateral.
- The bridge liveness invariant — that at least one honest verifier can always challenge and disprove — is broken for all active kickoffs simultaneously.

### Likelihood Explanation

- Any transient Bitcoin node RPC error during a duty dispatch (broadcasting a watchtower challenge or disprove tx) triggers the bug.
- Production deployments regularly experience transient RPC failures.
- A persistent error (e.g., a UTXO already spent by the operator before the state machine tries to use it) causes the state manager to permanently lose all machines until a manual restart and DB reload.
- No privileged access is required: the trigger is an infrastructure-level failure or a state inconsistency that any on-chain event can induce.

### Recommendation

Restore `self.kickoff_machines` and `self.round_machines` before returning on error, or restructure `process_block_parallel` to not use `std::mem::take` until after the error check. Concretely:

1. Collect errors but do not return early; log them and continue advancing the remaining machines.
2. Only remove or quarantine the specific state machine(s) that produced errors, not all machines.
3. Ensure `self.kickoff_machines = final_kickoff_machines` and `self.round_machines = final_round_machines` are executed unconditionally before any error propagation, mirroring the M-08 fix of "return instead of revert so other items can still be updated."

### Proof of Concept

1. State manager holds kickoff machines K1 (operator A, legitimate) and K2 (operator B, whose disprove duty fails due to a transient RPC error).
2. A new Bitcoin block arrives; `NewFinalizedBlock` is dispatched.
3. `process_block_parallel` calls `update_machines`, emptying `self.kickoff_machines` via `std::mem::take`.
4. Both K1 and K2 are processed in parallel. K2's `disprove_if_ready` calls `context.capture_error(...)`, which captures the RPC error.
5. After `join_all`, `all_errors` is non-empty. `process_block_parallel` returns `Err(...)`.
6. `final_kickoff_machines` (containing K1) is dropped. `self.kickoff_machines` remains empty.
7. The next `NewFinalizedBlock` event processes zero machines. `next_height_to_process` advances past K1's disprove window.
8. Operator A's fraudulent assertion goes undisproved. Operator A claims the collateral. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

**File:** core/src/states/mod.rs (L500-525)
```rust
    fn update_machines<'a, M>(
        machines: &mut Vec<InitializedStateMachine<M>>,
        base_context: &'a context::StateContext<T>,
    ) -> (
        Vec<InitializedStateMachine<M>>,
        Vec<
            impl Future<Output = (InitializedStateMachine<M>, context::StateContext<T>)> + Send + 'a,
        >,
    )
    where
        M: IntoStateMachine + Send + Sync + 'static,
        M::State: Send + Sync + 'static,
        InitializedStateMachine<M>: ContextProcessor<T, M>,
    {
        let mut unchanged_machines = Vec::new();
        let mut processing_futures = Vec::new();

        for machine in std::mem::take(machines).into_iter() {
            match machine.process_with_ctx(base_context) {
                ContextProcessResult::Processing(future) => processing_futures.push(future),
                ContextProcessResult::Unchanged(machine) => unchanged_machines.push(machine),
            }
        }

        (unchanged_machines, processing_futures)
    }
```

**File:** core/src/states/mod.rs (L580-615)
```rust
        let (mut final_kickoff_machines, mut kickoff_futures) =
            Self::update_machines(&mut self.kickoff_machines, context);
        let (mut final_round_machines, mut round_futures) =
            Self::update_machines(&mut self.round_machines, context);

        // Here we store number of iterations to detect if the machines do not stabilize after a while
        // to prevent infinite loops. If a matcher is used, it is deleted, but a bug in implementation
        // can technically cause infinite loops.
        let mut iterations = 0;

        // On each iteration, we'll update the changed machines until all machines
        // stabilize in their state.
        while !kickoff_futures.is_empty() || !round_futures.is_empty() {
            // Execute all futures in parallel
            let (kickoff_results, round_results) =
                join(join_all(kickoff_futures), join_all(round_futures)).await;

            // Unzip the results into updated machines and state contexts
            let (mut changed_kickoff_machines, mut kickoff_contexts): (Vec<_>, Vec<_>) =
                kickoff_results.into_iter().unzip();
            let (mut changed_round_machines, mut round_contexts): (Vec<_>, Vec<_>) =
                round_results.into_iter().unzip();

            // Merge and handle errors
            let mut all_errors = Vec::new();
            for ctx in kickoff_contexts.iter_mut().chain(round_contexts.iter_mut()) {
                all_errors.extend(std::mem::take(&mut ctx.errors));
            }

            if !all_errors.is_empty() {
                // Return first error or create a combined error
                return Err(eyre::eyre!(
                    "Multiple errors occurred during state processing: {:?}",
                    all_errors
                ));
            }
```

**File:** core/src/states/mod.rs (L685-692)
```rust
        // Set back the original machines
        self.round_machines = final_round_machines;
        self.kickoff_machines = final_kickoff_machines;

        self.next_height_to_process = max(block_height + 1, self.next_height_to_process);

        Ok(())
    }
```

**File:** core/src/states/context.rs (L217-225)
```rust
    pub async fn capture_error(
        &mut self,
        fnc: impl AsyncFnOnce(&mut Self) -> Result<(), eyre::Report>,
    ) {
        let result = fnc(self).await;
        if let Err(e) = result {
            self.errors.push(e.into());
        }
    }
```
