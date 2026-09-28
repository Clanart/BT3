### Title
`Scanner::scan_block`/`scan_transaction` unconditionally credit coinbase outputs that are not spendable for 100 blocks - (File: networks/bitcoin/src/wallet/mod.rs)

### Summary
The external report's class is an unconditional terminal step applied on a path where it is not valid (`_shadow.executeCallback(guid)` executed even on the NFT's native chain, where the decoded `shadowAddress` is not a shadow contract, bricking ownership updates). The analog in Serai's in-scope code is `Scanner::scan_block` / `Scanner::scan_transaction`, which unconditionally credit every transaction output matching a registered `script_pubkey` — including the coinbase transaction, whose outputs are consensus-unspendable for 100 blocks.

### Finding Description
`Scanner::scan_transaction` iterates every output of every transaction and pushes a `ReceivedOutput` whenever `output.script_pubkey` matches a registered script, with no distinction made for coinbase transactions [1](#0-0) . `Scanner::scan_block` then iterates `block.txdata` starting at index 0, so the coinbase transaction is scanned identically to normal transactions [2](#0-1) . The only mitigation is a doc comment telling callers to post-process the results or slice off `txdata[0]` themselves [3](#0-2) .

This mirrors the bug class exactly: a uniform operation (credit output / `executeCallback`) applied unconditionally where one legitimate variant of the input (coinbase / native-chain message) requires different handling. The resulting `ReceivedOutput` is indistinguishable from a spendable one — it carries a valid `offset`, `TxOut`, and `OutPoint` — so downstream code treats it as confirmed, spendable balance.

### Impact Explanation
A `ReceivedOutput` produced from a coinbase is passed to `SignableTransaction::new`, which happily includes it as an input (`previous_output: input.outpoint`) [4](#0-3) . The threshold group then produces a valid BIP-340 signature over a transaction the Bitcoin network will reject as non-final/immature. Concretely this yields "funds reported received that are not spendable": Serai credits a deposit (minting the corresponding asset) against an output that cannot be spent for ~100 blocks, and any spend plan incorporating it produces an invalid transaction that wastes the signing session and stalls the multisig queue. That is a Medium-severity, consensus-validity/liveness failure driven by public chain data.

### Likelihood Explanation
Any Bitcoin miner is an unprivileged party who can craft the triggering input: a coinbase transaction paying to Serai's P2TR script (either the base key script or any registered offset script). Miners routinely send coinbase outputs to arbitrary addresses (payouts, donations, dusting); nothing prevents a coinbase `script_pubkey` from colliding with Serai's watched scripts. Any caller of `scan_block` — the natural API for block scanning — hits this unconditionally whenever such a block is scanned; likelihood is bounded only by how often a coinbase pays a registered script, which requires no cooperation from Serai.

### Recommendation
Make the coinbase case conditional, mirroring the `if (!isNative)` fix: in `scan_block`, skip `block.txdata[0]` when `tx.is_coinbase()`, or have `scan_transaction`/`scan_block` take/return maturity metadata so `ReceivedOutput`s from coinbases are quarantined until 100 confirmations. The check belongs inside `Scanner` rather than relying on every integrator to remember the documented post-processing pass.

### Proof of Concept
1. Register a Serai scanner: `Scanner::new(tweaked_group_key)` (`networks/bitcoin/src/wallet/mod.rs:162`).
2. An unprivileged miner includes in block *B* a coinbase transaction whose output `script_pubkey` equals `p2tr_script_buf(key)` (`mod.rs:80-86`) — e.g. a pool paying to that address, or deliberate dusting.
3. Serai calls `Scanner::scan_block(B)`; `scan_transaction` matches `output.script_pubkey` at `mod.rs:205` and returns a `ReceivedOutput` for `OutPoint { txid: coinbase_txid, vout }` — identical in shape to a spendable deposit.
4. The scheduler feeds it to `SignableTransaction::new` and `multisig(keys)` (`send.rs:273-285`), which succeeds because the offset/script check only validates key ownership, not spendability; `taproot_key_spend_signature_hash` is signed and `complete` emits a transaction (`send.rs:383-397`, `send.rs:417-425`) that Bitcoin Core rejects with `bad-txns-premature-spend-of-coinbase`. The deposit was credited; the funds are unspendable for 100 blocks; the signing round was wasted.

Caveat: the coinbase/maturity gap is acknowledged in the doc comment at `mod.rs:216-220`, so a strict reading could classify this as documented integrator responsibility; it is still the closest unconditional-step analog reachable purely from public transaction data, and the unsafe default remains the dominant code path since `scan_block` does scan `txdata[0]` unconditionally.

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

**File:** networks/bitcoin/src/wallet/mod.rs (L216-220)
```rust
  /// Scan a block.
  ///
  /// This will also scan the coinbase transaction which is bound by maturity. If received outputs
  /// must be immediately spendable, a post-processing pass is needed to remove those outputs.
  /// Alternatively, scan_transaction can be called on `block.txdata[1 ..]`.
```

**File:** networks/bitcoin/src/wallet/mod.rs (L221-227)
```rust
  pub fn scan_block(&self, block: &Block) -> Vec<ReceivedOutput> {
    let mut res = Vec::new();
    for tx in &block.txdata {
      res.extend(self.scan_transaction(tx));
    }
    res
  }
```

**File:** networks/bitcoin/src/wallet/send.rs (L177-185)
```rust
    let tx_ins = inputs
      .iter()
      .map(|input| TxIn {
        previous_output: input.outpoint,
        script_sig: ScriptBuf::new(),
        sequence: Sequence::MAX,
        witness: Witness::new(),
      })
      .collect::<Vec<_>>();
```
