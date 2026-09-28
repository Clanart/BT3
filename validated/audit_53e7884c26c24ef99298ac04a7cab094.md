### Title
`Scanner::scan_block` reports immature coinbase outputs as spendable - (File: networks/bitcoin/src/wallet/mod.rs)

### Summary
An unprivileged Bitcoin miner can create a coinbase output paying a script monitored by `Scanner`. `Scanner::scan_block` treats that output as an ordinary `ReceivedOutput`, even though coinbase outputs cannot be spent until they mature. A wallet that consumes the reported output can construct and sign a transaction which the Bitcoin network rejects, causing the apparent funds to be unusable until maturity.

### Finding Description
`Scanner::scan_transaction` creates a `ReceivedOutput` whenever a transaction output’s `script_pubkey` matches a registered script, recording only the scalar offset, `TxOut`, and outpoint. [1](#0-0)  `Scanner::scan_block` invokes this logic over every transaction in `block.txdata`, including `txdata[0]`, which is the coinbase transaction. [2](#0-1)  The returned `ReceivedOutput` contains no maturity status or other field allowing a downstream consumer to distinguish a coinbase output from a normally spendable output. [3](#0-2) 

`SignableTransaction::new` then trusts each supplied `ReceivedOutput`, uses its nominal `TxOut` value as input value, and creates a transaction input directly from its outpoint. [4](#0-3)  During signing, all supplied prevouts are committed through `Prevouts::All` and the input sighash is produced without any coinbase-maturity validation. [5](#0-4) 

This is analogous to an asset-level balance becoming conditionally non-transferable: the scanner reports the nominal amount as a received spendable balance, but consensus temporarily prevents spending it.

### Impact Explanation
A miner can make Serai report funds that cannot actually be spent yet. Any transaction automatically built from the reported output will sign successfully but be rejected by Bitcoin consensus until the coinbase matures. If the wallet aggregates all detected outputs, one immature coinbase output can invalidate the entire attempted spend, temporarily delaying use of the other inputs as well.

The effect is temporary rather than permanent: the output becomes spendable after coinbase maturity. The issue therefore fits medium severity rather than a permanent loss-of-funds vulnerability.

### Likelihood Explanation
The attacker must produce a Bitcoin block and direct a coinbase output to a tracked script. That requires mining capability, but no privileged access, stolen keys, malformed signature data, or control over the Serai implementation. Once such a block is scanned, reaching the flawed path requires a consumer to pass `scan_block` results to `SignableTransaction`, which is the natural flow exposed by the wallet API.

### Recommendation
Do not return coinbase outputs from `Scanner::scan_block`; iterate over `block.txdata[1 ..]` instead. Alternatively, expose an output state such as `PendingCoinbase { mature_at }`, retain the output for accounting, and prevent `SignableTransaction::new` from accepting it until maturity. Add a regression test mining a coinbase directly to a tracked P2TR script and asserting that it cannot be consumed as an immediate `ReceivedOutput`.

### Proof of Concept
Conceptually:

```rust
// networks/bitcoin/src/wallet/mod.rs
let mut scanner = Scanner::new(group_key).unwrap();

// A miner creates a block whose coinbase pays the tracked script.
assert!(block.txdata[0].is_coinbase());
block.txdata[0].output[0].script_pubkey =
    p2tr_script_buf(group_key).unwrap();

// This reports the immature coinbase output as spendable.
let outputs = scanner.scan_block(&block);
assert_eq!(outputs.len(), 1);
assert_eq!(outputs[0].outpoint().txid, block.txdata[0].compute_txid());

// The wallet accepts it and signs a spend.
let signable = SignableTransaction::new(
    outputs,
    &[(payment_script, payment_amount)],
    Some(change_script),
    None,
    fee_per_vbyte,
).unwrap();

let signed_tx = sign(&threshold_keys, &signable);

// Consensus rejects signed_tx because it spends an immature coinbase.
```

The key behavior is that `scan_block` includes `block.txdata[0]`, while the resulting `ReceivedOutput` cannot represent that the referenced output is subject to coinbase maturity. [6](#0-5)

### Citations

**File:** networks/bitcoin/src/wallet/mod.rs (L88-96)
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
```

**File:** networks/bitcoin/src/wallet/mod.rs (L205-210)
```rust
      if let Some(offset) = self.scripts.get(&output.script_pubkey) {
        res.push(ReceivedOutput {
          offset: *offset,
          output: output.clone(),
          outpoint: OutPoint::new(tx.compute_txid(), vout),
        });
```

**File:** networks/bitcoin/src/wallet/mod.rs (L216-225)
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
```

**File:** networks/bitcoin/src/wallet/send.rs (L175-184)
```rust
    let input_sat = inputs.iter().map(|input| input.output.value.to_sat()).sum::<u64>();
    let offsets = inputs.iter().map(|input| input.offset).collect();
    let tx_ins = inputs
      .iter()
      .map(|input| TxIn {
        previous_output: input.outpoint,
        script_sig: ScriptBuf::new(),
        sequence: Sequence::MAX,
        witness: Witness::new(),
      })
```

**File:** networks/bitcoin/src/wallet/send.rs (L373-387)
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
```
