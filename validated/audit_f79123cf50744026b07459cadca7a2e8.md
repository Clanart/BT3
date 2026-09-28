### Title
`Scanner::scan_block` credits immature coinbase outputs as spendable `ReceivedOutput`s - (File: `networks/bitcoin/src/wallet/mod.rs`)

### Summary

The analog to CVE-2026-2285 (a loader returning a resource identified by unvalidated input without checking whether access is permitted) is Serai's Bitcoin scanner: `Scanner::scan_block` walks every transaction in a block — including `block.txdata[0]`, the coinbase — and emits `ReceivedOutput` values indistinguishable from ordinary, immediately-spendable UTXOs. Coinbase outputs are consensus-unspendable for 100 blocks, so the scanner reports "funds received" that are not, in fact, spendable. [1](#0-0) 

### Finding Description

`Scanner::scan_block` iterates `&block.txdata` from index 0, calling `scan_transaction` on each: [1](#0-0) 

```rust
pub fn scan_block(&self, block: &Block) -> Vec<ReceivedOutput> {
  let mut res = Vec::new();
  for tx in &block.txdata {
    res.extend(self.scan_transaction(tx));
  }
  res
}
```

`scan_transaction` matches `output.script_pubkey` against the registered scripts and produces a `ReceivedOutput { offset, output, outpoint }` with no marker of coinbase-ness or maturity. [2](#0-1) 

The `ReceivedOutput` type carries only `offset`, `output`, and `outpoint` — there is no maturity/confirmation field, so downstream consumers cannot distinguish an immature coinbase output from a normal one. [3](#0-2) 

Any `ReceivedOutput` can be fed directly into `SignableTransaction::new`, which uses its `outpoint` and claimed `value` to build inputs and prevouts, with no maturity check. [4](#0-3)  The resulting transaction is then threshold-signed by `TransactionSignMachine` via `taproot_key_spend_signature_hash` and broadcast. [5](#0-4) 

The only mitigation is a doc comment on `scan_block` stating a "post-processing pass is needed" — the API itself returns the immature output as a fully-formed `ReceivedOutput` with nothing marking it unspendable. [6](#0-5) 

### Impact Explanation

An unprivileged third party (any miner, or any party able to influence a coinbase's outputs) can cause a fresh coinbase to pay to the wallet's P2TR script. `scan_block` then reports a `ReceivedOutput` that:

1. Cannot be spent for 100 blocks — any `SignableTransaction` built from it and threshold-signed will be rejected by consensus, wasting a coordinated signing attempt and potentially confusing nonce/session state.
2. Is indistinguishable from spendable outputs — a processor crediting "received" balances from `scan_block` will report funds received that are not spendable, one of the explicitly accepted impact classes.

The parallel to the source CVE is direct: unvalidated input (the block's untrusted tx set) is dereferenced into a resource (a spendable-output record) without checking the state precondition (coinbase maturity) that governs whether that resource may actually be used.

Severity: Medium — real correctness/accounting flaw reachable by public on-chain data, but exploitability is limited by the documented warning and the fact that the funds are genuinely owned after maturity.

### Likelihood Explanation

High occurrence probability: any miner can include a payment to a Serai-scanned script in a coinbase at zero extra cost. Whether it causes damage depends on whether integrators heed the docstring caveat; nothing in the type system or API enforces the maturity check, so misuse is easy.

### Recommendation

Have `scan_block` skip `block.txdata[0]`, or tag `ReceivedOutput` with a maturity/confirmation requirement (e.g., a `min_confirmations` or `coinbase` flag) and have `SignableTransaction::new` reject immature coinbase inputs. Alternatively, make `scan_transaction` take the block position so coinbase provenance is preserved rather than erased.

### Proof of Concept

1. Wallet calls `Scanner::new(key)` (or registers offsets) and monitors blocks via `scan_block`.
2. A miner mines a block whose coinbase (`txdata[0]`) contains a `TxOut` with `script_pubkey` equal to `p2tr_script_buf(key)` — e.g., a mining pool paying the Serai address directly from the coinbase.
3. `scan_block` returns `ReceivedOutput { offset: ZERO, output: coinbase_txout, outpoint: OutPoint::new(coinbase_txid, vout) }`.
4. Integrator passes it to `SignableTransaction::new(vec![received], payments, ...)`; no check fails. The transaction is signed by `TransactionSignMachine::sign` producing a valid BIP-340 signature.
5. Broadcast fails consensus validation: `bad-txns-coinbase-spend` (spending a coinbase before 100 confirmations). The reported "received" funds were not spendable, and a signing round was consumed on a permanently invalid transaction (the signed sighash also commits to the invalid input set).

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

**File:** networks/bitcoin/src/wallet/mod.rs (L150-185)
```rust

/// A transaction scanner capable of being used with HDKD schemes.
#[derive(Clone, Debug)]
pub struct Scanner {
  key: ProjectivePoint,
  scripts: HashMap<ScriptBuf, Scalar>,
}

impl Scanner {
  /// Construct a Scanner for a key.
  ///
  /// Returns None if this key can't be scanned for.
  pub fn new(key: ProjectivePoint) -> Option<Scanner> {
    let mut scripts = HashMap::new();
    scripts.insert(p2tr_script_buf(key)?, Scalar::ZERO);
    Some(Scanner { key, scripts })
  }

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
```

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

**File:** networks/bitcoin/src/wallet/send.rs (L373-397)
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
        )?;
        shares.push(share);
        Ok(sig)
      })
      .collect::<Result<_, _>>()?;

    Ok((TransactionSignatureMachine { tx: self.tx.tx, sigs }, shares))
```
