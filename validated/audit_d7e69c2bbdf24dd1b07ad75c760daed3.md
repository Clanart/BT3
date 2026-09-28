### Title
Deserialized `ReceivedOutput` bypasses scanner binding and can fabricate unspendable funds - (File: networks/bitcoin/src/wallet/mod.rs)

### Summary
`Scanner::scan_transaction` only creates a `ReceivedOutput` after matching the actual transaction output script and derives its `OutPoint` from the containing transaction. [1](#0-0)  `ReceivedOutput::read` accepts an offset, `TxOut`, and `OutPoint` independently, without proving that the outpoint exists, that the `TxOut` is the output at that outpoint, or that the output belongs to the key associated with the offset. [2](#0-1) 

### Finding Description
The scanner path enforces the wallet-ownership invariant by looking up `output.script_pubkey` in the registered script map and binding the returned offset to that actual output and transaction position. [1](#0-0)  The deserialization path reconstructs the same “spendable output” type from attacker-controlled bytes, but only checks that the scalar, `TxOut`, and `OutPoint` are syntactically decodable. [2](#0-1) 

Downstream transaction construction trusts the claimed `TxOut` as both the input value and the previous output committed by the Taproot sighash. [3](#0-2)  `SignableTransaction::multisig` only verifies that the claimed script can be reached from the supplied threshold keys and offset; it does not verify that the claimed `OutPoint` references that output on chain. [4](#0-3) 

### Impact Explanation
An unprivileged party who can feed bytes to `ReceivedOutput::read` can cause a nonexistent or incorrectly valued UTXO to be represented as a spendable received output. If the forged `TxOut` uses one of the wallet’s registered P2TR scripts, the resulting `SignableTransaction` can pass the wallet’s ownership check and proceed to signing even though the referenced outpoint is fake or does not contain the claimed output. This can report unavailable Bitcoin as spendable balance and produce an invalid transaction which cannot spend the claimed funds.

### Likelihood Explanation
The input requirements are public and easy to construct: a canonical scalar, consensus-encoded `TxOut`, and consensus-encoded `OutPoint`. No private key material, malicious validator behavior, or protocol collusion is required. Exploitation depends on an integration accepting serialized `ReceivedOutput` data from an untrusted source rather than treating it as trusted local scanner state.

### Recommendation
Do not allow `ReceivedOutput::read` to establish scanner provenance from unattributed bytes. Prefer serializing only the discovered `OutPoint` and deriving the authoritative `TxOut` and offset by rescanning the referenced transaction through `Scanner`. Alternatively, require `ReceivedOutput::read` to authenticate scanner-produced state and revalidate the output against chain data before exposing it as spendable. At minimum, add an API which verifies `output.script_pubkey == p2tr_script_buf(expected_key + G * offset)` and fetches the output addressed by `outpoint` to confirm equality with the encoded `TxOut`.

### Proof of Concept
```rust
// Pseudocode demonstrating the reachable deserialization bypass.

let offset = Scalar::ZERO;
let forged_output = TxOut {
  value: Amount::from_sat(1_000_000_000),
  // A script actually registered by the victim's Scanner.
  script_pubkey: victim_registered_p2tr_script,
};
let fake_outpoint = OutPoint {
  txid: arbitrary_or_nonexistent_txid,
  vout: 0,
};

let mut encoded = Vec::new();
encoded.extend(offset.to_bytes());
forged_output.consensus_encode(&mut encoded).unwrap();
fake_outpoint.consensus_encode(&mut encoded).unwrap();

// Accepted even though fake_outpoint was never found by Scanner and may not exist.
let received = ReceivedOutput::read(&mut encoded.as_slice()).unwrap();
assert_eq!(received.value(), 1_000_000_000);

// The claimed amount is now trusted as available input value.
let signable = SignableTransaction::new(
  vec![received],
  &payments,
  change,
  None,
  fee_per_vbyte,
).unwrap();

// Because the forged script matches the wallet key at the encoded offset,
// this ownership check succeeds despite the outpoint being fabricated.
let machine = signable.multisig(&victim_threshold_keys).unwrap();
```

The resulting transaction would contain a valid-looking Taproot input and signatures, but it cannot spend `fake_outpoint` because no matching on-chain output with the claimed value was ever discovered by `Scanner`.

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

**File:** networks/bitcoin/src/wallet/mod.rs (L198-211)
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

**File:** networks/bitcoin/src/wallet/send.rs (L273-282)
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
```
