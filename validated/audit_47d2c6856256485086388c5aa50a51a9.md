[1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

**File:** third_party/move/move-binary-format/src/control_flow_graph.rs (L159-203)
```rust
        while let Some(block) = stack.pop() {
            match exploration.entry(block) {
                Entry::Vacant(entry) => {
                    // Record the fact that exploration of this block and its sub-graph has started.
                    entry.insert(Exploration::InProgress);

                    // Push the block back on the stack to finish processing it, and mark it as done
                    // once its sub-graph has been traversed.
                    stack.push(block);

                    for succ in &blocks[&block].successors {
                        match exploration.get(succ) {
                            // This successor has never been visited before, add it to the stack to
                            // be explored before `block` gets marked `Done`.
                            None => stack.push(*succ),

                            // This block's sub-graph was being explored, meaning it is a (reflexive
                            // transitive) predecessor of `block` as well as being a successor,
                            // implying a loop has been detected -- greedily choose the successor
                            // block as the loop head.
                            Some(Exploration::InProgress) => {
                                loop_heads.entry(*succ).or_default().insert(block);
                            },

                            // Cross-edge detected, this block and its entire sub-graph (modulo
                            // cycles) has already been explored via a different path, and is
                            // already present in `post_order`.
                            Some(Exploration::Done) => { /* skip */ },
                        };
                    }
                },

                Entry::Occupied(mut entry) => match entry.get() {
                    // Already traversed the sub-graph reachable from this block, so skip it.
                    Exploration::Done => continue,

                    // Finish up the traversal by adding this block to the post-order traversal
                    // after its sub-graph (modulo cycles).
                    Exploration::InProgress => {
                        post_order.push(block);
                        entry.insert(Exploration::Done);
                    },
                },
            }
        }
```

**File:** third_party/move/move-bytecode-verifier/src/control_flow.rs (L118-163)
```rust
fn verify_reducibility<'a>(
    verifier_config: &VerifierConfig,
    function_view: &'a FunctionView<'a>,
) -> PartialVMResult<()> {
    let current_function = function_view.index().unwrap_or(FunctionDefinitionIndex(0));
    let err = move |code: StatusCode, offset: CodeOffset| {
        Err(PartialVMError::new(code).at_code_offset(current_function, offset))
    };

    let summary = LoopSummary::new(function_view.cfg());
    let mut partition = LoopPartition::new(&summary);

    // Iterate through nodes in reverse pre-order so more deeply nested loops (which would appear
    // later in the pre-order) are processed first.
    for head in summary.preorder().rev() {
        // If a node has no back edges, it is not a loop head, so doesn't need to be processed.
        let back = summary.back_edges(head);
        if back.is_empty() {
            continue;
        }

        // Collect the rest of the nodes in `head`'s loop, in `body`.  Start with the nodes that
        // jump back to the head, and grow `body` by repeatedly following predecessor edges until
        // `head` is found again.

        let mut body = BTreeSet::new();
        for node in back {
            let node = partition.containing_loop(*node);

            if node != head {
                body.insert(node);
            }
        }

        let mut frontier: Vec<_> = body.iter().copied().collect();
        while let Some(node) = frontier.pop() {
            for pred in summary.pred_edges(node) {
                let pred = partition.containing_loop(*pred);

                // `pred` can eventually jump back to `head`, so is part of its body.  If it is not
                // a descendant of `head`, it implies that `head` does not dominate a node in its
                // loop, therefore the CFG is not reducible, according to Property 1 (see doc
                // comment).
                if !summary.is_descendant(/* ancestor */ head, /* descendant */ pred) {
                    return err(StatusCode::INVALID_LOOP_SPLIT, summary.block(pred));
                }
```

**File:** third_party/move/move-bytecode-verifier/src/code_unit_verifier.rs (L169-177)
```rust
        // create `FunctionView` and `BinaryIndexedView`
        let function_view = control_flow::verify_function(
            verifier_config,
            module,
            index,
            function_definition,
            code,
            meter,
        )?;
```

**File:** third_party/move/move-bytecode-verifier/src/code_unit_verifier.rs (L211-225)
```rust
    fn verify_common(
        &self,
        verifier_config: &VerifierConfig,
        meter: &mut impl Meter,
    ) -> PartialVMResult<()> {
        StackUsageVerifier::verify(verifier_config, &self.resolver, &self.function_view, meter)?;
        type_safety::verify(&self.resolver, &self.function_view, meter)?;
        locals_safety::verify(&self.resolver, &self.function_view, meter)?;
        reference_safety::verify(
            &self.resolver,
            &self.function_view,
            self.name_def_map,
            meter,
        )
    }
```
