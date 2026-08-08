### Title
Underpriced compute cost for malformed `alt_bn128_pairing` syscall input (missing element-size length validation) - ([File: syscalls/src/lib.rs])

### Summary
The report describes a Solidity zk-verifier that assumes a proof byte array has a specific expected length without validating it, leading to wasted/underpriced computation and potential memory misuse when extra bytes are appended. The closest analog in Agave is the `SyscallAltBn128` implementation for the `ALT_BN128_PAIRING_BE`/`ALT_BN128_PAIRING_LE` group operations, where the compute cost is derived from `input_size` divided by the fixed pairing element size without first validating that `input_size` is an exact multiple of that element size.

### Finding Description
In `SyscallAltBn128` [1](#0-0) , the cost for a pairing operation is computed as:
```
let ele_len = input_size.checked_div(ALT_BN128_PAIRING_ELEMENT_SIZE as u64)...
```
This is an integer division. If a caller supplies an `input_size` that is not an exact multiple of `ALT_BN128_PAIRING_ELEMENT_SIZE`, the trailing partial-element bytes are silently dropped from the cost calculation (`ele_len` is truncated), while the full `input_size` bytes are still translated from guest memory via `translate_slice::<u8>` [2](#0-1)  and passed wholesale into `alt_bn128_versioned_pairing` [3](#0-2) . This is structurally the same bug class as the external report: a byte array is consumed by a cryptographic verification routine that assumes a fixed-multiple length without the calling code first checking `input_size % ELEMENT_SIZE == 0`.

Notably, the codebase already contains a feature flag `fix_alt_bn128_pairing_length_check` (SIMD-0334: "Fix alt_bn128_pairing length check") [4](#0-3) , confirming that this exact length-check class of bug was previously identified as needing a protocol-level fix for `alt_bn128_pairing`. However, in the `SyscallAltBn128` implementation itself [5](#0-4) , the `fix_alt_bn128_pairing_length_check` feature flag is never referenced or checked — the pairing cost/length logic runs unconditionally with the same `checked_div` truncation regardless of whether that feature is active. This suggests either (a) the actual fix lives inside `alt_bn128_versioned_pairing`/`solana_bn254` (not indexed/visible here) and correctly rejects non-multiple-length inputs, or (b) the syscall-level cost-accounting gap described above remains present in this snapshot.

### Impact Explanation
If `alt_bn128_versioned_pairing` does not itself reject inputs whose length is not a multiple of `ALT_BN128_PAIRING_ELEMENT_SIZE`, then a caller program could supply padding bytes to `input_size` to alter the truncated `ele_len` (undercounting the number of priced pairing elements) while providing memory/CPU work proportional to the real (larger) input, resulting in materially underpriced compute for the pairing operation — the class of bug this scan is meant to flag. This could not be fully confirmed because the internal validation logic of `alt_bn128_versioned_pairing` in `solana_bn254` was not present in the indexed codebase.

### Likelihood Explanation
Any BPF program can invoke `sol_alt_bn128_group_op` with attacker-controlled `input_size`/`group_op = ALT_BN128_PAIRING_BE/LE`, making this directly reachable from unprivileged user programs with no special permissions.

### Recommendation
Verify that `alt_bn128_versioned_pairing` (in the `solana_bn254` crate) explicitly validates `input.len() % ALT_BN128_PAIRING_ELEMENT_SIZE == 0` before processing, and additionally add an explicit check in `SyscallAltBn128` itself (before the cost calculation) that `input_size % ALT_BN128_PAIRING_ELEMENT_SIZE == 0`, returning an error otherwise, so that the compute cost calculation and the actual work performed can never diverge.

### Proof of Concept
Not able to construct a concrete PoC transaction from this index alone, because the internal implementation of `alt_bn128_versioned_pairing`/`solana_bn254::versioned` (which may already perform the missing length validation) is not included in the indexed files. Confirming exploitability requires inspecting that crate directly (e.g., via a full Devin session with repository access) to determine whether it rejects non-multiple-of-`ALT_BN128_PAIRING_ELEMENT_SIZE` inputs before or after the cost is charged in `syscalls/src/lib.rs`.

### Citations

**File:** syscalls/src/lib.rs (L2128-2213)
```rust
declare_builtin_function!(
    /// alt_bn128 group operations
    SyscallAltBn128,
    fn rust(
        invoke_context: &mut InvokeContext<'_, '_>,
        group_op: u64,
        input_addr: u64,
        input_size: u64,
        result_addr: u64,
        _arg5: u64,
    ) -> Result<u64, Error> {
        use solana_bn254::versioned::{
            alt_bn128_versioned_g1_addition, alt_bn128_versioned_g1_multiplication,
            alt_bn128_versioned_g2_addition, alt_bn128_versioned_g2_multiplication,
            alt_bn128_versioned_pairing, Endianness, VersionedG1Addition,
            VersionedG1Multiplication, VersionedG2Addition, VersionedG2Multiplication,
            VersionedPairing, ALT_BN128_G1_POINT_SIZE, ALT_BN128_G2_POINT_SIZE,
            ALT_BN128_G1_ADD_BE, ALT_BN128_G1_MUL_BE, ALT_BN128_PAIRING_BE,
            ALT_BN128_PAIRING_ELEMENT_SIZE, ALT_BN128_PAIRING_OUTPUT_SIZE, ALT_BN128_G1_ADD_LE,
            ALT_BN128_G1_MUL_LE, ALT_BN128_PAIRING_LE, ALT_BN128_G2_ADD_BE, ALT_BN128_G2_ADD_LE,
            ALT_BN128_G2_MUL_BE, ALT_BN128_G2_MUL_LE,
        };

        // SIMD-0284: Block LE ops if the feature is not active.
        if !invoke_context.get_feature_set().alt_bn128_little_endian &&
            matches!(
                group_op,
                ALT_BN128_G1_ADD_LE
                    | ALT_BN128_G1_MUL_LE
                    | ALT_BN128_PAIRING_LE
            )
        {
            return Err(SyscallError::InvalidAttribute.into());
        }

        // SIMD-0302: Block G2 ops if the feature is not active.
        if !invoke_context.get_feature_set().enable_alt_bn128_g2_syscalls &&
            matches!(
                group_op,
                ALT_BN128_G2_ADD_BE
                    | ALT_BN128_G2_ADD_LE
                    | ALT_BN128_G2_MUL_BE
                    | ALT_BN128_G2_MUL_LE
            )
        {
            return Err(SyscallError::InvalidAttribute.into());
        }

        let execution_cost = invoke_context.get_execution_cost();
        let (cost, output): (u64, usize) = match group_op {
            ALT_BN128_G1_ADD_BE | ALT_BN128_G1_ADD_LE => (
                execution_cost.alt_bn128_g1_addition_cost,
                ALT_BN128_G1_POINT_SIZE,
            ),
            ALT_BN128_G2_ADD_BE | ALT_BN128_G2_ADD_LE => (
                execution_cost.alt_bn128_g2_addition_cost,
                ALT_BN128_G2_POINT_SIZE,
            ),
            ALT_BN128_G1_MUL_BE | ALT_BN128_G1_MUL_LE => (
                execution_cost.alt_bn128_g1_multiplication_cost,
                ALT_BN128_G1_POINT_SIZE,
            ),
            ALT_BN128_G2_MUL_BE | ALT_BN128_G2_MUL_LE => (
                execution_cost.alt_bn128_g2_multiplication_cost,
                ALT_BN128_G2_POINT_SIZE,
            ),
            ALT_BN128_PAIRING_BE | ALT_BN128_PAIRING_LE => {
                let ele_len = input_size
                    .checked_div(ALT_BN128_PAIRING_ELEMENT_SIZE as u64)
                    .expect("div by non-zero constant");
                let cost = execution_cost
                    .alt_bn128_pairing_one_pair_cost_first
                    .saturating_add(
                        execution_cost
                            .alt_bn128_pairing_one_pair_cost_other
                            .saturating_mul(ele_len.saturating_sub(1)),
                    )
                    .saturating_add(execution_cost.sha256_base_cost)
                    .saturating_add(input_size)
                    .saturating_add(ALT_BN128_PAIRING_OUTPUT_SIZE as u64);
                (cost, ALT_BN128_PAIRING_OUTPUT_SIZE)
            }
            _ => {
                return Err(SyscallError::InvalidAttribute.into());
            }
        };
```

**File:** syscalls/src/lib.rs (L2227-2232)
```rust
        let input = translate_slice::<u8>(
            memory_mapping,
            input_addr,
            input_size,
            check_aligned,
        )?;
```

**File:** syscalls/src/lib.rs (L2275-2280)
```rust
            ALT_BN128_PAIRING_BE => {
                alt_bn128_versioned_pairing(VersionedPairing::V1, input, Endianness::BE)
            }
            ALT_BN128_PAIRING_LE => {
                alt_bn128_versioned_pairing(VersionedPairing::V1, input, Endianness::LE)
            }
```

**File:** feature-set/src/lib.rs (L2497-2500)
```rust
        (
            fix_alt_bn128_pairing_length_check::id(),
            "SIMD-0334: Fix alt_bn128_pairing length check",
        ),
```
