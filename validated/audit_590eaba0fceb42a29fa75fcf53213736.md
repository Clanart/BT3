### Title
Hardcoded `DUST` threshold accepts payments that are dust for their actual output script — ([File: networks/bitcoin/src/wallet/send.rs](networks/bitcoin/src/wallet/send.rs))

### Summary

`SignableTransaction::new` hardcodes the dust threshold to `pub const DUST: u64 = 546` and applies it uniformly to every payment and change output, regardless of the output's `script_pubkey`. Bitcoin's standardness dust rule is per-output: `GetDustThreshold` scales with the serialized size of the output plus the estimated size of the input that will spend it. A flat 546-sat floor is only correct for P2PKH-sized outputs; larger scripts require a higher minimum value. This is the same bug class as the Ubiquity issue: a validity/security threshold is hardcoded at construction where it should be derived per-asset (here, per-output) from the actual parameter it protects.

### Finding Description

In `networks/bitcoin/src/wallet/send.rs`, `SignableTransaction::new` validates every requested payment with a single constant:

```rust
pub const DUST: u64 = 546;                    // line 32

for (_, amount) in payments {
  if *amount < DUST {                          // lines 165-169
    Err(TransactionError::DustPayment)?;
  }
}
```

and gates change creation on the same constant (`if value >= DUST`, line 229). The comment at lines 28-31 acknowledges the constant is a simplification but only reasons about the direction where SegWit outputs could be *lower*; it ignores outputs whose scripts make the real dust threshold *higher* than 546.

Bitcoin Core's dust rule (`policy.cpp`, referenced by the in-code link) computes:

```
dustThreshold = dustRelayFee (3000 sat/kvB) * (serialized TxOut size + spend-input size)
```

The serialized `TxOut` includes the `script_pubkey` itself, which `payments` supplies as an arbitrary `ScriptBuf`. For a script larger than ~40 bytes, the threshold exceeds 546. For example, a ~150-byte `script_pubkey` yields a dust threshold on the order of 700-1000 sats. A payment of 546-999 sats to such a script passes `SignableTransaction::new` and is then signed by the threshold multisig (`TransactionSignMachine::sign`, line 355) and broadcast, but every standardness-enforcing node rejects it as dust, so it never relays or confirms.

Unlike the Ubiquity case (where the admin could later fix the threshold via a setter), here nothing recomputes dust per output anywhere in `send.rs` — `calculate_weight_vbytes` (line 62) is only used for fee/weight accounting, not for a per-output dust check.

### Impact Explanation

An unprivileged party can cause Serai to sign and broadcast a transaction containing a payment whose value is ≥ 546 sats but below the actual dust threshold for its `script_pubkey` (the payment's destination script is attacker-influenced data — users specify withdrawal addresses/scripts and amounts). The resulting transaction:

- consumes the multisig's UTXOs as inputs and pays a fee,
- is rejected by the mempool/relay policy of standard nodes as a dust output, so it never confirms,
- locks the spent inputs until the transaction is abandoned or replaced, wasting the operating fee and the input funds' availability.

This maps to the accepted "funds reported received/sent that are not spendable / signing of a transaction that cannot achieve its intent" class: the protocol signs and pays for a transaction that is unrelayable by construction. Severity is Medium: permanent fund loss requires the node never succeeding at RBF/replacement, but the fee burn and input lockup are deterministic consequences of reaching `TransactionSignMachine::sign` with such a payment.

### Likelihood Explanation

Reachable from public inputs: `payments` is a `&[(ScriptBuf, u64)]` built from externally requested withdrawals (`processor/src/networks/bitcoin.rs:433-444` forwards user payments into `BSignableTransaction::new`). Any withdrawal whose script exceeds ~40 bytes (e.g., a bare multisig or long hashlock script addressable on Bitcoin) combined with a borderline amount triggers it. No collusion, validator misbehavior, or leaked keys are required — just one crafted payment.

### Recommendation

Compute the dust threshold per output instead of using the flat `DUST` constant. For each `TxOut` (payments and change), evaluate the standard `GetDustThreshold` formula — `3 * (GetSerializeSize(txout) + spend_input_size)` with the appropriate witness-discounted input estimate for a Taproot spend — or reuse `bitcoin`'s `TxOut::minimal_non_dust_value(dust_relay_fee)` equivalent. Apply the check to the change output as well (line 229) and document that `DUST` is only a conservative lower bound for fee-estimation purposes (as `processor/src/networks/bitcoin.rs:441` uses it).

### Proof of Concept

```rust
// networks/bitcoin context: a payment to a large script near the flat DUST floor
let big_script = ScriptBuf::from_bytes(vec![0x51; 200]); // ~200-byte script_pubkey
let payments = vec![(big_script, 600u64)];               // >= DUST (546), passes check

let stx = SignableTransaction::new(inputs, &payments, None, None, fee_per_vbyte).unwrap();
// SignableTransaction::new returns Ok: 600 >= 546
// Real dust threshold for a ~209-byte TxOut at 3000 sat/kvB is > 600 sats,
// so the assembled tx fails standardness dust rules on every relay node:
// the multisig signs it, pays the fee, and it is never relayed or confirmed.
```