### Title
`ReceivedOutput::read` accepts unbound `(offset, output, outpoint)` triples, allowing unspendable inputs to be reported and signed for - ([File: networks/bitcoin/src/wallet/mod.rs])

### Summary
`ReceivedOutput` claims to represent “a spendable output,” but its deserializer only decodes three independent fields and never checks that the embedded `TxOut` is the output named by `outpoint` or that the `offset` is associated with a scanner-registered key. [1](#0-0)  An attacker who can supply serialized `ReceivedOutput` bytes can therefore combine a wallet-controlled script and attacker-chosen value with a nonexistent or unrelated outpoint, causing the wallet to report funds and construct a transaction which cannot spend them. [2](#0-1) 

### Finding Description
The vulnerable object consists of `offset`, `output`, and `outpoint`, all attacker-controlled when `ReceivedOutput::read` is used on untrusted bytes. [1](#0-0)  `read` parses the scalar, consensus-decodes a `TxOut`, and consensus-decodes an `OutPoint`, then returns the composite object without any consistency check. [2](#0-1) 

The parser does not ensure that `outpoint` identifies a transaction containing `output`, nor that `output.script_pubkey` is the Taproot script for a previously registered `key + offset * G`. [3](#0-2)  Legitimate outputs constructed by `Scanner::scan_transaction` do bind these fields correctly by copying the actual transaction output and deriving `outpoint` from `tx.compute_txid()` and its index, but deserialized objects bypass that provenance. [4](#0-3) 

Downstream, `SignableTransaction::new` trusts the decoded value for input accounting and trusts the decoded outpoint as the transaction input. [5](#0-4)  The later signing check only verifies that the embedded `TxOut` script equals `p2tr_script_buf(keys.clone().offset(offset).group_key())`; it does not validate that the referenced UTXO exists or contains that script and value. [6](#0-5) 

### Impact Explanation
A forged object can report an arbitrary balance through `ReceivedOutput::value`, because the value comes entirely from the embedded `TxOut`. [7](#0-6)  The wallet can then produce a `SignableTransaction` whose input references a fake or unrelated `OutPoint` while treating the fake `TxOut` as its prevout for sighash purposes. [5](#0-4) [8](#0-7) 

Because the embedded script can be a legitimate wallet script, `SignableTransaction::multisig` accepts the forged input and creates per-input signing machines. [9](#0-8)  The resulting signed transaction is still unspendable: Bitcoin consensus will resolve `previous_output` against the real UTXO set, so a fake outpoint has no spendable coin and an unrelated outpoint will not contain the claimed prevout script/value. [10](#0-9) 

### Likelihood Explanation
The attack requires an attacker-controlled byte stream to reach `ReceivedOutput::read`, which is explicitly a public deserialization API for wallet outputs. [11](#0-10)  The payload requires no secret material: the attacker needs only a wallet P2TR script, which is public from an address or observed output, plus a fabricated scalar, amount, txid, and vout. [12](#0-11) 

The vulnerability is confined to callers which treat deserialized `ReceivedOutput`s as authoritative scanner results rather than reparsing them from authenticated chain data. [3](#0-2)  Within that trust boundary, the impact is concrete: falsely recognized funds and a signed transaction that cannot obtain the reported input value. [13](#0-12) 

### Recommendation
Do not expose `ReceivedOutput::read` as an authority-establishing constructor for untrusted data; rename or document it as trusted storage serialization and require a chain-verifying constructor for network input. [14](#0-13) 

For untrusted inputs, reconstruct `ReceivedOutput` only through `Scanner::scan_transaction`/`scan_block`, or provide a `Scanner::verify_received_output`/`ReceivedOutput::verify` path which re-derives the expected script from `key + offset * G` and fetches the named outpoint to compare the full `TxOut`. [15](#0-14)  At minimum, `SignableTransaction::multisig` should not be treated as validation of UTXO existence; its check currently validates only the claimed script against the claimed offset. [6](#0-5) 

### Proof of Concept
The serialized shape is:

```text
offset          = 32-byte canonical secp256k1 scalar
txout           = value_u64_le || compact_size(script_len) || script_pubkey
outpoint        = txid_internal_order_32_bytes || vout_u32_le
received_output = offset || txout || outpoint
```

For a wallet base key with x-only coordinate `X`, use a zero offset and the P2TR script `0x51 0x20 || X`, with an arbitrary outpoint such as `00…00 || 00000000`. [16](#0-15)  The resulting bytes are:

```text
00 * 32                                      # offset = 0
a0 86 01 00 00 00 00 00                      # claimed value = 100,000 sats
22                                           # script length = 34
51 20 || X                                   # P2TR output for the wallet key
00 * 32 || 00 00 00 00                       # nonexistent outpoint
```

`ReceivedOutput::read` accepts this object, and `value()` reports 100,000 sats even though no such UTXO is established by the bytes. [17](#0-16)  `SignableTransaction::new` uses the fake value and outpoint as an input, while `multisig` accepts it because the claimed script matches the wallet key plus zero offset. [5](#0-4) [6](#0-5)  The signing machine then commits to the fabricated `prevouts` in the Taproot sighash, yielding a transaction Bitcoin cannot accept as spending the reported funds. [8](#0-7)

### Citations

**File:** networks/bitcoin/src/wallet/mod.rs (L77-86)
```rust
/// Return the Taproot address payload for a public key.
///
/// If the key is odd, this will return None.
pub fn p2tr_script_buf(key: ProjectivePoint) -> Option<ScriptBuf> {
  if key.to_encoded_point(true).tag() != Tag::CompressedEvenY {
    return None;
  }

  Some(ScriptBuf::new_p2tr_tweaked(TweakedPublicKey::dangerous_assume_tweaked(x_only(&key))))
}
```

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

**File:** networks/bitcoin/src/wallet/mod.rs (L115-134)
```rust
  /// The value of this output.
  pub fn value(&self) -> u64 {
    self.output.value.to_sat()
  }

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

**File:** networks/bitcoin/src/wallet/mod.rs (L180-214)
```rust
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
    }
    res
  }
```

**File:** networks/bitcoin/src/wallet/send.rs (L175-185)
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
      .collect::<Vec<_>>();
```

**File:** networks/bitcoin/src/wallet/send.rs (L245-255)
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
    })
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

**File:** networks/bitcoin/src/wallet/send.rs (L373-386)
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
```
