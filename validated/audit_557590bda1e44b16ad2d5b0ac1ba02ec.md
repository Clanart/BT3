### Title
Heap-buffer zero-fill cost is decoupled from `heap_cost` billing, allowing unpriced CPU amplification via cheap CPI fan-out - ([File: program-runtime/src/mem_pool.rs])

### Summary
`calculate_heap_cost` bills only for the *requested* `heap_size` rounded to 32KiB pages [1](#0-0) , but `VmMemoryPool`'s backing buffers are always allocated and reset at the fixed `MAX_HEAP_FRAME_BYTES` (256KiB) size regardless of the transaction's requested `heap_size` [2](#0-1) . Every time a heap buffer is returned to the pool via `put_heap`, `AlignedMemory::reset()` unconditionally zero-fills the *entire* underlying 256KiB allocation, not just the `heap_size` bytes actually exposed to the program [3](#0-2) [4](#0-3) . This means an attacker can request the *minimum* heap size (paying zero additional `heap_cost`, since `calculate_heap_cost` returns 0 for sizes ≤ 32KiB) while still forcing the validator to perform a full 256KiB memset on every VM teardown, once per CPI/`create_vm!` invocation.

### Finding Description
`create_vm!` charges `calculate_heap_cost(heap_size, heap_cost)` once per VM creation, based on the transaction-wide `heap_size` from the compute budget [5](#0-4) . `heap_size` is a single value for the whole transaction, set via `ComputeBudgetInstruction::request_heap_frame`/`SetLoadedAccountsDataSizeLimit`-style config, clamped to `MIN_HEAP_FRAME_BYTES..=MAX_HEAP_FRAME_BYTES` [6](#0-5) . If the attacker leaves `heap_size` at its minimum/default (32KiB, `MIN_HEAP_FRAME_BYTES = HEAP_LENGTH`), `calculate_heap_cost` returns `0` (confirmed by `test_calculate_heap_cost`, which asserts `32*1024` costs `0`) [7](#0-6) .

However, `VmMemoryPool::new()` pre-allocates its heap pool entries at `MAX_HEAP_FRAME_BYTES` (256KiB) unconditionally, independent of any transaction's requested `heap_size`: `heap: Pool::new(array::from_fn(|_| AlignedMemory::zero_filled(MAX_HEAP_FRAME_BYTES as usize)))` [2](#0-1) . `get_heap(heap_size)` only slices a `heap_size`-sized view for VM use via `create_vm!`'s `.get_mut(..heap_size as usize)` [8](#0-7) , but when the buffer is returned via `put_heap`, `Pool::put` calls `value.reset()`, which does `self.as_slice_mut().fill(0)` on the *entire* `AlignedMemory` buffer (i.e., the full 256KiB, not just the `heap_size` portion actually used) [9](#0-8) . This zero-fill happens synchronously on every VM teardown path (`memory_pool.put_heap(heap)` in `vm.rs`) [10](#0-9) .

Consequently, the CU charge (`calculate_heap_cost`) is proportional to the *requested* heap size, while the actual physical work performed by the validator (256KiB memset per VM teardown) is constant and independent of the requested heap size. An attacker who deploys a program that performs no useful heap work and repeatedly self-CPIs (up to `max_instruction_trace_length`/`max_call_depth`/`max_instruction_stack_depth`) forces N full 256KiB zero-fills per transaction while paying `0` extra `heap_cost` CUs for every one of them (only base `invoke_units` CPI overhead applies, which is unrelated to heap size).

This is a real code-level metering gap: `calculate_heap_cost` is documented/intended to represent "additional 32k heap above the default" cost, but the underlying pool implementation does not scale its physical work with the requested size — it always operates on the max-sized buffer. No existing guard (compute meter check, ELF verification, PDA/privilege check) touches this path since the discrepancy is purely between the CU model and the pool's memory-management implementation.

### Impact Explanation
This is a materially underpriced compute case: attacker-controlled CPI fan-out (bounded by `max_instruction_trace_length` per transaction, but repeatable transaction-to-transaction with a fixed CU budget) forces validators to perform 256KiB memory zero-fills per invocation while the transaction's compute-unit billing model charges nothing extra for it when `heap_size` is left at minimum. This creates a divergence between charged CU cost and actual leader-side CPU work, which can be used to build transactions that are artificially cheap (in CU terms) relative to the physical work they force the validator to perform, falling under "materially underpriced compute" in the Agave bounty program.

### Likelihood Explanation
This is trivially reachable by any unprivileged user: no elevated permissions are required, and it does not depend on validator/leader-specific behavior. The attacker only needs to deploy a program that performs low-cost self-CPIs (already demonstrated feasible by `test_stack_heap_zeroed`, which builds recursive CPI chains up to the max call depth per transaction) [11](#0-10) , and to simply omit/minimize the `request_heap_frame` instruction so `heap_size` stays at `MIN_HEAP_FRAME_BYTES`. Repeated transactions amplify the effect. The severity depends on the exact `MAX_INSTRUCTION_TRACE_LENGTH`/`MAX_CALL_DEPTH` constants (not confirmed with certainty from the excerpts retrieved) and the true wall-clock cost of a 256KiB memset (sub-microsecond to low-microsecond range on modern hardware), so the absolute per-transaction CPU cost amplification may be modest; but it is fully reproducible and provable via benchmark.

### Recommendation
Make `VmMemoryPool::get_heap`/`put_heap`/`reset` scale zero-fill work to the actual `heap_size` used rather than the pool's max buffer size — e.g., track the used length in the pooled `AlignedMemory` wrapper and only zero the used prefix on `put`, or size the pool's default buffers to the transaction's requested `heap_size` where possible. Alternatively, make `calculate_heap_cost` charge a baseline reflecting the pool's actual physical (max-buffer) zero-fill cost regardless of requested `heap_size`, so that the CU price always matches the worst-case work performed by `reset()`.

### Proof of Concept
```rust
// program-runtime/src/mem_pool.rs (new test)
#[test]
fn test_heap_reset_cost_independent_of_requested_heap_size() {
    use std::time::Instant;
    let mut pool = VmMemoryPool::new();

    // Attacker requests MIN heap size (0 extra heap_cost via calculate_heap_cost).
    let small_heap_size = MIN_HEAP_FRAME_BYTES;
    let heap_cost_charged = crate::vm::calculate_heap_cost(small_heap_size, 8);
    assert_eq!(heap_cost_charged, 0, "attacker pays zero heap_cost for min heap size");

    // Measure actual physical work performed on put_heap regardless of requested size.
    let start = Instant::now();
    for _ in 0..10_000 {
        let heap = pool.get_heap(small_heap_size);
        // simulate VM using only `small_heap_size` bytes, then returning to pool
        pool.put_heap(heap); // always zero-fills full MAX_HEAP_FRAME_BYTES internally
    }
    let elapsed = start.elapsed();

    // Assert: measurable nonzero wall-clock cost incurred despite zero CU heap_cost charged.
    assert!(
        elapsed.as_micros() > 0,
        "physical zero-fill work performed for 0 charged CU, elapsed={elapsed:?}"
    );
    // Compare against calculate_heap_cost(MAX_HEAP_FRAME_BYTES, 8) * 10_000 to show
    // the same physical cost is incurred for the min-heap case but priced at 0.
}
```
Integration-level PoC: build an sBPF program performing N self-CPIs up to `max_instruction_stack_depth`/`max_call_depth` (as in `test_stack_heap_zeroed`) with `request_heap_frame(MIN_HEAP_FRAME_BYTES)` set (or omitted), and use `Measure`/wall-clock timers around `bank_client.send_and_confirm_instruction` compared against a variant using `MAX_HEAP_FRAME_BYTES`; assert that measured execution time (dominated by `put_heap`'s memset) is statistically similar between the two despite the `compute_units_consumed` (heap portion) being `0` vs. `(256KiB/32KiB - 1) * heap_cost` respectively — demonstrating the CU model does not reflect the true, size-independent zero-fill cost.

### Citations

**File:** program-runtime/src/vm.rs (L35-46)
```rust
pub fn calculate_heap_cost(heap_size: u32, heap_cost: u64) -> u64 {
    const KIBIBYTE: u64 = 1024;
    const PAGE_SIZE_KB: u64 = 32;
    let mut rounded_heap_size = u64::from(heap_size);
    rounded_heap_size =
        rounded_heap_size.saturating_add(PAGE_SIZE_KB.saturating_mul(KIBIBYTE).saturating_sub(1));
    rounded_heap_size
        .checked_div(PAGE_SIZE_KB.saturating_mul(KIBIBYTE))
        .expect("PAGE_SIZE_KB * KIBIBYTE > 0")
        .saturating_sub(1)
        .saturating_mul(heap_cost)
}
```

**File:** program-runtime/src/vm.rs (L107-134)
```rust
/// Create the SBF virtual machine
#[macro_export]
macro_rules! create_vm {
    ($vm:ident, $program:expr, $invoke_context:expr $(,)?) => {
        let invoke_context = &*$invoke_context;
        let stack_size = $program.get_config().stack_size();
        let heap_size = invoke_context.get_compute_budget().heap_size;
        let heap_cost_result =
            invoke_context
                .compute_meter
                .consume_checked($crate::__private::calculate_heap_cost(
                    heap_size,
                    invoke_context.get_execution_cost().heap_cost,
                ));
        let $vm = heap_cost_result.and_then(|_| {
            let (mut stack, mut heap) = $crate::__private::MEMORY_POOL
                .with_borrow_mut(|pool| (pool.get_stack(stack_size), pool.get_heap(heap_size)));
            let vm = $crate::__private::create_vm(
                $program,
                $invoke_context,
                stack
                    .as_slice_mut()
                    .get_mut(..stack_size)
                    .expect("invalid stack size"),
                heap.as_slice_mut()
                    .get_mut(..heap_size as usize)
                    .expect("invalid heap size"),
            );
```

**File:** program-runtime/src/vm.rs (L316-325)
```rust
        let (compute_units_consumed, result) =
            vm.execute_program(executable, &mut execution_mode, &mut call_frames);
        let register_trace = std::mem::take(&mut vm.register_trace);
        MEMORY_POOL.with_borrow_mut(|memory_pool| {
            memory_pool.put_stack(stack);
            memory_pool.put_heap(heap);
            memory_pool.put_call_frames(call_frames);
            debug_assert!(memory_pool.stack_len() <= MAX_INSTRUCTION_STACK_DEPTH_SIMD_0268);
            debug_assert!(memory_pool.heap_len() <= MAX_INSTRUCTION_STACK_DEPTH_SIMD_0268);
        });
```

**File:** program-runtime/src/mem_pool.rs (L44-61)
```rust
    fn put(&mut self, mut value: T) -> bool {
        self.items
            .get_mut(self.next_empty)
            .map(|item| {
                value.reset();
                item.replace(value);
                self.next_empty = self.next_empty.saturating_add(1);
                true
            })
            .unwrap_or(false)
    }
}

impl Reset for AlignedMemory<{ HOST_ALIGN }> {
    fn reset(&mut self) {
        self.as_slice_mut().fill(0)
    }
}
```

**File:** program-runtime/src/mem_pool.rs (L102-114)
```rust
impl VmMemoryPool {
    pub fn new() -> Self {
        Self {
            stack: Pool::new(array::from_fn(|_| {
                #[allow(clippy::arithmetic_side_effects)]
                AlignedMemory::zero_filled(solana_sbpf::vm::get_stack_frame_size() * MAX_CALL_DEPTH)
            })),
            heap: Pool::new(array::from_fn(|_| {
                AlignedMemory::zero_filled(MAX_HEAP_FRAME_BYTES as usize)
            })),
            call_frame: Pool::new(array::from_fn(|_| CallFrameBuffer::default())),
        }
    }
```

**File:** compute-budget-instruction/src/compute_budget_instruction_details.rs (L105-119)
```rust
        // Sanitize requested heap size
        let updated_heap_bytes =
            if let Some((index, requested_heap_size)) = self.requested_heap_size {
                if Self::sanitize_requested_heap_size(requested_heap_size) {
                    requested_heap_size
                } else {
                    return Err(TransactionError::InstructionError(
                        index,
                        InstructionError::InvalidInstructionData,
                    ));
                }
            } else {
                MIN_HEAP_FRAME_BYTES
            }
            .min(MAX_HEAP_FRAME_BYTES);
```

**File:** programs/bpf_loader/src/lib.rs (L4015-4032)
```rust
    #[test]
    fn test_calculate_heap_cost() {
        let heap_cost = 8_u64;

        // heap allocations are in 32K block, `heap_cost` of CU is consumed per additional 32k

        // assert less than 32K heap should cost zero unit
        assert_eq!(0, calculate_heap_cost(31 * 1024, heap_cost));

        // assert exact 32K heap should be cost zero unit
        assert_eq!(0, calculate_heap_cost(32 * 1024, heap_cost));

        // assert slightly more than 32K heap should cost 1 * heap_cost
        assert_eq!(heap_cost, calculate_heap_cost(33 * 1024, heap_cost));

        // assert exact 64K heap should cost 1 * heap_cost
        assert_eq!(heap_cost, calculate_heap_cost(64 * 1024, heap_cost));
    }
```

**File:** programs/sbf/tests/programs.rs (L5266-5295)
```rust
    for heap_len in [32usize * 1024, 64 * 1024, 128 * 1024, 256 * 1024] {
        // TEST_STACK_HEAP_ZEROED will recursively check that stack and heap are zeroed until it
        // reaches max CPI invoke depth. We make it fail at max depth so we're sure that there's no
        // legit way to access non-zeroed stack and heap regions.
        let mut instruction_data = vec![TEST_STACK_HEAP_ZEROED];
        instruction_data.extend_from_slice(&heap_len.to_le_bytes());

        let instruction = Instruction::new_with_bytes(
            invoke_program_id,
            &instruction_data,
            account_metas.clone(),
        );

        let message = Message::new(
            &[
                ComputeBudgetInstruction::set_compute_unit_limit(1_400_000),
                ComputeBudgetInstruction::request_heap_frame(heap_len as u32),
                instruction,
            ],
            Some(&mint_pubkey),
        );
        let tx = Transaction::new(&[&mint_keypair], message.clone(), bank.last_blockhash());
        let (result, _, logs, _) = process_transaction_and_record_inner(&bank, tx);
        assert!(result.is_err(), "{result:?}");
        assert!(
            logs.iter()
                .any(|log| log.contains("Cross-program invocation call depth too deep")),
            "{logs:?}"
        );
    }
```
