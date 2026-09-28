### Title
`Scanner::scan_block` reports immature coinbase outputs as spendable `ReceivedOutput`s without any status/maturity check - (File: networks/bitcoin/src/wallet/mod.rs)

### Summary
The analog of "no check whether the milestone is rejected before distributing" is a missing status check before an item is treated as valid/spendable. `Scanner::scan_block` iterates over `block.txdata`, which includes `txdata[0]` — the coinbase transaction — and returns every output paying to a registered script as a `ReceivedOutput`, with no check that the output's producing transaction is a coinbase or that it has reached the 100-block maturity required by Bitcoin consensus.

### Finding Description
`scan_transaction` (networks/bitcoin/src/wallet/mod.rs:199-214) matches outputs solely on `output.script_pubkey` membership in `self.scripts` and immediately constructs a `ReceivedOutput` carrying the spend offset and outpoint. `scan_block` (lines 221-227) calls `scan_transaction` on *every* transaction in `block.txdata`, including `block.txdata[0]` (the coinbase), whose outputs are unspendable for 100 blocks per consensus rules (BIP-30/COINBASE_MATURITY = 100).

The only mitigation is a doc comment stating "a post-processing pass is needed" — the API itself performs no status check, mirroring the RFP bug where `_distribute` acts on a milestone without checking its `Rejected` status. A caller (the in-tree flow scans blocks and feeds `ReceivedOutput`s into `SignableTransaction::new`, which validates amounts/fee/dust but has no way to know an input is a coinbase) will build a transaction spending an immature coinbase output. `SignableTransaction::new` (networks/bitcoin/src/wallet/send.rs:150-256) performs no maturity check and `TransactionSignMachine::sign` will produce a validly-signed transaction that consensus rejects, while the scanner already reported the funds as received.

### Impact Explanation
- Funds are reported as received/usable that are not spendable: an immature coinbase `ReceivedOutput` is indistinguishable from a normal output downstream.
- Any `SignableTransaction` constructed with such an input produces a transaction that will never be accepted by the network until maturity; in a threshold-custody flow this can wedge a signing plan (a signed-but-invalid transaction) or cause the multisig to attempt spending locked funds.
- If the immature output's value was counted toward balances/acks before maturity, accounting treats locked coins as liquid — directly parallel to "milestones accepted even though rejected."

### Likelihood Explanation
Any miner can cause this: mining a block whose coinbase pays the multisig's P2TR script (or a registered offset script) creates a `ReceivedOutput` through purely public on-chain data — an unprivileged party sends a Bitcoin transaction/block, which is within the reachable-input rules. It also happens naturally if a mining pool ever pays the scanned address. No key compromise, collusion, or invalid-curve input is required; the scanner simply lacks a coinbase-status check.

### Recommendation
Check the producing transaction's coinbase status before emitting the output — e.g. in `scan_block`, skip `block.txdata[0]` by default (or return coinbase outputs in a separate "immature" set), and/or have `scan_transaction` take/verify an `is_coinbase` flag:

```rust
// scan_block: enforce the status check instead of documenting it
for tx in &block.txdata[1 ..] {
  res.extend(self.scan_transaction(tx));
}
// plus a dedicated path that only surfaces coinbase outputs once
// sufficient confirmations (>= 100 blocks) have elapsed
```

Alternatively carry a `matures_at: Option<u64>` field on `ReceivedOutput` so `SignableTransaction::new` can reject immature inputs.

### Proof of Concept
1. Register a multisig key, obtain `script = p2tr_script_buf(key)` via `Scanner::new(key)`.
2. An attacker (miner, or a pool paying the address) mines a block where `coinbase_tx.output[0].script_pubkey == script`.
3. Call `Scanner::scan_block(&block)` — it returns `ReceivedOutput { offset: ZERO, output: coinbase_output, outpoint }` with no indication it is immature (`scan_block` loops over `block.txdata` including index 0, mod.rs:221-227).
4. Pass that `ReceivedOutput` into `SignableTransaction::new(inputs, payments, ...)` — all checks (dust, fee, weight) pass; `multisig()`/`sign()` produce a fully signed transaction.
5. Broadcasting it fails with a non-BIP68/BIP-34 maturity rejection (`bad-txns-premature-spend-of-coinbase`), while the scanner already reported the funds as received — i.e., funds reported received that are not spendable.