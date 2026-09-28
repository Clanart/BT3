### Title
Scanner accepts dust/zero-value outputs as received funds that are uneconomical or impossible to spend - (File: networks/bitcoin/src/wallet/mod.rs)

### Summary
Analogous to BlueBerryBank's `borrow()` minting zero debt shares for a nonzero transfer, `Scanner::scan_transaction` registers any output paying to a tracked `script_pubkey` as a `ReceivedOutput` regardless of its `value`. There is no check that `output.value` is at least `DUST` (546 sats, defined in `networks/bitcoin/src/wallet/send.rs:32`), nor that it is non-zero. An unprivileged external party can send arbitrarily many 0- or 1-sat outputs to a registered offset address; the scanner reports them as received balance even though spending them costs more in fees than they are worth (a key-spend input adds ~58 vbytes, i.e. ≥58 sats at 1 sat/vbyte minimum relay fee), so funds are reported received that are not spendable.

### Finding Description
`Scanner::scan_transaction` (mod.rs:199-214) matches outputs purely on `output.script_pubkey` against `self.scripts` and pushes a `ReceivedOutput` carrying `output.value` verbatim:

```rust
if let Some(offset) = self.scripts.get(&output.script_pubkey) {
  res.push(ReceivedOutput { offset: *offset, output: output.clone(), ... });
}
```

No minimum-value filter exists anywhere in the scan path (`scan_transaction`, `scan_block`, `ReceivedOutput::read`). `ReceivedOutput::value()` exposes the raw satoshi value, and `SignableTransaction::new` (send.rs:150-256) sums `input.output.value` into `input_sat` and only enforces `input_sat >= payment_sat + needed_fee` — it never checks that each input is individually worth including. Each input adds fixed weight (`calculate_weight_vbytes` assumes a ~58-vbyte Taproot input), so a sub-546-sat (or zero-value) input contributes less value than the fee it forces the transaction to pay; a 0-value input is pure loss. The rounding-to-zero-free-borrow class maps here as "zero-value outputs accepted as real balance".

### Impact Explanation
Like repeated zero-share borrows draining the vault, an attacker can deposit many dust/zero-value outputs to Serai's key or any registered offset. These inflate the reported wallet balance while being unspendable at any economical fee rate (net negative once input weight fees are charged). Downstream accounting that credits deposits based on `ReceivedOutput::value()`/count can be polluted at near-zero cost to the attacker, and including them in a `SignableTransaction` burns fee budget.

### Likelihood Explanation
Sending dust outputs to a known P2TR script requires only a standard Bitcoin transaction — fully within an unprivileged party's reach, the exact reachable input ("Bitcoin transactions they send"). Cost is bounded only by the dust the attacker must commit (which can be 0-value outputs for OP_RETURN-free scripts, or ≥1 sat), making large-scale pollution cheap.

### Recommendation
In `Scanner::scan_transaction` (or `scan_block`), skip outputs with `output.value.to_sat() < DUST`, or at minimum reject zero-value outputs, before creating a `ReceivedOutput`. Alternatively, apply the filter at the point `ReceivedOutput`s are consumed (`SignableTransaction::new` should reject inputs whose value is below the marginal fee cost of spending them, `fee_per_vbyte * input_vbytes`).

### Proof of Concept
```rust
// Attacker crafts a TX with a 1-sat (or 0-sat) output to Serai's tweaked key script.
let tx = Transaction {
  output: vec![TxOut {
    value: Amount::from_sat(1), // below DUST (546)
    script_pubkey: serai_p2tr_script, // registered offset script
  }],
  ..Default::default()
};
let outputs = scanner.scan_transaction(&tx);
assert_eq!(outputs.len(), 1);          // reported as received
assert_eq!(outputs[0].value(), 1);     // worth less than the ~58+ sat fee to spend it
// Repeat with thousands of outputs: balance inflated, none spendable economically.
```
Relevant code: `networks/bitcoin/src/wallet/mod.rs:199-214` (no value filter), `networks/bitcoin/src/wallet/send.rs:32` (`DUST` constant exists but is unused on the receive path), `send.rs:175-221` (input value summed without per-input viability check).