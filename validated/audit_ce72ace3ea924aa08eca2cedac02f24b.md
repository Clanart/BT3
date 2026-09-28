### Title
Coinbase outputs are reported as received/spendable despite being immature, causing constructed transactions to be invalid and funds to be unusable - (File: networks/bitcoin/src/wallet/mod.rs)

### Summary
`Scanner::scan_block` scans every transaction in a block, including the coinbase transaction (`block.txdata[0]`). Outputs of a coinbase transaction are subject to Bitcoin's 100-block maturity rule (consensus rule COINBASE_MATURITY): they cannot be spent until 100 confirmations. Serai reports these as ordinary `ReceivedOutput`s with no maturity marker, and `SignableTransaction::new` will happily build a transaction spending them — a transaction that is consensus-invalid and will be rejected by every node, permanently (until maturity) burning the signature attempt and leaving the funds unspendable through this path.

### Finding Description
Analogous to the Teller finding where withdrawable collateral becomes permanently frozen with no way to redirect it, `scan_block` hands the integrator an output that is reported as received but is not spendable. In `networks/bitcoin/src/wallet/mod.rs:221-227`, `scan_block` iterates over all `block.txdata` including the coinbase, and `scan_transaction` (mod.rs:199-214) pushes a `ReceivedOutput` for any matching `script_pubkey` with no distinction for coinbase inputs. The code comment acknowledges the issue but pushes responsibility to an undocumented post-processing pass:

```rust
// This will also scan the coinbase transaction which is bound by maturity. If received
// outputs must be immediately spendable, a post-processing pass is needed to remove
// those outputs.
```

`ReceivedOutput` (mod.rs:89-97) carries only `offset`, `output`, and `outpoint` — there is no flag indicating coinbase origin or required confirmation depth, so downstream code cannot distinguish immature outputs. `SignableTransaction::new` (send.rs:150-256) then consumes `Vec<ReceivedOutput>` directly, and `TransactionSignMachine::sign` (send.rs:373-398) produces a transaction committing to `Prevouts::All` including the immature prevout. The resulting transaction fails consensus validation (`bad-txns-premature-spend-of-coinbase`) — the funds are frozen and cannot be redirected or re-signed into a valid form without re-scanning at maturity.

### Impact Explanation
A threshold vault that relies on `scan_block` (the natural API for a Bitcoin full-node integrator) will produce invalid transactions whenever an immature coinbase output is included, and more critically, the protocol treats those funds as part of its spendable balance. In a Serai-style bridge where the validator set's BTC address receives mining-pool payouts or where a counterparty deliberately funds the vault's key via a coinbase transaction, the wallet reports balance that cannot be moved for 100 blocks, and any spend attempt including it fails outright — funds reported received that are not spendable.

### Likelihood Explanation
Medium. An external party cannot create coinbase outputs arbitrarily, but mining pools routinely pay directly from coinbase transactions; anyone mining or routing pool payouts to the vault's P2TR address triggers this. The reachability requirement is satisfied since the trigger is a Bitcoin transaction an outside party sends.

### Recommendation
Tag coinbase-derived `ReceivedOutput`s. Record the block height (or a maturity flag) when scanning `block.txdata[0]` in `scan_block`, expose it on `ReceivedOutput`, and have `SignableTransaction::new` reject immature inputs rather than building a consensus-invalid transaction.

### Proof of Concept
1. A miner (or a pool paying from coinbase) sends BTC to the vault's tweaked P2TR script.
2. The vault node calls `Scanner::scan_block` on the containing block; `scan_transaction` matches the `script_pubkey` and returns a `ReceivedOutput` indistinguishable from a normal UTXO (mod.rs:199-227).
3. The coordinator builds `SignableTransaction::new` using that output; `multisig`/`sign` produce a fully signed transaction committing to the coinbase prevout via `Prevouts::All` (send.rs:375).
4. Broadcast fails with `bad-txns-premature-spend-of-coinbase`; the output cannot be spent or excluded by the wallet layer since nothing marks it immature — frozen until maturity with no way to redirect it, mirroring the blacklisted-collateral freeze in the reference report. [1](#0-0) [2](#0-1) [3](#0-2)

### Citations

**File:** networks/bitcoin/src/wallet/mod.rs (L199-214)
```rust
  pub fn scan_transaction(&self, tx: &Transaction) -> Vec<ReceivedOutput> {
    let mut res = Vec::new();
    for (vout, output) in tx.output.iter().enumerate() {
      // If the vout index exceeds 2**32, stop scanning outputs
      let Ok(vout) = u32::try_from(vout) else { break };

      if let Some(offset) = self.scripts.get(&output.script_pubkey) {
        res.push(ReceivedOutput {
          offset: *offset,
          output: output.clone(),
          outpoint: OutPoint::new(tx.compute_txid(), vout),
        });
      }
    }
    res
  }
```

**File:** networks/bitcoin/src/wallet/mod.rs (L216-227)
```rust
  /// Scan a block.
  ///
  /// This will also scan the coinbase transaction which is bound by maturity. If received outputs
  /// must be immediately spendable, a post-processing pass is needed to remove those outputs.
  /// Alternatively, scan_transaction can be called on `block.txdata[1 ..]`.
  pub fn scan_block(&self, block: &Block) -> Vec<ReceivedOutput> {
    let mut res = Vec::new();
    for tx in &block.txdata {
      res.extend(self.scan_transaction(tx));
    }
    res
  }
```

**File:** networks/bitcoin/src/wallet/send.rs (L373-390)
```rust
    let mut cache = SighashCache::new(&self.tx.tx);
    // Sign committing to all inputs
    let prevouts = Prevouts::All(&self.tx.prevouts);

    let mut shares = Vec::with_capacity(self.sigs.len());
    let sigs = self
      .sigs
      .drain(..)
      .enumerate()
      .map(|(i, sig)| {
        let (sig, share) = sig.sign(
          commitments[i].clone(),
          cache
            .taproot_key_spend_signature_hash(i, &prevouts, TapSighashType::Default)
            // This should never happen since the inputs align with the TX the cache was
            // constructed with, and because i is always < prevouts.len()
            .expect("taproot_key_spend_signature_hash failed to return a hash")
            .as_ref(),
```
