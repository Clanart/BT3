### Title
Coinbase outputs reported as immediately spendable funds by `scan_block` - (File: networks/bitcoin/src/wallet/mod.rs)

### Summary
`Scanner::scan_block` iterates over `block.txdata` including `block.txdata[0]` (the coinbase transaction) and emits any coinbase outputs paying to a registered script as ordinary `ReceivedOutput`s. A `ReceivedOutput` carries no maturity/confirmations metadata — only `offset`, `output`, and `outpoint` — so an immature coinbase output is indistinguishable from a normally spendable output. Any downstream consumer that feeds the result into `SignableTransaction::new` / `multisig` will construct and sign a transaction spending a consensus-immature output, which the Bitcoin network will reject (coinbase outputs are unspendable for 100 blocks).

### Finding Description
The bug class is a "pending grant that cannot be consumed/replaced": in the external report, an allowance occupies a one-slot state and requires a separate, time-gated withdrawal before anything new can be set — value is credited but not actually usable yet. The Serai analog lives in the scanner, which credits funds with no notion of temporal usability.

`Scanner::scan_block` scans every transaction in the block:

```rust
// networks/bitcoin/src/wallet/mod.rs:221-227
pub fn scan_block(&self, block: &Block) -> Vec<ReceivedOutput> {
  let mut res = Vec::new();
  for tx in &block.txdata {
    res.extend(self.scan_transaction(tx));
  }
  res
}
```

`scan_transaction` matches only on `script_pubkey` and vout, producing a `ReceivedOutput` identical in shape for coinbase and regular outputs (`networks/bitcoin/src/wallet/mod.rs:199-214`). `ReceivedOutput` has no field indicating coinbase provenance or maturity (`networks/bitcoin/src/wallet/mod.rs:88-97`), and `scan_transaction` itself is also the API used when scanning individual transactions — there is no flag on the returned value letting a caller distinguish "coinbase, immature" from "spendable now" without re-fetching and re-inspecting the containing block.

Contrast with the processor integration (out of scope, but showing intended usage): `get_outputs` explicitly skips `block.txdata[1..]`'s index 0 precisely "which is burdened by maturity" (processor/src/networks/bitcoin.rs:690-691) — confirming the in-scope `scan_block` API hands out unspendable outputs by default and pushes maturity handling entirely onto callers via a doc comment.

### Impact Explanation
A miner (any external, unprivileged party — mining is permissionless and the coinbase is simply a transaction they create and cause the scanner to observe) pays an output to a Serai P2TR script (external, branch, change, or forward address, all derivable publicly from the group key). `scan_block` reports it as a `ReceivedOutput` with `value() > 0`, i.e., funds received that are not spendable. If selected as an input to `SignableTransaction::new`, the resulting signed transaction is consensus-invalid until maturity, stalling the signing round and the plan built on it; if an integrator builds accounting/forwarding logic on `scan_block` results, it books non-existent available balance. This matches the accepted impact "funds reported received that are not spendable" and mirrors the Derby issue's "credited but unusable value blocking progress".

### Likelihood Explanation
Any miner on the Bitcoin network can include an output to a known Serai script in their coinbase at zero marginal cost beyond the output value (which returns to them only via Serai's inability to spend it — the funds sit locked for 100 blocks regardless). No collusion, no compromised key, no malicious node required — just a Bitcoin transaction the attacker causes to exist, which is within the permitted input surface.

### Recommendation
Either (a) make `scan_block` skip `block.txdata[0]`, or (b) extend `ReceivedOutput`/the scan result to carry a maturity flag (e.g., an `is_coinbase`/minimum-spendable-height marker) so downstream input selection can exclude immature outputs. At minimum, `scan_transaction` callers on coinbase transactions should be forced to opt in explicitly rather than receiving silently-credited immature outputs.

### Proof of Concept
1. Construct `Scanner::new(key)` for a Serai group key; obtain its P2TR script via `p2tr_script_buf`.
2. As a miner, mine a block whose coinbase transaction pays `N` sats to that script.
3. Call `scanner.scan_block(&block)` — the coinbase output is returned as a `ReceivedOutput` (`outpoint = (coinbase_txid, vout)`), indistinguishable from a mature UTXO.
4. Pass it to `SignableTransaction::new(vec![output], payments, change, None, fee)` — construction succeeds, `multisig`/`preprocess`/`sign`/`complete` produce a fully signed transaction.
5. Broadcasting fails: Bitcoin consensus rejects spending a coinbase output before 100 confirmations (`bad-txns-premature-spend-of-coinbase`). The scanner credited funds the multisig cannot actually spend, and the input-poisoned plan must be rebuilt.