### Title
Scanner accepts zero-value/dust outputs which are registered as spendable funds yet can never cover their own input weight - (`networks/bitcoin/src/wallet/mod.rs`)

### Summary
`Scanner::scan_transaction` registers any `TxOut` whose `script_pubkey` matches a tracked script as a `ReceivedOutput`, without checking `output.value`. An external party can send a zero-value or dust output to a scanned P2TR script; it is reported as received funds and will be accepted by `SignableTransaction::new` as an input, even though it cannot economically (or in the zero-value case, at all) contribute value to a spend — permanently "stuck" value analogous to the report's zero-amount auction.

### Finding Description
The original bug class: a value computed from attacker-influenceable state is allowed to be 0, and downstream logic assumes `> 0`, so the affected asset is silently skipped/stuck.

In `Scanner::scan_transaction` (networks/bitcoin/src/wallet/mod.rs:199-214), the only check before pushing a `ReceivedOutput` is `self.scripts.get(&output.script_pubkey)`; `output.value` is never inspected:

```rust
if let Some(offset) = self.scripts.get(&output.script_pubkey) {
  res.push(ReceivedOutput { offset: *offset, output: output.clone(), outpoint: ... });
}
```

`SignableTransaction::new` (networks/bitcoin/src/wallet/send.rs:150-256) enforces dust limits only on *payments* (`*amount < DUST → DustPayment`, send.rs:165-169) and on the *change* output (`value >= DUST`, send.rs:229). Inputs are unconstrained: `input_sat` sums whatever `ReceivedOutput.output.value` carries (send.rs:175), and every input is committed into the sighash via `Prevouts::All` (send.rs:375).

A P2TR key-spend input costs ~57.5 vbytes of weight. Any output with `value < input_weight * fee_per_vbyte` (and strictly, any 0-value output) contributes less than the fee it forces the transaction to pay. Because every queued input must be signed for (sighash commits to all prevouts), a scan result containing such outputs produces transactions whose effective spendable balance is lower than what the scanner reported — and a zero-value output is a pure liability that can never be redeemed for value.

### Impact Explanation
Funds reported received that are not spendable: an unprivileged third party can send a dust/zero-value output to any registered script (the scanner accepts any transaction paying to a tracked `script_pubkey`, including the key with `Scalar::ZERO` offset). The wallet's `input_sat` accounting and fee math treat these outputs as real inputs, but spending them is loss-making or impossible to justify — the value is effectively stuck, mirroring the stuck NFT in the source report.

### Likelihood Explanation
Low-Medium: requires an external party to deliberately fund a watched address with dust/zero outputs. This is cheap (a single dust output), and scanning is fully permissionless. Impact is limited to wasted fees/economically unspendable UTXOs rather than direct theft, so Medium at most; arguably Low.

### Recommendation
Reject or quarantine outputs below a threshold in `Scanner::scan_transaction` (e.g., skip `output.value.to_sat() == 0` or below `DUST`), or surface `value` at registration so callers can filter. At minimum, `SignableTransaction::new` should skip inputs whose value is below their marginal fee cost (`input_weight * fee_per_vbyte`).

### Proof of Concept
```rust
// Attacker broadcasts a tx paying 0 sats (or 1 sat) to the tracked script
let tx = Transaction {
  output: vec![TxOut { value: Amount::ZERO, script_pubkey: p2tr_script_buf(key).unwrap() }],
  ..Default::default()
};
// Scanner reports it as received funds
let outputs = scanner.scan_transaction(&tx);
assert_eq!(outputs.len(), 1);          // reported received
assert_eq!(outputs[0].value(), 0);     // contributes nothing
// SignableTransaction::new will happily consume it as an input,
// inflating weight/fee while adding zero value.
```