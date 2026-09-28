### Title
Coinbase outputs are reported as spendable before maturity - (File: networks/bitcoin/src/wallet/mod.rs)

### Summary
`Scanner::scan_block` scans every transaction in a block, including `block.txdata[0]`, the coinbase transaction. Any coinbase output paying to a registered Scanner script is returned as a `ReceivedOutput` even though Bitcoin consensus prevents spending it until the coinbase maturity interval has elapsed.

### Finding Description
`Scanner::scan_transaction` maps a transaction output to `ReceivedOutput` solely by looking up `output.script_pubkey` in `self.scripts`. `Scanner::scan_block` then calls `scan_transaction` for every `tx` in `block.txdata`, without skipping index `0` or checking whether the transaction is a coinbase.

The resulting `ReceivedOutput` contains the output value, outpoint, and key offset needed by `SignableTransaction::new` and `SignableTransaction::multisig`. Neither `SignableTransaction::new` nor `multisig` checks coinbase maturity. `multisig` verifies only that the offset-derived Taproot script matches the serialized prevout script before creating a signing machine.

The GitLab bug class is an improper authorization/state transition: an object derived from an earlier pipeline can be claimed through a later retry without the required authority. The Serai analog is a block-derived output being claimed by the scanner as wallet-owned spendable input while a required Bitcoin authorization condition—coinbase maturity—has not been satisfied.

### Impact Explanation
A wallet or service that uses `scan_block` directly can account for and attempt to spend an immature coinbase output. `SignableTransaction` will construct a transaction spending the reported outpoint, and `multisig` will accept it as long as the supplied prevout script matches `key + G * offset`. The resulting threshold-signature operation is therefore authorized over an input Bitcoin consensus does not yet permit to be spent.

This produces funds reported received that are not currently spendable and can cause signing/transaction construction for an invalid spend.

### Likelihood Explanation
Any miner able to place a coinbase output to a watched Taproot script can trigger this during normal operation. The code path does not require malformed encodings, validator collusion, leaked secrets, or access to the wallet’s key shares: the trigger is a valid Bitcoin block containing an immature coinbase payment to the scanner.

Whether this becomes exploitable depends on the caller using `scan_block` without the documented post-processing maturity filter. The API returns a type explicitly described as “a spendable output,” making the mistaken assumption plausible.

### Recommendation
Change `Scanner::scan_block` to skip `block.txdata[0]`, matching the guidance in its own documentation and the later production call pattern that scans `block.txdata[1 ..]`.

If immature coinbase outputs must remain visible for accounting, represent them with a distinct non-spendable output type or include enough block-height/maturity metadata to prevent them from being passed to `SignableTransaction::new` until mature. Do not rely on callers to infer that a `ReceivedOutput` returned by `scan_block` may be invalid.

### Proof of Concept
```rust
// networks/bitcoin/src/wallet/mod.rs

pub fn scan_block(&self, block: &Block) -> Vec<ReceivedOutput> {
  let mut res = Vec::new();
  for tx in &block.txdata {
    // txdata[0] is included here.
    res.extend(self.scan_transaction(tx));
  }
  res
}
```

For a block whose coinbase output is:

```rust
TxOut {
  value: Amount::from_sat(50 * 100_000_000),
  script_pubkey: p2tr_script_buf(scanner_key).unwrap(),
}
```

`scan_block` returns:

```rust
ReceivedOutput {
  offset: Scalar::ZERO,
  output,
  outpoint: OutPoint::new(coinbase_tx.compute_txid(), 0),
}
```

That output is under the 100-block coinbase maturity rule, but it is indistinguishable from an immediately spendable `ReceivedOutput`. Passing it to `SignableTransaction::new` and then `multisig` reaches the signing path because `multisig` checks only that `p2tr_script_buf(keys.offset(offset).group_key())` equals the serialized prevout `script_pubkey`; it performs no maturity check.