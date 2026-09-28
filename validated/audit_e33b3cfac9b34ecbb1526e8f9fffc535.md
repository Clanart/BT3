### Title
`Scanner::scan_block` reports immature coinbase outputs as spendable funds, producing unspendable `ReceivedOutput`s that block later spends - (File: networks/bitcoin/src/wallet/mod.rs)

### Summary
The Rocket Pool bug class is: a withdrawal/spend path that "should always succeed" can be blocked by an external condition the caller never validated, so funds that the system reports as available cannot actually be moved. The Serai analog lives in `Scanner::scan_block` in `networks/bitcoin/src/wallet/mod.rs`: it iterates over `block.txdata` including `txdata[0]` (the coinbase transaction) and emits `ReceivedOutput`s for any coinbase outputs paying to a registered script. Coinbase outputs are consensus-unspendable for 100 blocks, so the scanner reports funds as received that are not spendable. Any downstream consumer that feeds such an output into `SignableTransaction::new` will produce and threshold-sign a transaction that Bitcoin nodes reject, blocking the spend.

### Finding Description
`Scanner::scan_transaction` matches outputs purely by `script_pubkey` and builds a `ReceivedOutput` with no notion of maturity:

```rust
// networks/bitcoin/src/wallet/mod.rs
if let Some(offset) = self.scripts.get(&output.script_pubkey) {
  res.push(ReceivedOutput { offset: *offset, output: output.clone(),
    outpoint: OutPoint::new(tx.compute_txid(), vout) });
}
```

`scan_block` then applies this to every transaction in the block, including `txdata[0]`:

```rust
pub fn scan_block(&self, block: &Block) -> Vec<ReceivedOutput> {
  let mut res = Vec::new();
  for tx in &block.txdata {
    res.extend(self.scan_transaction(tx));
  }
  res
}
```

A doc comment notes the coinbase is "bound by maturity" and suggests a "post-processing pass", but the API itself performs no check: there is no `is_coinbase` flag, no maturity height, and nothing on `ReceivedOutput` distinguishing an immature coinbase output from a normal one. The block height at which the output matures is also not recorded, so a caller cannot recover the information from the `ReceivedOutput` alone.

The spend path in `networks/bitcoin/src/wallet/send.rs` (`SignableTransaction::new`, `multisig`, `TransactionSignMachine::sign`) performs dust/fee/weight validation but no coinbase-maturity check — it happily constructs and signs a transaction spending an immature output, which every Bitcoin node will then reject under the coinbase-maturity consensus rule (and standardness). The signed transaction is garbage; the funds remain locked until maturity even though the scanner reported them.

This is reachable by an unprivileged party: any miner (or a mining pool, which is trivially able to set an arbitrary coinbase output) can pay the multisig's publicly known P2TR script in a coinbase. The scanner will then report a received balance that cannot be spent, and a spend attempt consumes signing rounds to produce a transaction that can never confirm until 100 further blocks pass — and if the wallet treats the output as confirmed spendable balance for accounting, it can additionally stall or mis-select inputs.

### Impact Explanation
Funds are reported received that are not spendable. A spend ("withdrawal") built on such an output is signed across the threshold yet fails relay/consensus, blocking the withdrawal path — the same "guaranteed-withdrawal that can be blocked" failure as the Rocket Pool report. Severity: Medium — it requires a coinbase payment to the scanned script (any miner can do this; it is not permissioned), and the lockout is bounded by the 100-block maturity rather than permanent, but the signed-but-unbroadcastable transaction and incorrect spendable balance are concrete.

### Likelihood Explanation
Medium-low. Exploitation requires a miner directing a coinbase payout to the Serai address, which costs the miner nothing unusual (coinbase outputs are arbitrary). The failure is automatic once such an output is scanned, since `scan_block` is the natural API for block-level scanning. The damage is time-bounded by maturity, which caps the severity.

### Recommendation
Have `scan_block` skip `block.txdata[0]` (or mark `ReceivedOutput`s originating from coinbase transactions with their maturity height) so that immature outputs are never reported as spendable. Additionally, `SignableTransaction::new` should reject coinbase `ReceivedOutput`s that have not reached maturity, mirroring the report's recommendation that functions which must succeed should not depend on unvalidated external state.

### Proof of Concept
```rust
// networks/bitcoin/src/wallet/mod.rs conceptual PoC
let scanner = Scanner::new(group_key).unwrap();
// A block whose coinbase tx pays to p2tr_script_buf(group_key)
let outputs = scanner.scan_block(&block);
// outputs[0] is a ReceivedOutput for the coinbase output.
// Its value() is counted as received balance immediately.
let tx = SignableTransaction::new(
    vec![outputs[0].clone()], &payments, Some(change), None, FEE,
).unwrap();
// The threshold signs this fine; broadcast fails:
// "bad-txns-premature-spend-of-coinbase" — the withdrawal is blocked
// until 100 confirmations despite the scanner reporting the funds.
```
Supporting code: `scan_block`/`scan_transaction` at `networks/bitcoin/src/wallet/mod.rs:199-227` perform no maturity or coinbase filtering; `SignableTransaction::new` at `networks/bitcoin/src/wallet/send.rs:150-256` performs no coinbase check either, so the signed transaction is only rejected at the Bitcoin consensus layer.

Note: the doc comment on `scan_block` acknowledges maturity as a caller responsibility; I have flagged this as a vulnerability anyway because the `ReceivedOutput` type carries no maturity information, so the API makes the required "post-processing pass" impossible to perform correctly from its return value alone. If the maintainers consider documented caller obligations sufficient, this finding should be downgraded accordingly — the rest of the in-scope code contains no stronger instance of this bug class reachable by unprivileged parties.