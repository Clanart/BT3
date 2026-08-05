Based on the local code, the closest Agave analog is in the BLS12-381 elliptic-curve syscalls in `syscalls/src/lib.rs`, not in a proto/gRPC decoder as in the external report, but the same broken invariant applies: curve point bytes are accepted and operated on without the mandatory on-curve/subgroup validation that the codebase itself provides as a separate primitive.

### Title
BLS12-381 group-op syscall performs "unchecked" point addition/subtraction on unvalidated attacker-supplied points - (File: `syscalls/src/lib.rs`)

### Summary
`SyscallCurveGroupOps` implements `sol_curve_group_op`, which any on-chain program can invoke directly. For the `BLS12_381_G1`/`BLS12_381_G2` curve IDs, the `ADD`/`SUB` branches translate raw caller memory straight into `PodBLSG1Point`/`PodBLSG2Point` via `translate_type` (a byte-layout reinterpretation with no curve semantics) and pass them straight to `solana_bls12_381_syscall::bls12_381_g1_addition_unchecked` / `bls12_381_g2_addition_unchecked` / `..._subtraction_unchecked` [1](#0-0) . This is the same class of defect as the external report: point components (X, Y limbs) are accepted and fed into curve arithmetic without ever calling the codebase's own validation routine, `bls12_381_g1_point_validation`/`bls12_381_g2_point_validation`, which is only invoked from the separate `SyscallCurvePointValidation` syscall [2](#0-1) .

### Finding Description
`SyscallCurveGroupOps` handles four curve families in one dispatcher: Curve25519 Edwards, Curve25519 Ristretto, BLS12-381 G1, and BLS12-381 G2 [3](#0-2) . For Curve25519, `edwards::add_edwards`/`ristretto::add_ristretto` internally validate compressed point encoding as part of decompression and return `None` on failure [4](#0-3) . For BLS12-381, however, the G1/G2 `ADD` and `SUB` operations explicitly call the `_unchecked` variants of the underlying library functions:
- `bls12_381_g1_addition_unchecked` / `bls12_381_g1_subtraction_unchecked` [5](#0-4) 
- `bls12_381_g2_addition_unchecked` / `bls12_381_g2_subtraction_unchecked` [6](#0-5) 

The inputs `left_point`/`right_point` come straight from `translate_type::<PodBLSG1Point>(...)` / `translate_type::<PodBLSG2Point>(...)` over caller-controlled VM memory — a raw POD (plain-old-data) cast with zero curve-membership checking [7](#0-6) . This is functionally identical to the reported bug: components/limbs of a point are set directly from untrusted bytes and used in group/pairing-adjacent arithmetic without confirming the point is on the curve or in the correct subgroup. The dedicated, correctly-checked path — `SyscallCurvePointValidation` calling `bls12_381_g1_point_validation`/`bls12_381_g2_point_validation` [8](#0-7)  — exists in the same file but is never invoked by `SyscallCurveGroupOps` before doing arithmetic, and its use is left entirely to the calling BPF program's discretion.

The actual "unchecked" library function bodies live in the external `solana-bls12-381-syscall` crate (crates.io dependency, not vendored in this repo) [9](#0-8) , so I cannot confirm from local source exactly what internal checks (if any) remain inside those functions; the finding rests on the explicit `_unchecked` naming and on the fact that Agave's syscall layer does not perform or enforce validation before calling them, unlike the parallel Curve25519 paths which validate as part of decompression.

### Impact Explanation
If a program built on top of these syscalls (e.g., a BLS aggregate-signature or pairing-based verifier) uses `sol_curve_group_op` `ADD`/`SUB` on user-supplied G1/G2 points and then feeds the result into `sol_curve_pairing_map` expecting a valid subgroup element, an attacker can supply off-curve or wrong-subgroup coordinates that still satisfy the POD byte layout. Because the addition/subtraction is explicitly "unchecked," the syscall will not reject such input and may hand back a point that a subsequent pairing check treats as valid, potentially allowing forged signature/pairing acceptance in the calling program — the same "may impact pairing checks" concern raised in the original report. This falls into the "false execution/acceptance" impact category for on-chain program logic.

### Likelihood Explanation
Likelihood is moderate to low at the Agave layer itself: the vulnerability is only actionable if a downstream program relies on `ADD`/`SUB` for point validity instead of calling `sol_curve_validate_point` first, since the runtime does not implicitly enforce validation between syscall calls. The explicit `_unchecked` naming, contrasted with the fully-checked `SyscallCurvePointValidation` path, indicates this is intentional performance-oriented design (the codebase shows the same "cheap unchecked op + single validation at the end" pattern intentionally in `bls_vote_sigverify.rs` for signature aggregation) [10](#0-9) , rather than an oversight. This significantly reduces confidence that it constitutes an exploitable Agave-level bug versus a documented API contract that shifts validation responsibility to callers.

### Recommendation
- Document explicitly (in the syscall interface / SIMD spec) that `ADD`/`SUB` for BLS12-381 G1/G2 do not validate on-curve/subgroup membership, and that any program intending to use the result in a pairing check must call `sol_curve_validate_point` on inputs first.
- Consider exposing a "checked" variant of `sol_curve_group_op` for BLS12-381 (mirroring the Curve25519 behavior, which validates as part of decompression) so callers get safe-by-default semantics, consistent with the `GetBlobHeaderFromProto` recommendation of either performing explicit `IsOnCurve`/`IsInSubGroup` checks or using validated deserialization.

### Proof of Concept
Not independently verified against the vendored `solana-bls12-381-syscall` crate internals (unavailable in local source), so a concrete forged-pairing PoC cannot be constructed from this repo alone. The evidence supporting the finding is the direct syscall code path shown above: `translate_type` → raw `PodBLSG1Point`/`PodBLSG2Point` → `*_addition_unchecked`/`*_subtraction_unchecked` with no intervening call to `bls12_381_g*_point_validation` [11](#0-10) . Confirming exploitability requires inspecting the external crate's `*_unchecked` implementations, which is outside what the local codebase index provides — I recommend starting a full Devin session with repository/dependency access to pull in `solana-bls12-381-syscall` source and verify whether `_unchecked` skips on-curve or only subgroup checks, and to test whether a program can leverage this into an actual forged pairing acceptance.

### Citations

**File:** syscalls/src/lib.rs (L1051-1105)
```rust
            BLS12_381_G1_LE | BLS12_381_G1_BE => {
                let cost = invoke_context
                    .get_execution_cost()
                    .bls12_381_g1_validate_cost;
                invoke_context.compute_meter.consume_checked(cost)?;

                let point = translate_type::<solana_bls12_381_syscall::PodG1Point>(
                    memory_mapping,
                    point_addr,
                    check_aligned,
                )?;

                let endianness = if curve_id == BLS12_381_G1_LE {
                    solana_bls12_381_syscall::Endianness::LE
                } else {
                    solana_bls12_381_syscall::Endianness::BE
                };

                if solana_bls12_381_syscall::bls12_381_g1_point_validation(
                    solana_bls12_381_syscall::Version::V0,
                    point,
                    endianness,
                ) {
                    Ok(SUCCESS)
                } else {
                    Ok(1)
                }
            }
            BLS12_381_G2_LE | BLS12_381_G2_BE => {
                let cost = invoke_context
                    .get_execution_cost()
                    .bls12_381_g2_validate_cost;
                invoke_context.compute_meter.consume_checked(cost)?;

                let point = translate_type::<solana_bls12_381_syscall::PodG2Point>(
                    memory_mapping,
                    point_addr,
                    check_aligned,
                )?;

                let endianness = if curve_id == BLS12_381_G2_LE {
                    solana_bls12_381_syscall::Endianness::LE
                } else {
                    solana_bls12_381_syscall::Endianness::BE
                };

                if solana_bls12_381_syscall::bls12_381_g2_point_validation(
                    solana_bls12_381_syscall::Version::V0,
                    point,
                    endianness,
                ) {
                    Ok(SUCCESS)
                } else {
                    Ok(1)
                }
```

**File:** syscalls/src/lib.rs (L1217-1254)
```rust
declare_builtin_function!(
    // Elliptic Curve Group Operations
    //
    // Currently, the following curves are supported:
    // - Curve25519 Edwards and Ristretto representations
    // - BLS12-381
    SyscallCurveGroupOps,
    fn rust(
        invoke_context: &mut InvokeContext<'_, '_>,
        curve_id: u64,
        group_op: u64,
        left_input_addr: u64,
        right_input_addr: u64,
        result_point_addr: u64,
    ) -> Result<u64, Error> {
        use {
            solana_bls12_381_syscall::{
                PodG1Point as PodBLSG1Point, PodG2Point as PodBLSG2Point, PodScalar as PodBLSScalar,
            },
            solana_curve25519::{
                edwards::{self, PodEdwardsPoint},
                ristretto::{self, PodRistrettoPoint},
                scalar,
            },
            solana_define_syscall::curve_constants::*,
        };

        if !invoke_context.get_feature_set().enable_bls12_381_syscall
            && matches!(
                curve_id,
                BLS12_381_G1_BE | BLS12_381_G1_LE | BLS12_381_G2_BE | BLS12_381_G2_LE
            )
        {
            return Err(SyscallError::InvalidAttribute.into());
        }

        let check_aligned = invoke_context.get_check_aligned();
        match curve_id {
```

**File:** syscalls/src/lib.rs (L1262-1285)
```rust
                    let memory_mapping = invoke_context.memory_contexts.memory_mapping_mut()?;
                    let left_point = translate_type::<PodEdwardsPoint>(
                        memory_mapping,
                        left_input_addr,
                        check_aligned,
                    )?;
                    let right_point = translate_type::<PodEdwardsPoint>(
                        memory_mapping,
                        right_input_addr,
                        check_aligned,
                    )?;

                    if let Some(result_point) = edwards::add_edwards(left_point, right_point) {
                        translate_mut!(
                            memory_mapping,
                            check_aligned,
                            let result_point_ref_mut: (&mut MaybeUninit<PodEdwardsPoint>) = map(result_point_addr)?;
                        );
                        result_point_ref_mut.write(result_point);
                        Ok(0)
                    } else {
                        Ok(1)
                    }
                }
```

**File:** syscalls/src/lib.rs (L1465-1535)
```rust
                    GROUP_OP_ADD => {
                        let cost = invoke_context.get_execution_cost().bls12_381_g1_add_cost;
                        invoke_context.compute_meter.consume_checked(cost)?;

                        let memory_mapping = invoke_context.memory_contexts.memory_mapping_mut()?;
                        let left_point = translate_type::<PodBLSG1Point>(
                            memory_mapping,
                            left_input_addr,
                            check_aligned,
                        )?;
                        let right_point = translate_type::<PodBLSG1Point>(
                            memory_mapping,
                            right_input_addr,
                            check_aligned,
                        )?;

                        if let Some(result_point) =
                            solana_bls12_381_syscall::bls12_381_g1_addition_unchecked(
                                solana_bls12_381_syscall::Version::V0,
                                left_point,
                                right_point,
                                endianness,
                            )
                        {
                            translate_mut!(
                                memory_mapping,
                                check_aligned,
                                let result_point_ref_mut: (&mut MaybeUninit<PodBLSG1Point>) = map(result_point_addr)?;
                            );
                            result_point_ref_mut.write(result_point);
                            Ok(SUCCESS)
                        } else {
                            Ok(1)
                        }
                    }
                    GROUP_OP_SUB => {
                        let cost = invoke_context
                            .get_execution_cost()
                            .bls12_381_g1_subtract_cost;
                        invoke_context.compute_meter.consume_checked(cost)?;

                        let memory_mapping = invoke_context.memory_contexts.memory_mapping_mut()?;
                        let left_point = translate_type::<PodBLSG1Point>(
                            memory_mapping,
                            left_input_addr,
                            check_aligned,
                        )?;
                        let right_point = translate_type::<PodBLSG1Point>(
                            memory_mapping,
                            right_input_addr,
                            check_aligned,
                        )?;

                        if let Some(result_point) =
                            solana_bls12_381_syscall::bls12_381_g1_subtraction_unchecked(
                                solana_bls12_381_syscall::Version::V0,
                                left_point,
                                right_point,
                                endianness,
                            )
                        {
                            translate_mut!(
                                memory_mapping,
                                check_aligned,
                                let result_point_ref_mut: (&mut MaybeUninit<PodBLSG1Point>) = map(result_point_addr)?;
                            );
                            result_point_ref_mut.write(result_point);
                            Ok(SUCCESS)
                        } else {
                            Ok(1)
                        }
```

**File:** syscalls/src/lib.rs (L1603-1657)
```rust
                        if let Some(result_point) =
                            solana_bls12_381_syscall::bls12_381_g2_addition_unchecked(
                                solana_bls12_381_syscall::Version::V0,
                                left_point,
                                right_point,
                                endianness,
                            )
                        {
                            translate_mut!(
                                memory_mapping,
                                check_aligned,
                                let result_point_ref_mut: (&mut MaybeUninit<PodBLSG2Point>) = map(result_point_addr)?;
                            );
                            result_point_ref_mut.write(result_point);
                            Ok(SUCCESS)
                        } else {
                            Ok(1)
                        }
                    }
                    GROUP_OP_SUB => {
                        let cost = invoke_context
                            .get_execution_cost()
                            .bls12_381_g2_subtract_cost;
                        invoke_context.compute_meter.consume_checked(cost)?;

                        let memory_mapping = invoke_context.memory_contexts.memory_mapping_mut()?;
                        let left_point = translate_type::<PodBLSG2Point>(
                            memory_mapping,
                            left_input_addr,
                            check_aligned,
                        )?;
                        let right_point = translate_type::<PodBLSG2Point>(
                            memory_mapping,
                            right_input_addr,
                            check_aligned,
                        )?;

                        if let Some(result_point) =
                            solana_bls12_381_syscall::bls12_381_g2_subtraction_unchecked(
                                solana_bls12_381_syscall::Version::V0,
                                left_point,
                                right_point,
                                endianness,
                            )
                        {
                            translate_mut!(
                                memory_mapping,
                                check_aligned,
                                let result_point_ref_mut: (&mut MaybeUninit<PodBLSG2Point>) = map(result_point_addr)?;
                            );
                            result_point_ref_mut.write(result_point);
                            Ok(SUCCESS)
                        } else {
                            Ok(1)
                        }
```

**File:** Cargo.toml (L359-361)
```text
solana-bls-signatures = { version = "3.3.0", features = ["serde"] }
solana-bls12-381-syscall = "0.1.0"
solana-bn254 = "3.2.1"
```

**File:** bls-sigverify/src/bls_vote_sigverify.rs (L337-348)
```rust
#[cfg_attr(feature = "dev-context-only-utils", qualifiers(pub))]
fn aggregate_signatures(votes: &[UnverifiedVotePayload]) -> Result<SignatureProjective, BlsError> {
    debug_assert!(current_thread_index().is_some());
    let signatures = votes.par_iter().map(|v| &v.vote_message.signature);
    // TODO(sam): Currently, `par_aggregate` performs full validation
    // (on-curve + subgroup check) for every signature. Since the subgroup
    // check is expensive, we can use an `unchecked` deserialization here
    // (performing only the cheap on-curve check) and rely on a single subgroup
    // check on the final aggregated signature. This should save more than 80%
    // of the time for signature aggregation.
    SignatureProjective::par_aggregate(signatures)
}
```
