### Title
Dust and low-value received inputs are silently included in spends, causing net value loss ("depreciating asset") - (File: networks/bitcoin/src/wallet/send.rs)

### Summary
`SignableTransaction::new` accepts every `ReceivedOutput` it is given as an input without checking whether that input is worth more than the marginal fee required to spend it. `Scanner::scan_transaction` credits any on-chain output paying to a registered script regardless of value, so an unprivileged party can send arbitrarily small (but consensus/relay-valid) outputs to a Serai-controlled P2TR script. When those outputs are aggregated into a spend, each one adds ~230 weight units (~57 vbytes) of fee cost while contributing less value than that cost, so the transaction's total inputs minus outputs silently decreases — funds reported as received are economically unspendable, and spending them actively drains the pool.

### Finding Description
`Scanner::scan_transaction` pushes a `ReceivedOutput` for any matching `script_pubkey` with no value threshold:

```rust
// networks/bitcoin/src/wallet/mod.rs
if let Some(offset) = self.scripts.get(&output.script_pubkey) {
  res.push(ReceivedOutput {
    offset: *offset,
    output: output.clone(),
    outpoint: OutPoint::new(tx.compute_txid(), vout),
  });
}
```

In `SignableTransaction::new`, the dust check applies only to *payments* (`for (_, amount) in payments { if *amount < DUST { ... } }`), never to *inputs*. Every supplied input's full value is summed into `input_sat` and committed:

```rust
// networks/bitcoin/src/wallet/send.rs
let input_sat = inputs.iter().map(|input| input.output.value.to_sat()).sum::<u64>();
```

and each input adds a fixed ~230 WU to the transaction weight via `calculate_weight_vbytes` (fixed `TxIn` + 64-byte witness). The required fee `fee_per_vbyte * vbytes` therefore grows with each input, while an input worth less than `fee_per_vbyte * 57` contributes negative net value. If `input_sat` still covers payments + fee, the transaction succeeds and the loss is realized either by shrinking the change output or, when there is no change/change is under `DUST` (546 sats), being absorbed entirely into the fee paid to miners (`let value = input_sat.checked_sub(payment_sat + fee_with_change)`, with any sub-dust remainder forfeited).

So an attacker who learns a Serai P2TR script (publicly visible on-chain or derivable from the group key) can repeatedly send 546–few-thousand-satoshi outputs to it. Those outputs are indistinguishable from legitimate deposits to the scanner, and nothing in the in-scope wallet code marks them as unprofitable to spend or refuses to include them.

### Impact Explanation
Value received by the wallet silently depreciates: each attacker-created input costs more in fees to spend than it is worth, so including it reduces the change/payment value of an otherwise legitimate transaction, or is outright burned as excess fee when the remainder is below `DUST`. At moderate `fee_per_vbyte` (e.g., 20 sat/vB), any input under ~1,140 sats is net-negative; at 50 sat/vB, inputs under ~2,850 sats are net-negative. This is a repeatable, bounded economic drain and produces outputs the wallet reports as received but which cannot be spent without loss — matching the "asset that has lost its usefulness/value" bug class.

### Likelihood Explanation
Triggering it requires only a standard Bitcoin transaction to a known script — fully within reach of an unprivileged party. Realization of the loss additionally depends on the consumer of this library choosing to aggregate such outputs as inputs; the wallet layer itself provides no profitability signal or minimum-input check to prevent it, so any caller that feeds `scan_block`/`scan_transaction` results into `SignableTransaction::new` without an out-of-scope dust policy will bleed value.

### Recommendation
Track and expose the marginal spend cost per input (e.g., `fee_per_vbyte * INPUT_VBYTES`) and either refuse inputs whose value does not exceed it or return their estimated net value so callers can exclude them. Alternatively, add a minimum-input-value parameter to `SignableTransaction::new` and/or a `Scanner` option to not report outputs below a spendability threshold.

### Proof of Concept
1. Attacker sends a transaction with an output paying `p2tr_script_buf(group_key)` worth 600 sats (above the 546 dust/relay limit, so it confirms).
2. `Scanner::scan_transaction` returns a `ReceivedOutput` for it; nothing distinguishes it from a real deposit.
3. A caller builds `SignableTransaction::new(inputs_including_attacker_output, &payments, Some(change), None, fee_per_vbyte = 20)`.
4. `calculate_weight_vbytes` adds ~57 vbytes for the attacker's input → `needed_fee` rises by ~1,140 sats while `input_sat` rises by only 600 sats → `change = input_sat - payment_sat - fee_with_change` drops by ~540 sats (or the payment set fails `NotEnoughFunds` / change falls below `DUST` and the remainder is forfeited to miners).
5. Repeat with N dust outputs to scale the drain.