[1](#0-0) ### Title
Immature coinbase outputs are reported spendable and can stall withdrawals - (File: networks/bitcoin/src/wallet/mod.rs)

### Summary
`Scanner::scan_block` treats coinbase outputs paying a watched Taproot script like ordinary received outputs, while `ReceivedOutput` retains no maturity metadata. `SignableTransaction` and `TransactionSignMachine` then accept and sign those outputs like any other UTXO. A transaction spending an immature coinbase is consensus-invalid, so a withdrawal containing it cannot be published until the coinbase matures.

### Finding Description
`Scanner::scan_block` iterates over every transaction in `block.txdata`, including index `0`, which is the coinbase transaction. [1](#0-0) 

`Scanner::scan_transaction` emits a `ReceivedOutput` whenever an output's `script_pubkey` matches a registered script; it does not distinguish coinbase outputs from ordinary transaction outputs. [2](#0-1) 

`ReceivedOutput` stores only the scalar offset, `TxOut`, and `OutPoint`, so the resulting object carries no coinbase flag, block height, or maturity state that a downstream consumer could enforce. [3](#0-2) 

`SignableTransaction::new` converts every supplied `ReceivedOutput` directly into a transaction input and only validates properties such as non-empty inputs, dust payments, sufficient value, fee rate, and transaction weight. [4](#0-3) 

The signing path then commits to all supplied prevouts and produces signatures for every input without checking whether any prevout is an immature coinbase. [5](#0-4) 

### Impact Explanation
A Bitcoin miner can create a coinbase output paying a watched scanner script. That output is publicly supplied transaction data, is returned by `scan_block` as an apparently spendable `ReceivedOutput`, and can be selected for a withdrawal transaction.

Because coinbase outputs cannot be spent before maturity, a signed transaction containing one will be rejected by Bitcoin consensus. The attempted withdrawal therefore becomes unsatisfiable until the transaction is rebuilt without the immature input or the coinbase matures. If the consumer repeatedly selects freshly reported immature coinbase outputs, a miner can repeatedly force withdrawal construction to produce transactions that cannot be relayed.

This is the same impact class as the reference finding: a state controlled outside the withdrawer can make withdrawal construction produce an unusable result while the affected funds remain temporarily unspendable.

### Likelihood Explanation
The attacker only needs to produce a valid Bitcoin block and direct its coinbase output to the target script. No validator key, leaked secret, malformed signature, trusted RPC response, or privileged Serai role is required.

The issue requires the wallet consumer to rely on `scan_block`'s `ReceivedOutput` results as spendable inputs. `scan_block` explicitly exposes that behavior, and `ReceivedOutput` gives consumers no way to determine that the returned UTXO is immature without separately retaining the source transaction and block context. [6](#0-5) 

### Recommendation
Track spendability explicitly instead of representing every matched output with the same `ReceivedOutput` type:

- Have `scan_block` skip `block.txdata[0]`, or mark coinbase outputs as immature.
- Store the receiving block height and transaction type in `ReceivedOutput`.
- Add a maturity-aware constructor, such as `scan_block_at_height`, and expose `is_spendable_at(height)`.
- Have `SignableTransaction::new` reject inputs whose recorded maturity is greater than the intended spend height.
- If mature-only scanning is desired, implement `scan_block` as `block.txdata[1..]` and provide a separate `scan_coinbase` API for wallet accounting.

### Proof of Concept
Conceptual reproduction:

1. Create a scanner for an even Taproot key and derive its watched script. [7](#0-6) 
2. Mine a regtest block whose coinbase sends funds to that script.
3. Immediately call `Scanner::scan_block` on the mined block.
4. The coinbase output is returned as a `ReceivedOutput`. [1](#0-0) 
5. Pass it to `SignableTransaction::new`; construction succeeds because no maturity field exists and no coinbase check is performed. [8](#0-7) 
6. Complete the threshold-signing flow; `TransactionSignMachine::sign` signs the transaction using `Prevouts::All`. [5](#0-4) 
7. Broadcast fails because the transaction spends an immature coinbase output.

Minimal flow:

```rust
let outputs = scanner.scan_block(&block);
assert_eq!(outputs.len(), 1);
assert!(block.txdata[0].is_coinbase());

let spend = SignableTransaction::new(
  outputs,
  &[(withdrawal_script, payment_amount)],
  Some(change_script),
  None,
  fee_per_vbyte,
);

// Accepted and signable locally, but consensus-invalid until coinbase maturity.
assert!(spend.is_ok());
```

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

**File:** networks/bitcoin/src/wallet/mod.rs (L162-165)
```rust
  pub fn new(key: ProjectivePoint) -> Option<Scanner> {
    let mut scripts = HashMap::new();
    scripts.insert(p2tr_script_buf(key)?, Scalar::ZERO);
    Some(Scanner { key, scripts })
```

**File:** networks/bitcoin/src/wallet/mod.rs (L198-210)
```rust
  /// Scan a transaction.
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
```

**File:** networks/bitcoin/src/wallet/mod.rs (L216-224)
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
```

**File:** networks/bitcoin/src/wallet/send.rs (L150-185)
```rust
  pub fn new(
    mut inputs: Vec<ReceivedOutput>,
    payments: &[(ScriptBuf, u64)],
    change: Option<ScriptBuf>,
    data: Option<Vec<u8>>,
    fee_per_vbyte: u64,
  ) -> Result<SignableTransaction, TransactionError> {
    if inputs.is_empty() {
      Err(TransactionError::NoInputs)?;
    }

    if payments.is_empty() && change.is_none() && data.is_none() {
      Err(TransactionError::NoOutputs)?;
    }

    for (_, amount) in payments {
      if *amount < DUST {
        Err(TransactionError::DustPayment)?;
      }
    }

    if data.as_ref().map_or(0, Vec::len) > 80 {
      Err(TransactionError::TooMuchData)?;
    }

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
      .collect::<Vec<_>>();
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
