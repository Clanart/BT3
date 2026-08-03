[1](#0-0) [2](#0-1) [3](#0-2)

### Citations

**File:** third_party/move/move-bytecode-verifier/src/control_flow_v5.rs (L6-10)
```rust
//! This module implements a checker to verify control flow in bytecode version 5 and below. The
//! following properties are ensured:
//! - All forward jumps do not enter into the middle of a loop
//! - All "breaks" (forward, loop-exiting jumps) go to the "end" of the loop
//! - All "continues" (back jumps in a loop) are only to the current loop
```

**File:** third_party/move/move-bytecode-verifier/src/control_flow_v5.rs (L87-103)
```rust
fn instruction_labels(context: &ControlFlowVerifier) -> Vec<Label> {
    let mut labels: Vec<Label> = (0..context.code.len()).map(|_| Label::Code).collect();
    let mut loop_continue = |loop_idx: CodeOffset, last_continue: CodeOffset| {
        labels[loop_idx as usize] = Label::Loop { last_continue }
    };
    for (i, instr) in context.code() {
        match instr {
            // Back jump/"continue"
            Bytecode::Branch(prev) | Bytecode::BrTrue(prev) | Bytecode::BrFalse(prev)
                if is_back_edge(i, *prev) =>
            {
                loop_continue(*prev, i)
            },
            _ => (),
        }
    }
    labels
```

**File:** third_party/move/move-bytecode-verifier/src/control_flow_v5.rs (L111-127)
```rust
fn check_jumps(
    verifier_config: &VerifierConfig,
    context: &ControlFlowVerifier,
    labels: Vec<Label>,
) -> PartialVMResult<()> {
    // All back jumps are only to the current loop
    check_continues(context, &labels)?;
    // All "breaks" go to the "end" of the loop
    check_breaks(context, &labels)?;

    let loop_depth = count_loop_depth(&labels);

    // All forward jumps do not enter into the middle of a loop
    check_no_loop_splits(context, &labels, &loop_depth)?;
    // Nested loops do not exceed a given depth
    check_loop_depth(verifier_config, context, &labels, &loop_depth)
}
```
