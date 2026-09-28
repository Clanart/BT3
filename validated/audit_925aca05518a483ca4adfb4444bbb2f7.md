### Title
Attacker-pollutable input set can strand funds: dust/small outputs scanned as spendable while the transaction weight cap makes the combined input set unspendable - (File: networks/bitcoin/src/wallet/send.rs)

### Summary

The bug class in the external report is a protocol-wide resource limit that an unprivileged party can saturate to deny service to other users. In `bitcoin-serai`, the analogous shared limit is `MAX_STANDARD_TX_WEIGHT` enforced in `SignableTransaction::new`, combined with the `Scanner`, which registers *every* output paying to a registered `script_pubkey` as a spendable `ReceivedOutput` with no value or count filtering. Any unprivileged party can send Bitcoin transactions (their public input) to Serai's tweaked-key address, injecting arbitrarily many dust/near-dust `ReceivedOutput`s into the input set the wallet later tries to spend.

### Finding Description

`Scanner::scan_transaction` records an output as spendable whenever `self.scripts.get(&output.script_pubkey)` matches — it only inspects the script, never the amount or the total count of received outputs (`networks/bitcoin/src/wallet/mod.rs:199-213`). An attacker who knows the (public) Taproot address can create many outputs paying to it.

When the wallet later constructs a transaction, `SignableTransaction::new` takes the full `inputs: Vec<ReceivedOutput>` and rejects the transaction with `TooLargeTransaction` if `weight > MAX_STANDARD_TX_WEIGHT` (`send.rs:241-243`). Since each Taproot input contributes ~58 vbytes, fewer than ~1700 inputs already exceed the standard weight limit. There is no input-selection, deduplication, or dust-filtering logic: the constructor either consumes all supplied inputs or errors. Additionally, per-input fee costs are never compared against per-input value, so a received output worth less than the marginal fee to spend it is `NotEnoughFunds`-adjacent dead weight — funds reported received that are not economically spendable.

This mirrors the Linea report: a shared, attacker-influenceable cap (weight limit instead of ETH rate limit) that, once saturated, blocks legitimate withdrawals entirely.

### Impact Explanation

An attacker spamming dust outputs to the deposit address inflates the input set until any `SignableTransaction` consuming it hits `TooLargeTransaction`, denying service: user burns/payments cannot be funded because the wallet is handed an unspendable or oversized input vector. Sub-dust outputs are also permanently stuck — spending them costs more than their value, and nothing filters them out, so the scanner reports funds as received that cannot be profitably moved.

### Likelihood Explanation

Requires only the ability to send ordinary Bitcoin transactions to a public address — fully permissionless and cheap (dust is 546 sats, `send.rs:32`). No collusion, no validator role, no malformed encodings needed; the attacker's inputs are entirely valid Bitcoin outputs that the in-scope `Scanner` code treats as spendable.

### Recommendation

- Filter received outputs below an economic threshold (e.g., value < fee cost of spending one input at the target fee rate) either in `scan_transaction` or before passing inputs to `SignableTransaction::new`.
- Add input selection/batching in `SignableTransaction::new` (or the calling scheduler) so the input set is capped to keep `weight <= MAX_STANDARD_TX_WEIGHT`, rather than erroring on the whole set.
- Track per-input marginal fee contribution so unprofitable inputs are excluded from aggregation.

### Proof of Concept

```rust
// Attacker sends N transactions each paying `DUST` sats to the scanner's
// p2tr script. Scanner::scan_transaction registers every one of them as a
// ReceivedOutput regardless of amount:
//   if let Some(offset) = self.scripts.get(&output.script_pubkey) { res.push(...) }

// Later, the wallet aggregates all received outputs:
let inputs: Vec<ReceivedOutput> = scanner.scan_block(&block); // includes attacker's dust

// ~1700 taproot inputs (~58 vbytes each) push weight past
// bitcoin::policy::MAX_STANDARD_TX_WEIGHT (400_000 WU):
let res = SignableTransaction::new(inputs, &payments, change, None, fee_per_vbyte);
assert_eq!(res.unwrap_err(), TransactionError::TooLargeTransaction);
// => no transaction can be produced; withdrawals are DoS'd until inputs are
//    filtered, which the current code path never does.
```

Caveat: this assumes the scheduler passes the full scanned input set to `SignableTransaction::new` (the crate's only spend path); the crate itself performs no input filtering, so any caller doing so inherits the DoS. The routing of inputs in `processor/` was outside the permitted scope, so the end-to-end trigger depends on caller behavior — but the missing economic/weight filtering is a real defect in the in-scope wallet code regardless.