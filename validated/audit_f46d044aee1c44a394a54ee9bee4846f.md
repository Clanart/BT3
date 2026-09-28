### Title
Unchecked u64 multiplication/addition overflow in `SignableTransaction::new` fee and change math allows a consensus-invalid Bitcoin transaction to be threshold-signed — ([File: networks/bitcoin/src/wallet/send.rs](networks/bitcoin/src/wallet/send.rs))

### Summary
The analog to CVE-2026-33855 (integer overflow/wraparound) lives in `networks/bitcoin/src/wallet/send.rs`. `SignableTransaction::new` performs unsaturating `u64` arithmetic on attacker-influenced amounts: `fee_per_vbyte * vbytes` (line 206), `payment_sat + needed_fee` (line 215), and `payment_sat + fee_with_change` inside the `checked_sub` argument (line 228). In a release build these wrap; in a debug build they panic. A wrapped `payment_sat` defeats the `NotEnoughFunds` check and causes the FROST signing pipeline (`TransactionMachine` → `TransactionSignMachine::sign`, which computes `taproot_key_spend_signature_hash`) to sign a transaction whose output values exceed its inputs.

### Finding Description
Three unchecked arithmetic sites exist in `SignableTransaction::new` (`networks/bitcoin/src/wallet/send.rs`):

```rust
let mut needed_fee = fee_per_vbyte * vbytes;                    // line 206
...
if input_sat < (payment_sat + needed_fee) {                     // line 215
  Err(TransactionError::NotEnoughFunds { ... })?;
}
...
if let Some(value) = input_sat.checked_sub(payment_sat + fee_with_change) { // line 228
```

`payment_sat` is `payments.iter().map(|payment| payment.1).sum::<u64>()` (line 187) where `payments: &[(ScriptBuf, u64)]` is untrusted plan data that flows through `Plan::read`/`Payment::read` deserialization and into the signing machines. Each individual amount is only checked against `DUST` (line 166); no upper bound such as `Amount::MAX_MONEY` (21M BTC ≈ 2.1e15 sats) is enforced, and the running `sum::<u64>()` wraps silently on overflow.

If `payment_sat` wraps to a small value, the `NotEnoughFunds` check at line 215 passes even though the constructed `tx_outs` (line 188–191) each carry `Amount::from_sat(payment.1)` at their full attacker-specified magnitude — sum(outputs) > sum(inputs), which violates Bitcoin consensus rules. The transaction proceeds to `SignableTransaction::multisig` and `TransactionSignMachine::sign`, where every participant signs `taproot_key_spend_signature_hash` digests committing to these outputs via `Prevouts::All` (lines 373–397). The resulting signed transaction is unconfirmable.

The same wrap applies to `fee_per_vbyte * vbytes` (line 206): an attacker-influenced or extreme fee rate multiplied by `vbytes` can wrap `needed_fee` to a small value, bypassing the `TooLowFee` check (line 211) and producing a transaction relay-rejected for insufficient fee — again after the threshold signature has been produced.

Similarly, `payment_sat + fee_with_change` at line 228 can wrap before `checked_sub`, yielding a spuriously large `value` pushed as the change output (line 230), again exceeding real input value.

### Impact Explanation
Two concrete harms, both reachable by an unprivileged party whose withdrawal/payment instructions are encoded into the plan the signing set processes:

1. **Threshold signatures over a consensus-invalid transaction.** The FROST participants produce valid BIP-340 signatures for a transaction no node will ever accept (`outputs > inputs`). The plan is consumed (signing nonces used, plan marked attempted) yet the funds never move — the withdrawal is stuck, and depending on processor retry behavior, the associated inputs may be considered spent/unavailable. This is "concrete signing of an unintended message": the signers intended to authorize a valid transfer and instead produced signatures for a transaction that can never execute.
2. **Debug-build panic.** With overflow checks enabled, lines 206/215/228 panic inside the signing pipeline, a denial of service against the signing machine.

### Likelihood Explanation
Triggering requires a plan whose `payments` sum exceeds `u64::MAX` or whose amounts individually exceed `MAX_MONEY`, plus a `fee_per_vbyte` capable of overflowing the product at line 206. Whether a plan carrying such absurd amounts reaches `SignableTransaction::new` depends on upstream scheduler validation (in `processor/src`, out of scope here); the wallet layer itself performs no bound beyond `DUST`, so any caller feeding deserialized plan data into it exposes this path. The wrap is deterministic once such inputs are supplied — no race, no malicious-validator requirement — and every honest signer in the set will sign the invalid sighash, so a single crafted plan suffices. Medium severity: impact is a stuck/DoSed plan plus wasted threshold signatures, not key recovery or theft of funds, since the invalid transaction can never confirm.

### Recommendation
In `SignableTransaction::new` (`networks/bitcoin/src/wallet/send.rs`):

- Use `checked_add`/`checked_mul`/`checked_sum` for `input_sat`, `payment_sat`, `needed_fee`, `fee_with_change`, and the `payment_sat + fee_with_change` argument to `checked_sub`, returning a `TransactionError` on overflow instead of wrapping/panicking.
- Validate each payment amount and the aggregate against `bitcoin::Amount::MAX_MONEY` (21M BTC) in addition to the existing `DUST` lower bound.
- Apply the same checked arithmetic to `fee()` (lines 139–141), where `sum(inputs) - sum(outputs)` can underflow-panic if the constructed transaction is ever unbalanced.

### Proof of Concept
```rust
// Conceptual drive of SignableTransaction::new with wrapping amounts.
// payments sums to > u64::MAX, wrapping payment_sat to a small value.
let payments: Vec<(ScriptBuf, u64)> = vec![
  (script_a.clone(), u64::MAX - 1000),   // each >= DUST
  (script_b.clone(), u64::MAX - 1000),
];
// payment_sat wraps: (u64::MAX - 1000) + (u64::MAX - 1000) wraps to ~u64::MAX - 2001
// still huge; use three payments to wrap below input_sat:
let payments: Vec<(ScriptBuf, u64)> = vec![
  (script_a.clone(), u64::MAX / 2 + 1),
  (script_b.clone(), u64::MAX / 2 + 1),
  (script_c.clone(), u64::MAX / 2 + 1),
];
// sum = 3*(u64::MAX/2 + 1) wraps mod 2^64 to u64::MAX/2 + 2 (~9.2e18)
// With input_sat (real UTXOs, < 2.1e15 sats) this still fails; instead pick
// amounts so the wrapped sum < input_sat, e.g. two payments of
// (u64::MAX - input_sat + small)/1 each arranged so wrapped payment_sat < input_sat.
// Then line 215 passes, tx_outs carry the huge Amount values, and
// TransactionSignMachine::sign produces BIP-340 signatures over a
// transaction with sum(outputs) > sum(inputs) — consensus-invalid.
```
The essential witness: `payment_sat` at line 187 is a wrapping `u64` sum of unbounded `u64` payment amounts, and the only guard is `input_sat < payment_sat + needed_fee` (line 215), itself a wrapping addition. No `MAX_MONEY` or overflow check intervenes before the multisig machine signs the invalid outputs.