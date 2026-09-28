### Title
Scanner reports outputs as received regardless of spendability; sub-dust / uneconomical deposits are permanently locked with no refund or rejection path - (File: networks/bitcoin/src/wallet/mod.rs)

### Summary
`Scanner::scan_transaction` accepts any transaction output whose `script_pubkey` matches a registered script and reports it as a `ReceivedOutput`, without checking that the output is economically spendable or even spendable at all (e.g., below-dust values, coinbase outputs burdened by maturity). The `ReceivedOutput` type carries no spendability metadata, and `SignableTransaction` provides no way to reject or refund such outputs once they are on-chain. An unprivileged sender can therefore deposit BTC to a Serai address in a form the protocol will either silently drop or can never economically spend, permanently locking the funds under the threshold group key — the direct analog of "deposit succeeds on the source side, destination-side validation fails, no refund/retry."

### Finding Description
The scanner matches outputs purely by `script_pubkey` lookup:

```rust
// networks/bitcoin/src/wallet/mod.rs:199-213
pub fn scan_transaction(&self, tx: &Transaction) -> Vec<ReceivedOutput> {
  let mut res = Vec::new();
  for (vout, output) in tx.output.iter().enumerate() {
    let Ok(vout) = u32::try_from(vout) else { break };
    if let Some(offset) = self.scripts.get(&output.script_pubkey) {
      res.push(ReceivedOutput { offset: *offset, output: output.clone(),
        outpoint: OutPoint::new(tx.compute_txid(), vout) });
    }
  }
  res
}
```

`scan_block` additionally includes the coinbase transaction, whose outputs are unspendable for 100 blocks — the code itself acknowledges this ("This will also scan the coinbase transaction which is bound by maturity") yet pushes the post-processing burden onto callers rather than encoding it in the type [1](#0-0) .

`ReceivedOutput` is documented as "A spendable output" but is a plain data carrier — `read`/`scan_transaction` perform no check that the value exceeds the relay dust limit, exceeds the fee cost to spend it, or is mature [2](#0-1) . On the spend side, `SignableTransaction::new` treats `sum(inputs) - sum(outputs)` as fee and only routes leftovers to change "if the leftover funds exceed the minimum output amount" — anything below is burned as fee with no way to signal failure back to the depositor [3](#0-2) .

Because `register_offset` is surjective (an unusable offset is incremented until its point is even), two distinct logical deposit identifiers can collapse to one script — the second registration returns `None`, yet any funds an external party sends to that address are still scanned under the first offset, with no mechanism to distinguish or refund them [4](#0-3) .

### Impact Explanation
Funds sent to a Serai-controlled address can be credited as `ReceivedOutput`s that are never usable: outputs below the spend threshold are either dropped downstream (locked forever under the threshold key, since no other party can spend a P2TR output to the group key) or consumed such that their entire value burns to miners. As in the external report, the user "deposits tokens on the source chain" — here, broadcasts a valid Bitcoin transaction paying the protocol — and the destination-side accounting fails to make those funds spendable or returnable. There is no rejection transaction, no refund path, and no retry: once the output exists on-chain the BTC is stuck regardless of what any off-chain validation decided at send time.

### Likelihood Explanation
Any unprivileged party can reach this with a public Bitcoin transaction: send a sub-dust or uneconomical amount to a branch/forward/change-derived script (all derivable from the published group key and offset scheme), or send to an address derived from an offset whose registration collided. Users depositing small amounts is an ordinary occurrence, not an adversarial edge case.

### Recommendation
- Have `scan_transaction`/`ReceivedOutput` reject or flag outputs below a defined minimum spendable value at scan time, so they are never represented as "spendable."
- Make `scan_block` skip `txdata[0]` by default (or gate coinbase outputs behind a maturity check) instead of relying on every caller to post-process.
- Provide an explicit refund/reject transaction path for outputs that cannot be aggregated, or document and enforce a minimum deposit enforced before a deposit address is handed out.
- For `register_offset` collisions, reserve the collided offsets or return the canonical script so deposits to the rejected offset aren't silently aliased.

### Proof of Concept
1. Derive a valid Serai deposit script: `p2tr_script_buf(key + GENERATOR * offset)` for any registered `offset` (or `Scalar::ZERO` for the base key).
2. Broadcast a Bitcoin transaction with an output paying that `script_pubkey` and `value` below the dust/spend threshold (e.g., 300 sats).
3. `scanner.scan_transaction(&tx)` returns a `ReceivedOutput` claiming the output is spendable, with `offset` and `outpoint` populated — indistinguishable in type from a legitimate deposit.
4. Downstream, the output is filtered out (or costs more to spend than it contains), so it is never included in a `SignableTransaction`. No transaction the threshold set can produce refunds it; the satoshis remain encumbered by the group key permanently, with no on-chain signal back to the sender.

### Citations

**File:** networks/bitcoin/src/wallet/mod.rs (L88-97)
```rust
/// A spendable output.
#[derive(Clone, PartialEq, Eq, Debug)]
pub struct ReceivedOutput {
  // The scalar offset to obtain the key usable to spend this output.
  offset: Scalar,
  // The output to spend.
  output: TxOut,
  // The TX ID and vout of the output to spend.
  outpoint: OutPoint,
}
```

**File:** networks/bitcoin/src/wallet/mod.rs (L168-196)
```rust
  /// Register an offset to scan for.
  ///
  /// Due to Bitcoin's requirement that points are even, not every offset may be used.
  /// If an offset isn't usable, it will be incremented until it is. If this offset is already
  /// present, None is returned. Else, Some(offset) will be, with the used offset.
  ///
  /// This means offsets are surjective, not bijective, and the order offsets are registered in
  /// may determine the validity of future offsets.
  ///
  /// The offsets registered must be securely generated. Arbitrary offsets may introduce a script
  /// path into the output, allowing the output to be spent by satisfaction of an arbitrary script
  /// (not by the signature of the key).
  pub fn register_offset(&mut self, mut offset: Scalar) -> Option<Scalar> {
    // This loop will terminate as soon as an even point is found, with any point having a ~50%
    // chance of being even
    // That means this should terminate within a very small amount of iterations
    loop {
      match p2tr_script_buf(self.key + (ProjectivePoint::GENERATOR * offset)) {
        Some(script) => {
          if self.scripts.contains_key(&script) {
            None?;
          }
          self.scripts.insert(script, offset);
          return Some(offset);
        }
        None => offset += Scalar::ONE,
      }
    }
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

**File:** networks/bitcoin/src/wallet/send.rs (L137-147)
```rust
  /// Returns the fee this transaction will use.
  pub fn fee(&self) -> u64 {
    self.prevouts.iter().map(|prevout| prevout.value.to_sat()).sum::<u64>() -
      self.tx.output.iter().map(|prevout| prevout.value.to_sat()).sum::<u64>()
  }

  /// Create a new SignableTransaction.
  ///
  /// If a change address is specified, any leftover funds will be sent to it if the leftover funds
  /// exceed the minimum output amount. If a change address isn't specified, all leftover funds
  /// will become part of the paid fee.
```
