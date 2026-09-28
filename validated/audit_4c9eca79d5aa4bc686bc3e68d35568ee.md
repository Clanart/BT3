### Title
`ReceivedOutput::read` accepts inconsistent spend metadata, allowing unspendable deposits to be reported - ([File: networks/bitcoin/src/wallet/mod.rs](networks/bitcoin/src/wallet/mod.rs))

### Summary

`ReceivedOutput::read` deserializes `offset`, `output`, and `outpoint` as three independent fields and returns the object without checking that the claimed `TxOut` is the output identified by `outpoint`, or that `offset` actually maps the configured base key to `output.script_pubkey` [1](#0-0) . Scanner-created objects establish that relationship: `scan_transaction` only constructs a `ReceivedOutput` after matching `output.script_pubkey` to a registered script and stores the transaction ID/vout for that exact output [2](#0-1) . Deserialization bypasses those checks, so attacker-controlled bytes can create a structurally valid received-output object whose value and ownership metadata do not correspond to any spendable UTXO.

### Finding Description

The trusted producer path binds all three pieces of metadata together. `Scanner::scan_transaction` looks up `output.script_pubkey`, uses the registered offset for that script, clones the exact transaction output, and derives `outpoint` from `tx.compute_txid()` and the output index [2](#0-1) .

`ReceivedOutput::read` instead accepts:

- any canonical scalar as `offset`;
- any consensus-decodable `TxOut`;
- any consensus-decodable `OutPoint`.

It performs no script registration check, no base-key/offset relationship check, and no lookup proving that `outpoint` resolves to `output` [3](#0-2) .

The inconsistency then propagates into transaction construction. `SignableTransaction::new` trusts the serialized `output.value` when calculating available input value, trusts `input.offset` for signing metadata, and trusts `input.outpoint` for the transaction input [4](#0-3) . The claimed `TxOut` is also placed into `prevouts`, which are committed by the Taproot signature hash via `Prevouts::All` [5](#0-4) [6](#0-5) .

### Impact Explanation

An unprivileged party who can supply bytes to `ReceivedOutput::read` can report funds as received which are not spendable by Serai.

For example, the attacker can combine:

- an outpoint belonging to an unrelated or nonexistent UTXO;
- a `TxOut` containing a Serai P2TR script and an arbitrary inflated amount;
- an offset which makes `keys.offset(offset).group_key()` match the claimed script.

`ReceivedOutput::read` accepts the object, `SignableTransaction::new` counts the claimed value as input value, and `SignableTransaction::multisig` accepts the input because the claimed script matches the offset-adjusted group key [7](#0-6) . The resulting transaction cannot be confirmed because the real `outpoint` either does not contain that `TxOut` or does not exist. This is a deposit-accounting failure: the local object reports spendable value that consensus will never let Serai spend.

If the claimed script does not match the offset-adjusted key, `multisig` returns `None`; if it does match, the transaction can be signed but remains consensus-invalid because the serialized `prevouts` metadata is not bound to the real UTXO set [8](#0-7) .

### Likelihood Explanation

The forged encoding is easy to construct because every component is public metadata and independently serializable. No private key, proof forgery, malformed elliptic-curve encoding, or protocol participant is required. The attacker only needs an input path that treats serialized `ReceivedOutput` bytes as authoritative rather than requiring them to come from `Scanner::scan_transaction`.

The attack does not let the attacker steal the Serai key or create a valid Bitcoin transaction. The practical impact is false deposit/balance recognition and downstream acceptance of accounting or signing work for an output that cannot be spent. That matches the medium-severity shape of the source advisory: a formally accepted object is created with invalid security-relevant metadata.

### Recommendation

Do not deserialize `offset`, `TxOut`, and `OutPoint` as independent trusted facts without re-establishing their binding.

At minimum, change the deserialization API so callers must provide the expected `Scanner` or base key and verify:

1. `output.script_pubkey` is registered in `Scanner::scripts`;
2. the serialized `offset` equals `scripts[output.script_pubkey]`;
3. `p2tr_script_buf(base_key + G * offset) == output.script_pubkey`;
4. the referenced `outpoint` resolves on-chain and its actual `TxOut` exactly equals the serialized `output`.

Optionally, authenticate scanner-produced serialized outputs or store only the canonical chain-derived representation and reconstruct `ReceivedOutput` exclusively through `Scanner::scan_transaction`.

### Proof of Concept

Conceptual Rust PoC against the in-scope wallet API:

```rust
// networks/bitcoin/src/wallet/mod.rs

let base_key = keys.values().next().unwrap().group_key();
let offset = Scalar::ZERO;

// A script owned by the Serai wallet.
let victim_script = p2tr_script_buf(base_key + (ProjectivePoint::GENERATOR * offset))
    .unwrap();

// An outpoint controlled by the attacker, nonexistent, or pointing to an unrelated output.
let fake_outpoint = OutPoint::new(attacker_txid, 0);

// Metadata claims the unrelated outpoint is a high-value Serai output.
let forged_txout = TxOut {
    value: Amount::from_sat(1_000_000_000),
    script_pubkey: victim_script,
};

let mut bytes = Vec::new();
bytes.extend(offset.to_bytes());
bytes.extend(bitcoin::consensus::serialize(&forged_txout));
bytes.extend(bitcoin::consensus::serialize(&fake_outpoint));

// This succeeds despite the three fields being semantically unrelated.
let forged = ReceivedOutput::read(&mut bytes.as_slice()).unwrap();

// The claimed amount is now treated as spendable input value.
let tx = SignableTransaction::new(
    vec![forged],
    &[],
    Some(p2tr_script_buf(base_key).unwrap()),
    None,
    1,
).unwrap();

// The script check succeeds because offset + script are internally selected by
// the attacker to match the target key.
let machine = tx.clone().multisig(&keys[&Participant::new(1).unwrap()].clone());
assert!(machine.is_some());

// The signed transaction still cannot confirm: `fake_outpoint` does not resolve
// to `forged_txout`, so the consensus prevout/signature requirements are not met.
```

### Citations

**File:** networks/bitcoin/src/wallet/mod.rs (L120-134)
```rust
  /// Read a ReceivedOutput from a generic satisfying Read.
  #[cfg(feature = "std")]
  pub fn read<R: Read>(r: &mut R) -> io::Result<ReceivedOutput> {
    let offset = Secp256k1::read_F(r)?;
    let output;
    let outpoint;
    {
      let mut buf_r = BufReader::with_capacity(0, r);
      output =
        TxOut::consensus_decode(&mut buf_r).map_err(|_| io::Error::other("invalid TxOut"))?;
      outpoint =
        OutPoint::consensus_decode(&mut buf_r).map_err(|_| io::Error::other("invalid OutPoint"))?;
    }
    Ok(ReceivedOutput { offset, output, outpoint })
  }
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

**File:** networks/bitcoin/src/wallet/send.rs (L245-254)
```rust
    Ok(SignableTransaction {
      tx: Transaction {
        version: Version(2),
        lock_time: LockTime::ZERO,
        input: tx_ins,
        output: tx_outs,
      },
      offsets,
      prevouts: inputs.drain(..).map(|input| input.output).collect(),
      needed_fee,
```

**File:** networks/bitcoin/src/wallet/send.rs (L273-284)
```rust
  pub fn multisig(self, keys: &ThresholdKeys<Secp256k1>) -> Option<TransactionMachine> {
    let mut sigs = vec![];
    for i in 0 .. self.tx.input.len() {
      let offset = keys.clone().offset(self.offsets[i]);
      if p2tr_script_buf(offset.group_key())? != self.prevouts[i].script_pubkey {
        None?;
      }

      sigs.push(AlgorithmMachine::new(Schnorr::new(), keys.clone().offset(self.offsets[i])));
    }

    Some(TransactionMachine { tx: self, sigs })
```

**File:** networks/bitcoin/src/wallet/send.rs (L373-389)
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
```
