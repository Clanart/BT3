### Title
Change/payment outputs between the wallet's dust floor (546 sats) and the processor's dust floor (10,000 sats) are created on-chain but never credited — (File: networks/bitcoin/src/wallet/send.rs)

### Summary
`SignableTransaction::new` in `networks/bitcoin/src/wallet/send.rs` enforces Bitcoin's bare-relay dust limit `DUST = 546` when deciding whether a payment is allowed and whether a change output should be emitted. The Serai processor, however, uses a much higher economic dust threshold `Bitcoin::DUST = 10_000` and silently drops any scanned output below it. The result is the same double-accounting class as the reference bug: satoshis are counted in the transaction's own accounting (deducted from the inputs as change, or delivered as a payment) yet are simultaneously treated as non-existent by the accounting layer that tracks spendable funds.

### Finding Description
In `SignableTransaction::new`, a change output is appended whenever the leftover `value` satisfies `value >= DUST` where `DUST` is the crate-local constant `546`:

```rust
// networks/bitcoin/src/wallet/send.rs:32
pub const DUST: u64 = 546;
```

```rust
// networks/bitcoin/src/wallet/send.rs:224-234
if let Some(change) = change {
  let (weight_with_change, vbytes_with_change) =
    Self::calculate_weight_vbytes(tx_ins.len(), payments, Some(&change));
  let fee_with_change = fee_per_vbyte * vbytes_with_change;
  if let Some(value) = input_sat.checked_sub(payment_sat + fee_with_change) {
    if value >= DUST {
      tx_outs.push(TxOut { value: Amount::from_sat(value), script_pubkey: change });
      ...
    }
  }
}
```

Likewise, payments are only rejected below `546` (`send.rs:165-169`). The processor-side scanner, in contrast, filters every received output by `N::DUST`, which for Bitcoin is `10_000` (`processor/src/networks/bitcoin.rs:638`):

```rust
// processor/src/multisigs/scanner.rs:564
if output.balance().amount.0 >= N::DUST {
  outputs.push(output);
}
```

So whenever the change remainder (or an externally-triggered payment) lands in `[546, 10_000)`, the wallet library will happily put it on-chain — and the transaction's own `fee()` accounting treats those satoshis as output value rather than fee — while the processor's scanner discards them. The satoshis exist and are controlled by the multisig's offset key (change is paid to `change_address(key)`, registered via `register_offset`), yet they are never emitted as an output event, never enter the scheduler's UTXO set, and can never be respent. This mirrors the reference finding exactly: value counted under one ledger (the transaction construction: `input_sat - payment_sat - fee_with_change`) while being absent from the other ledger (the recognized-spendable-outputs set), with no reconciliation.

A secondary instance of the same class: the OP_RETURN `data` output is pushed onto `tx_outs` (`send.rs:194-202`) but `calculate_weight_vbytes` is called with `payments` only (`send.rs:204`), so its ~80-byte weight is excluded from both `needed_fee` and the `MAX_STANDARD_TX_WEIGHT` check — again, bytes/value accounted for inconsistently between the two views of the same transaction.

### Impact Explanation
Funds reported received (or retained as change) that are not spendable by the protocol. Every transaction whose change happens to land in `[546, 10_000)` sats permanently strands that amount under a key the multisig controls but the scheduler will never observe. The wallet layer subtracts the change from the fee (so the plan believes the funds were preserved), while the scanner layer drops it (so no UTXO is ever created). At scale, repeated transactions each leaking up to ~9,454 sats represent a slow, silent drain of the vault's balance, and there is no error path — construction succeeds and scanning succeeds.

### Likelihood Explanation
The change amount is `input_sat - payment_sat - fee_with_change`, essentially a uniformly distributed remainder of input selection. Any time this remainder is ≥546 and <10,000 the bug triggers. No adversary is required; ordinary withdrawal/batch traffic hits it probabilistically. An attacker who can influence input composition or amounts (anyone initiating burns/withdrawals) can also deliberately craft payments that force change into this band, amplifying the stranding.

### Recommendation
Align the two thresholds: `SignableTransaction` should not emit a change output (or should refuse a payment) below the network's economic dust floor, not the consensus/relay floor. Concretely, make the dust threshold a parameter of `SignableTransaction::new` (or gate the `value >= DUST` / `*amount < DUST` checks on the processor's `N::DUST`), so that change under `10_000` sats is folded into the fee instead of being created as an unspendable output. Additionally, include the OP_RETURN output in `calculate_weight_vbytes` so `needed_fee` and the weight bound reflect the actual signed transaction.

### Proof of Concept
```rust
// Conceptual PoC against networks/bitcoin/src/wallet/send.rs

// Input worth 1_000_000 sats, payment 990_000 sats, fee_with_change ~500 sats.
// Leftover = 1_000_000 - 990_000 - 500 = 9_500 sats.
let input = ReceivedOutput { /* 1_000_000 sats to external key */ };
let payments = vec![(dest_script, 990_000)];

// 546 <= 9_500  -> change output IS created
let tx = SignableTransaction::new(
    vec![input], &payments, Some(change_script.clone()), None, fee_per_vbyte,
).unwrap();

// tx.output contains { value: 9_500, script_pubkey: change_script }
assert!(tx.transaction().output.iter().any(|o| o.value.to_sat() == 9_500));

// But Scanner/processor drops it: 9_500 < Bitcoin::DUST (10_000)
// processor/src/multisigs/scanner.rs:564 filters it out.
// The 9_500 sats at the change offset key are stranded:
// counted as output value in tx.fee() accounting, never credited as a spendable output.
```

Verification path: construct a `SignableTransaction` whose change lands in `[546, 10_000)`, sign it via `multisig()`/`TransactionMachine`, broadcast, then observe that `Scanner::scan_transaction` returns the output while the processor's `get_outputs`→`scanner.rs:564` filter discards it — the same satoshis counted by the sender's fee math yet absent from the spendable set.