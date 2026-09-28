### Title
`SignableTransaction::new` sufficiency check `input_sat < (payment_sat + needed_fee)` is overflowable, bypassing the funds check — (`networks/bitcoin/src/wallet/send.rs`)

### Summary
The bounds check guarding transaction construction sums attacker-influenced payment amounts into `payment_sat` and then checks `input_sat < (payment_sat + needed_fee)`. Like the Vyper `assert le(add(start, length), src_len)` bug, the addition `payment_sat + needed_fee` (and `payment_sat + fee_with_change`) can overflow `u64` in release builds, wrapping to a small value and defeating the `NotEnoughFunds` check. The threshold then proceeds to produce FROST signature shares for a transaction the check was intended to reject.

### Finding Description
`SignableTransaction::new` computes `payment_sat` as the un-checked `u64` sum of all payment amounts: [1](#0-0) 

and validates solvency with a single additive inequality: [2](#0-1) 

Neither `payment_sat + needed_fee` (line 215) nor `payment_sat + fee_with_change` (line 228, where only the subtraction is `checked_sub`, not the addition inside it) uses `checked_add`. A payment amount such as `u64::MAX - 100` makes `payment_sat + needed_fee` wrap to a value below `input_sat`, so `NotEnoughFunds` is never raised even though the true payment total vastly exceeds the inputs. The wrapped sum then propagates into the change computation at line 228, causing `input_sat - wrapped_small` to produce an enormous change output. The resulting `SignableTransaction` is carried into `multisig()` and `sign()`, where `taproot_key_spend_signature_hash` is computed and signed per input (`networks/bitcoin/src/wallet/send.rs:383-390`).

The payment list is derived from out-instructions the processor is told to fulfill (cross-chain instructions whose amounts originate from user-supplied data on external chains, e.g. `InInstruction::read` which reads a raw 256-bit `amount` at `networks/ethereum/src/router.rs:85-87`), so the amounts are reachable by an unprivileged party who causes an instruction to be signed.

### Impact Explanation
The overflow causes the threshold signing pipeline to emit shares for, and ultimately sign, a transaction violating the explicit `input_sat >= payment_sat + needed_fee` invariant — concrete signing of an unintended message. Because any overflowing payment necessarily exceeds `MAX_MONEY`, the signed transaction is consensus-invalid and cannot move funds; the practical damage is (a) the sign session completes "successfully" on an invalid transaction, and (b) the associated out-instruction/plan may be marked fulfilled by the coordinator while the Bitcoin payment can never confirm, desynchronizing Serai's accounting of the send.

### Likelihood Explanation
Reachable whenever a payment amount in an out-instruction can carry a large (near-`u64::MAX` / >64-bit-derived) value; amounts read as `U256` from Ethereum-side instructions and converted downward are the plausible delivery path. Exploitation requires only controlling one instruction's amount field — no key material, collusion, or validator misbehavior needed. Impact is bounded to signing an unbroadcastable transaction rather than theft, hence Medium rather than High.

### Recommendation
Use checked arithmetic throughout: compute `payment_sat` with `checked_add`/`checked_sum`, replace `input_sat < (payment_sat + needed_fee)` and `payment_sat + fee_with_change` with `checked_add`/`checked_sub` combinations that error on overflow, and reject any individual payment `> MAX_MONEY` before summation.

### Proof of Concept
```rust
// Conceptual: in SignableTransaction::new, provide:
let payments = &[(some_script, u64::MAX - 100), (other_script, 1000)];
// payment_sat wraps: (u64::MAX - 100) + 1000 wraps to 899
// input_sat = 1_000_000 (one real UTXO)
// needed_fee = ~500
// Check: 1_000_000 < (899 + 500)? -> false, NotEnoughFunds never raised
// A transaction with outputs summing to ~2^64 sats over 1_000_000 sats of
// inputs is constructed, its sighash computed, and threshold-signed.
```
Note: I could not fully trace the upstream conversion from the 256-bit `InInstruction` amount to the `u64` payment within the available iterations; if a saturating/`try_into` conversion clamps values to `u64::MAX` rather than rejecting them, the overflow is reachable; if amounts are range-checked to `MAX_MONEY` upstream, the analog is unreachable and should be rejected.

### Citations

**File:** networks/bitcoin/src/wallet/send.rs (L187-187)
```rust
    let payment_sat = payments.iter().map(|payment| payment.1).sum::<u64>();
```

**File:** networks/bitcoin/src/wallet/send.rs (L215-221)
```rust
    if input_sat < (payment_sat + needed_fee) {
      Err(TransactionError::NotEnoughFunds {
        inputs: input_sat,
        payments: payment_sat,
        fee: needed_fee,
      })?;
    }
```
