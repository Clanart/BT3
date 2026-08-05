## Title
Ed25519/Secp256k1 precompile silently "verifies" with zero signatures when only the minimal header bytes are present — vacuous success masking absence of any signature check - (File: `precompiles/src/ed25519.rs`, `precompiles/src/secp256k1.rs`)

## Summary
The external report's core defect is: an unvalidated, attacker-supplied byte array (`priceUpdateData`) can be empty, which silently causes an update/verification step to be skipped, while downstream logic still treats the operation as having succeeded and consumes a stale/unverified value. The Agave analog is the `verify()` function of the `ed25519` and `secp256k1` precompiles: when `num_signatures == 0` and the instruction data is exactly the minimum header size, the "zero signatures but non-trivial data" guard is bypassed, the verification loop iterates zero times, and the function returns `Ok(())` — i.e., the precompile instruction is reported as successfully "verified" without performing any signature check at all.

## Finding Description
In `precompiles/src/ed25519.rs`: [1](#0-0) 
```
if data.len() < SIGNATURE_OFFSETS_START {
    return Err(PrecompileError::InvalidInstructionDataSize);
}
let num_signatures = data[0] as usize;
if num_signatures == 0 && data.len() > SIGNATURE_OFFSETS_START {
    return Err(PrecompileError::InvalidInstructionDataSize);
}
let expected_data_size = num_signatures
    .saturating_mul(SIGNATURE_OFFSETS_SERIALIZED_SIZE)
    .saturating_add(SIGNATURE_OFFSETS_START);
if data.len() < expected_data_size {
    return Err(PrecompileError::InvalidInstructionDataSize);
}
```
When `data.len() == SIGNATURE_OFFSETS_START` (the minimal header, no offset structs, no signature/pubkey/message payload) and `num_signatures == 0`, the `num_signatures == 0 && data.len() > SIGNATURE_OFFSETS_START` guard is false (because `data.len()` is not strictly greater than `SIGNATURE_OFFSETS_START`), so no error is raised. `expected_data_size` collapses to `SIGNATURE_OFFSETS_START`, which the data length satisfies, so the second check also passes. The verification loop `for i in 0..num_signatures` never executes, and `verify()` returns `Ok(())` unconditionally — meaning **no signature, no public key, and no message were ever checked**, yet the instruction is treated exactly the same as a successfully verified signature.

The same pattern exists in `precompiles/src/secp256k1.rs`: [2](#0-1) 
```
if data.is_empty() {
    return Err(PrecompileError::InvalidInstructionDataSize);
}
let count = data[0] as usize;
if count == 0 && data.len() > 1 {
    // count is zero but the instruction data indicates that is probably not
    // correct, fail the instruction to catch probable invalid secp256k1
    // instruction construction.
    return Err(PrecompileError::InvalidInstructionDataSize);
}
```
Here, if `data.len() == 1` (just the `count` byte) and `count == 0`, the guard is likewise bypassed and `verify()` trivially returns `Ok(())`.

This is notably inconsistent with `precompiles/src/secp256r1.rs`, which unconditionally rejects the zero-signature case regardless of data length: [3](#0-2) 
```
let num_signatures = data[0] as usize;
if num_signatures == 0 {
    return Err(PrecompileError::InvalidInstructionDataSize);
}
```
This confirms the ed25519/secp256k1 checks were specifically written to only catch the case where "leftover" data looks like an attempted (but malformed) signature — they were not designed to guarantee that a "successful" precompile instruction actually verified anything. The invariant broken is: *"the runtime accepting a precompile instruction as executed without error implies at least one signature was cryptographically checked."* That invariant does not hold for the minimal-length, zero-signature case in ed25519/secp256k1.

## Impact Explanation
This matches the "false execution/acceptance" category: transactions can include an `ed25519_program` or `secp256k1_program` instruction that trivially "succeeds" while performing zero cryptographic verification. Any code (on-chain program or off-chain indexer/verifier) that relies on the semantic "if the preceding/paired precompile instruction did not error, a signature was verified" — a widely used pattern for cross-program signature attestation via the Instructions sysvar — can be misled into treating an unauthenticated instruction as authenticated, if it does not independently re-check `num_signatures > 0`. This does not by itself move funds inside Agave's own runtime/builtins, but it demonstrates that the precompile's success/failure signal is not a reliable proxy for "signature verified," directly analogous to the Pyth report's "update appears to succeed but nothing was actually updated/checked" pattern.

## Likelihood Explanation
The bypass requires only crafting a 2-byte (ed25519) or 1-byte (secp256k1) instruction data payload with `num_signatures`/`count` set to `0` — trivially constructible by any unprivileged transaction sender with no special privileges, gossip trust, or admin access, satisfying the "unprivileged" requirement. No fees, staking, or timing constraints are needed; this is a pure instruction-construction issue reachable on every cluster.

## Recommendation
Make the zero-signature rejection in `ed25519::verify` and `secp256k1::verify` unconditional (matching `secp256r1::verify`), rather than conditioning it on `data.len() > SIGNATURE_OFFSETS_START` / `data.len() > 1`. I.e., change:
```
if num_signatures == 0 && data.len() > SIGNATURE_OFFSETS_START {
    return Err(PrecompileError::InvalidInstructionDataSize);
}
```
to:
```
if num_signatures == 0 {
    return Err(PrecompileError::InvalidInstructionDataSize);
}
```
(and equivalently for `secp256k1`), so a "successfully verified" precompile instruction can never signify zero checked signatures.

## Proof of Concept
1. Construct an `ed25519_program` instruction with `data = [0u8, 0u8]` (i.e., `num_signatures = 0`, padding byte `0`, total length `== SIGNATURE_OFFSETS_START`).
2. Submit a transaction containing this instruction (plus a fee payer signature to satisfy normal transaction requirements).
3. `precompiles::ed25519::verify` is invoked with this `data`:
   - `data.len() (2) < SIGNATURE_OFFSETS_START (2)` → false, no early error.
   - `num_signatures == 0 && data.len() (2) > SIGNATURE_OFFSETS_START (2)` → false (not strictly greater), no error.
   - `expected_data_size == 2`, `data.len() (2) < 2` → false, no error.
   - Loop `for i in 0..0` never runs.
   - Returns `Ok(())`.
4. The instruction is reported as executed without error, even though no signature, public key, or message was ever verified. The same construction with `data = [0u8]` works against `secp256k1::verify`. [4](#0-3) [5](#0-4) [6](#0-5)

### Citations

**File:** precompiles/src/ed25519.rs (L11-29)
```rust
pub fn verify(
    data: &[u8],
    instruction_datas: &[&[u8]],
    _feature_set: &FeatureSet,
) -> Result<(), PrecompileError> {
    if data.len() < SIGNATURE_OFFSETS_START {
        return Err(PrecompileError::InvalidInstructionDataSize);
    }
    let num_signatures = data[0] as usize;
    if num_signatures == 0 && data.len() > SIGNATURE_OFFSETS_START {
        return Err(PrecompileError::InvalidInstructionDataSize);
    }
    let expected_data_size = num_signatures
        .saturating_mul(SIGNATURE_OFFSETS_SERIALIZED_SIZE)
        .saturating_add(SIGNATURE_OFFSETS_START);
    // We do not check or use the byte at data[1]
    if data.len() < expected_data_size {
        return Err(PrecompileError::InvalidInstructionDataSize);
    }
```

**File:** precompiles/src/secp256k1.rs (L23-43)
```rust
pub fn verify(
    data: &[u8],
    instruction_datas: &[&[u8]],
    _feature_set: &FeatureSet,
) -> Result<(), PrecompileError> {
    if data.is_empty() {
        return Err(PrecompileError::InvalidInstructionDataSize);
    }
    let count = data[0] as usize;
    if count == 0 && data.len() > 1 {
        // count is zero but the instruction data indicates that is probably not
        // correct, fail the instruction to catch probable invalid secp256k1
        // instruction construction.
        return Err(PrecompileError::InvalidInstructionDataSize);
    }
    let expected_data_size = count
        .saturating_mul(SIGNATURE_OFFSETS_SERIALIZED_SIZE)
        .saturating_add(1);
    if data.len() < expected_data_size {
        return Err(PrecompileError::InvalidInstructionDataSize);
    }
```

**File:** precompiles/src/secp256r1.rs (L18-29)
```rust
pub fn verify(
    data: &[u8],
    instruction_datas: &[&[u8]],
    _feature_set: &FeatureSet,
) -> Result<(), PrecompileError> {
    if data.len() < SIGNATURE_OFFSETS_START {
        return Err(PrecompileError::InvalidInstructionDataSize);
    }
    let num_signatures = data[0] as usize;
    if num_signatures == 0 {
        return Err(PrecompileError::InvalidInstructionDataSize);
    }
```
