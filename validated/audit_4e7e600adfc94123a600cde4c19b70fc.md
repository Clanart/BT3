### Title
Fixed dust bound accepts payments whose outputs are dust for large scriptPubKeys - (File: networks/bitcoin/src/wallet/send.rs)

### Summary
`SignableTransaction::new` validates every payment amount against a single fixed `DUST = 546` satoshi constant. Bitcoin Core's dust threshold is not fixed: it is computed from the serialized size of the output (`(output_size + spend_input_size) * dust_relay_fee`). For payments carrying a large `script_pubkey` (non-witness scripts of more than a few dozen bytes), the real dust threshold exceeds 546 sats, so a payment that passes the check still produces a dust output and makes the whole transaction non-standard.

### Finding Description
The bug class of the reference issue is a *fixed bounds check that fails to enforce its intended invariant for edge-case parameter values* (strike prices passing the `[-9, 9]` decimal bound while being 1 wei for a low-decimal quote token). The Serai analog is in `SignableTransaction::new`:

```rust
pub const DUST: u64 = 546;
// ...
for (_, amount) in payments {
  if *amount < DUST {
    Err(TransactionError::DustPayment)?;
  }
}
```

`send.rs` acknowledges the bound is approximate ("This doesn't bother with delineation due to how marginal these values are"), but that comment only covers the *lower* threshold for SegWit outputs. It ignores the opposite direction: `payments` accept an arbitrary `ScriptBuf` supplied by the caller (any destination script, not just P2TR/P2WPKH). Bitcoin Core computes dust as `(txout_size + 148) * 3000 / 1000` for non-SegWit outputs, so an output with a ~150-byte scriptPubKey has a dust threshold around 900+ sats, yet `SignableTransaction::new` will happily emit it with 546 sats.

An unprivileged party who can cause a payment to an arbitrary destination script (e.g., a withdrawal to a non-standard scriptPubKey they control) with an amount in `[546, actual_dust)` causes the assembled transaction to contain a dust output. The resulting transaction violates default mempool policy (`dust` rejection) and will not be relayed or mined.

### Impact Explanation
The transaction is fully signed by the threshold set (the FROST signing machines in `TransactionSignMachine::sign` commit to it), yet it can never confirm: it is rejected at relay policy. Because inputs are consumed optimistically by the signing protocol and `sequence` is `Sequence::MAX` with `lock_time` zero (no RBF signaling), the orchestrating system cannot bump the fee on this signed transaction; it must coordinate a fresh signing session. The spend is stuck and the batch is blocked — a liveness/availability failure of an intended spend, matching the "transaction built by the protocol is unspendable/non-standard" impact class.

### Likelihood Explanation
Requires an integrator flow where an external requester chooses the destination `script_pubkey` and amount, and chooses a script large enough that the true dust threshold exceeds 546 while requesting a payment just above 546 sats. Standard addresses (P2TR, P2WPKH, P2PKH) have thresholds at or below 546 and are unaffected, so the window only opens for non-standard or bare-script destinations — a reachable but narrow edge, hence Medium/low-medium severity.

### Recommendation
Compute the dust bound per-output rather than with a global constant, e.g. use `TxOut::minimal_non_dust` (rust-bitcoin exposes `TxOut::minimal_non_dust` / `minimal_non_dust_internal`) with the payment's actual `script_pubkey`, or restrict payments to standard script templates (P2TR/P2WPKH) whose dust bounds are known to be ≤ 546.

### Proof of Concept
```rust
// Caller requests a payment to a large bare script (~200 bytes of pushes) with
// an amount just above the fixed bound.
let big_script = ScriptBuf::from_bytes(vec![0x51u8; 200]);
let payments = [(big_script.clone(), 546u64)]; // passes `546 < DUST`? no — 546 is allowed

// SignableTransaction::new succeeds:
let tx = SignableTransaction::new(inputs, &payments, None, None, 5).unwrap();

// Bitcoin Core policy: dust threshold for this output is
// (8 + varint + 200 + 148) * 3 sat/vB ≈ 1071 sats > 546 sats
// => every standard node rejects the fully-signed transaction with `dust`.
```
The check at `send.rs:165-169` therefore admits an output Bitcoin's policy rules classify as dust, producing a signed transaction that cannot propagate.