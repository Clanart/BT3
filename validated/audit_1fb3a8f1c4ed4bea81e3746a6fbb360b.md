### Title
Unchecked u64 arithmetic in transaction construction overflows on attacker-controlled output values - (networks/bitcoin/src/wallet/send.rs)

### Summary
`SignableTransaction::new` performs unchecked `u64` addition and multiplication over values that can originate from untrusted bytes (`ReceivedOutput::read` decodes an arbitrary `TxOut` via `consensus_decode`, with no bound checking the `value` field). Summing input/output amounts and computing `payment_sat + needed_fee` / `fee_per_vbyte * vbytes` can exceed `u64::MAX`, causing a panic (debug builds) or silent wraparound (release builds). With wraparound, the `NotEnoughFunds` check is bypassed and a `SignableTransaction` whose declared outputs exceed its inputs is produced and handed to the threshold signing machines.

### Finding Description
The relevant code is:

```rust
let input_sat = inputs.iter().map(|input| input.output.value.to_sat()).sum::<u64>();
...
let payment_sat = payments.iter().map(|payment| payment.1).sum::<u64>();
...
let mut needed_fee = fee_per_vbyte * vbytes;
if needed_fee < ((u64::from(bitcoin::policy::DEFAULT_MIN_RELAY_TX_FEE) * vbytes) / 1000) { ... }
if input_sat < (payment_sat + needed_fee) { Err(NotEnoughFunds ...)?; }
...
if let Some(value) = input_sat.checked_sub(payment_sat + fee_with_change) { ... }
```

`networks/bitcoin/src/wallet/send.rs:175-228`.

`ReceivedOutput::read` at `networks/bitcoin/src/wallet/mod.rs:122-134` accepts any consensus-decodable `TxOut`, so `output.value` may be any `u64` — including values far above the 21M BTC supply cap. Three overflow sites exist:

1. `input_sat` / `payment_sat` / `fee()` use plain `sum::<u64>()` — with `payment_sat` a single payment of `u64::MAX` is sufficient to make `payment_sat + needed_fee` wrap.
2. `payment_sat + needed_fee` and `payment_sat + fee_with_change` are ordinary `u64` additions evaluated before the `checked_sub`/`NotEnoughFunds` guard, so the guard itself can be defeated by wraparound.
3. `fee_per_vbyte * vbytes` is an unchecked multiplication; a large caller-or-attacker-influenced `fee_per_vbyte` overflows.
4. `fee()` at `send.rs:139-141` computes `sum(prevouts) - sum(outputs)` with a bare subtraction that underflows/panics when a malformed transaction was admitted via the wrapped check.

### Impact Explanation
When overflow checks are off (release), an unprivileged party who can feed crafted `ReceivedOutput` bytes or payment amounts into the planning path causes the `NotEnoughFunds` invariant to be bypassed: the constructed `SignableTransaction` contains outputs whose total exceeds the real inputs. The threshold FROST machines in `TransactionSignMachine::sign` (`send.rs:355-398`) then produce valid Schnorr signatures for a transaction that is consensus-invalid and unbroadcastable — burning the signing session and, in configurations that treat the built fee/plan as authoritative, misreporting the fee via `fee()`'s wrapped result. When overflow checks are enabled (debug or `overflow-checks = true`), the same inputs cause an immediate panic in the signing pipeline — a denial of service of the spend flow reachable purely from crafted bytes.

### Likelihood Explanation
Reachability is through the explicitly in-scope `ReceivedOutput::read` deserialization path plus integrator-supplied `payments`/`fee_per_vbyte`. The attacker does not need validator status — only the ability to supply bytes/parameters that are deserialized into `TxOut`/`Amount` values or payments. Crafting a `u64::MAX` output value or payment is trivial. However, the impact is bounded to availability and malformed-transaction production rather than key recovery or signature forgery, since the resulting Bitcoin transaction is invalid and the input funds remain unspent.

### Recommendation
Replace all bare `u64` arithmetic on attacker-influenced values in `send.rs` with checked/saturating operations and validate bounds at the deserialization boundary:

- In `ReceivedOutput::read` (`wallet/mod.rs`), reject `TxOut` values exceeding `MAX_MONEY` (21,000,000 BTC in sats) and enforce a maximum on the number of inputs/payments.
- In `SignableTransaction::new`, use `checked_add`/`checked_mul`/`checked_sum` for `input_sat`, `payment_sat`, `payment_sat + needed_fee`, `payment_sat + fee_with_change`, and `fee_per_vbyte * vbytes`, returning a `TransactionError` on overflow.
- In `fee()`, use `checked_sub` so it cannot wrap if a malformed transaction is ever constructed.

### Proof of Concept
```rust
// Attacker-controlled bytes -> ReceivedOutput::read -> SignableTransaction::new
// Payment amount of u64::MAX defeats the NotEnoughFunds check via wraparound.
let payments = &[(script_pubkey, u64::MAX)]; // or two payments summing past u64::MAX
let stx = SignableTransaction::new(inputs, payments, change, None, fee_per_vbyte);
// payment_sat + needed_fee wraps to a small value; the check
//   input_sat < payment_sat + needed_fee
// evaluates false, so stx is Ok(...) despite outputs exceeding inputs.
// stx.fee() then underflows: sum(prevouts) - sum(outputs) wraps to ~u64::MAX.
// The returned SignableTransaction is consensus-invalid; multisig(...) still
// produces TransactionSignMachine which signs its sighash.
```
Root cause: `send.rs:187,206,215,228` use non-checked `u64` ops over values that are not range-bounded on ingress in `ReceivedOutput::read` (`wallet/mod.rs:129`).