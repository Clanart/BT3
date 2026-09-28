### Title
Unbounded `fee_per_vbyte` in `SignableTransaction::new` allows arbitrary fee burn of user funds — no maximum fee bound exists - ([File: networks/bitcoin/src/wallet/send.rs](networks/bitcoin/src/wallet/send.rs))

### Summary
`SignableTransaction::new` enforces a *minimum* fee (`TooLowFee`) but never bounds the fee from above. The caller-supplied `fee_per_vbyte` is multiplied by the transaction vsize to produce `needed_fee`, and as long as `input_sat >= payment_sat + needed_fee`, the transaction is constructed and becomes signable — paying `needed_fee` entirely to miners. When `change` is `None`, the documented behavior sends *all* leftover input value to the fee, so even a modest fee rate consumes the entire remainder. An unprivileged party can drive the effective fee rate upward through public inputs (Bitcoin transactions they broadcast, which feed the processor's `median_fee` sampling of block transactions), causing the threshold-signed transaction to burn user funds as miner fees with no cap, sanity check, or percentage bound — the same "unbounded admin/protocol-set fee drains user value" class as the external report.

### Finding Description
In `networks/bitcoin/src/wallet/send.rs`:

- `needed_fee = fee_per_vbyte * vbytes` is computed at line 206 with an unchecked multiplication (wraps on overflow in release builds).
- Only a lower bound is enforced: `needed_fee < DEFAULT_MIN_RELAY_TX_FEE * vbytes / 1000` → `TooLowFee` (lines 211–213).
- The sufficiency check at line 215 (`input_sat < payment_sat + needed_fee`) permits `needed_fee` to be arbitrarily close to the entire input value.
- The change logic (lines 224–234) silently drops the change output when `input_sat.checked_sub(payment_sat + fee_with_change)` yields `< DUST`, at which point `sum(inputs) - sum(outputs)` — the actual fee paid, per `fee()` at lines 138–141 — becomes *all* leftover funds, not `needed_fee`.
- The doc comment at lines 145–147 confirms: "If a change address isn't specified, all leftover funds will become part of the paid fee."

There is no `TooHighFee`, no percentage-of-payment bound, and no absolute cap anywhere in the signing path — the resulting `SignableTransaction` is fed directly to the FROST threshold `sign` routine.

### Impact Explanation
A transaction paying an arbitrarily large fee is a valid, consensus-acceptable Bitcoin transaction. Once produced by `SignableTransaction::new` and signed by the threshold key, the excess sats are irreversibly paid to miners — a direct loss of user/protocol funds identical in effect to the external report's "fee equals the top-up amount" scenario. Because the construction silently swallows the leftover-as-fee case (no change, or change dropped below dust), the burn requires no error path and no detection.

### Likelihood Explanation
`fee_per_vbyte` is not a constant: the processor computes it via `median_fee` from the fee rates of transactions in a sampled block (`processor/src/networks/bitcoin.rs:388-415`, out-of-scope delivery mechanism). An unprivileged party who broadcasts Bitcoin transactions with inflated fee rates — explicitly a public input per scope rules — can shift the sampled median (with few transactions per block, a single outlier suffices to move the middle element). The in-scope defect is that `send.rs` applies this externally-derived value with no upper bound. The attack costs the attacker real miner fees, which tempers severity to Medium, but requires no privileged access, no collusion, and no key material.

### Recommendation
- Enforce a maximum acceptable fee in `SignableTransaction::new`: e.g., reject when `needed_fee` exceeds a configurable cap or a fixed fraction of `payment_sat` (mirroring the external report's percentage-based recommendation).
- Use `checked_mul` / `checked_add` for `fee_per_vbyte * vbytes` and `payment_sat + needed_fee` to prevent wraparound bypasses of `NotEnoughFunds`.
- Emit an explicit error (rather than silently converting the remainder to fee) when the change output would fall below `DUST` while `needed_fee` is disproportionate to `input_sat`.

### Proof of Concept
```rust
// networks/bitcoin/src/wallet/send.rs context
// One input of 1 BTC (100_000_000 sats), one payment of 546 sats, change specified.
let inputs = vec![received_output_value(100_000_000)];
let payments = vec![(p2tr_script_buf(key).unwrap(), 546)];

// Attacker-influenced fee rate: 500_000 sat/vbyte (reachable via median_fee sampling)
// vbytes ≈ 140+ => needed_fee ≈ 70_000_000 sats — passes all checks.
let tx = SignableTransaction::new(
    inputs, &payments,
    Some(change_addr), None,
    500_000, // fee_per_vbyte — no upper bound enforced
).unwrap();

// assert!(tx.needed_fee() > payments_total * 1000) — no such check exists
// sign(&keys, &tx) produces a valid TX burning ~0.7 BTC to miners
// for a 546-sat payment. Errors only when needed_fee >= input_sat.
```
No error is raised: `TooLowFee` guards only the minimum, `NotEnoughFunds` only fires when the fee exceeds total inputs, and `fee()` returns `inputs - outputs` which is accepted verbatim. Any `fee_per_vbyte` in the range `(input_sat - payment_sat) / vbytes` or below is honored without question.