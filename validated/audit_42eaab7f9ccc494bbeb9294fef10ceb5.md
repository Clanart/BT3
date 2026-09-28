### Title
Deserialized Bitcoin outputs can bind a trusted outpoint to an unrelated claimed script and value - (File: `processor/src/networks/bitcoin.rs`)

### Summary
`ReceivedOutput::read` accepts three independently controlled fields: the scalar offset, the claimed `TxOut`, and the `OutPoint`. It does not verify that the claimed output belongs to the referenced outpoint, nor that its script corresponds to the provided offset. `Output::read` further accepts an independently controlled `OutputType`. As a result, attacker-controlled serialized output bytes can make a real-looking outpoint identify an output whose reported owner and balance come from a completely different `TxOut`. [1](#0-0) [2](#0-1) 

### Finding Description
The identifier returned by `Output::id()` is derived solely from `ReceivedOutput.outpoint`, while the reported multisig owner returned by `Output::key()` is derived from `ReceivedOutput.output.script_pubkey` minus `offset * G`, and the reported balance is derived from `ReceivedOutput.output.value`. These three security-relevant views can therefore describe different objects after deserialization. [3](#0-2) 

This is the same authorization-shape as the external IDOR: the outpoint acts like the validated object identifier, while the offset and claimed `TxOut` are the separately supplied target fields that determine ownership and value. Nothing in `ReceivedOutput::read` binds those fields to the outpoint. [4](#0-3) 

The inconsistency propagates into spending. `SignableTransaction::new` uses `outpoint` to construct the transaction inputs, but uses the independently supplied `output` as the claimed prevout and `offset` to select the tweaked signing key. [5](#0-4)  `SignableTransaction::multisig` checks only that the claimed `TxOut` script matches the tweaked group key; it does not prove that this claimed `TxOut` is actually the output named by `outpoint`. [6](#0-5) 

### Impact Explanation
An unprivileged party who can supply serialized `ReceivedOutput` or Bitcoin `Output` bytes can cause an unrelated or victim-controlled outpoint to be reported as a Serai-owned output with an attacker-selected amount, type, and base key. Downstream accounting and planning can therefore treat funds as received and assign them to a multisig even though the referenced outpoint does not contain the claimed `TxOut`. [7](#0-6) 

The resulting transaction is not a valid spend of the referenced outpoint because its BIP-341 sighash commits to the fabricated prevout supplied in `SignableTransaction.prevouts`, while the transaction input references the unrelated outpoint. This produces outputs that may be reported or routed as received but are not spendable by the multisig, and can corrupt balance accounting, refund/forward routing, or output tracking. [8](#0-7) 

The independently accepted `OutputType` can additionally misclassify an external payment as branch, change, or forwarded output even though the scanner normally derives that classification from the registered offset. [9](#0-8) 

### Likelihood Explanation
The attack requires a path where an untrusted party supplies serialized `Output`/`ReceivedOutput` bytes rather than the processor exclusively consuming objects constructed by `Scanner::scan_transaction`. Scanner-derived outputs are internally consistent because the scanner derives the offset from the observed script and binds the observed transaction/vout into the outpoint. [10](#0-9) 

Where serialized outputs are accepted from peers, recovery data, persisted messages, or another untrusted source, exploitation requires only valid encodings. No private key material, invalid curve points, or malformed consensus encodings are required.

### Recommendation
Do not expose or trust `ReceivedOutput::read`/`Output::read` as self-authenticating representations of a spendable output. At minimum:

- Require the expected base key and derive classification by reconstructing the scanner, then verify `p2tr_script_buf(base_key + G * offset) == output.script_pubkey` and that `OutputType` matches the registered offset.
- Resolve `outpoint` against confirmed chain data and verify that it indexes exactly the serialized `TxOut` before exposing the object as a received balance.
- Store or transmit a network-verified outpoint-to-output commitment rather than allowing the outpoint and claimed output to vary independently.
- Return an error for inconsistent serialized records instead of constructing a struct whose `id`, `key`, `balance`, and spending fields disagree.

### Proof of Concept
For any public base key `K`, choose an offset `o` such that `K + oG` has even Y and construct the corresponding Taproot script `S`. Choose an unrelated confirmed outpoint `V`, for example an output belonging to another party, and an arbitrary inflated value.

```text
ReceivedOutput bytes =
    encode_scalar(o)
  || consensus_encode(TxOut {
       value: Amount::from_sat(inflated_amount),
       script_pubkey: S,
     })
  || consensus_encode(V)
```

`ReceivedOutput::read` accepts this tuple without checking that `V` resolves to that `TxOut`. Wrapped as an `Output`, `id()` identifies `V`, `key()` returns `K`, and `balance()` reports `inflated_amount`. `SignableTransaction::new` then uses `V` as the input while committing the fake `TxOut` as its prevout, producing a purported Serai-owned payment that cannot be validly spent from `V`.

### Citations

**File:** networks/bitcoin/src/wallet/mod.rs (L90-97)
```rust
pub struct ReceivedOutput {
  // The scalar offset to obtain the key usable to spend this output.
  offset: Scalar,
  // The output to spend.
  output: TxOut,
  // The TX ID and vout of the output to spend.
  outpoint: OutPoint,
}
```

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

**File:** networks/bitcoin/src/wallet/mod.rs (L198-213)
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
    }
    res
```

**File:** processor/src/networks/bitcoin.rs (L92-129)
```rust
  fn id(&self) -> Self::Id {
    let mut res = OutputId::default();
    self.output.outpoint().consensus_encode(&mut res.as_mut()).unwrap();
    debug_assert_eq!(
      {
        let mut outpoint = vec![];
        self.output.outpoint().consensus_encode(&mut outpoint).unwrap();
        outpoint
      },
      res.as_ref().to_vec()
    );
    res
  }

  fn tx_id(&self) -> [u8; 32] {
    let mut hash = *self.output.outpoint().txid.as_raw_hash().as_byte_array();
    hash.reverse();
    hash
  }

  fn key(&self) -> ProjectivePoint {
    let script = &self.output.output().script_pubkey;
    assert!(script.is_p2tr());
    let Instruction::PushBytes(key) = script.instructions_minimal().last().unwrap().unwrap() else {
      panic!("last item in v1 Taproot script wasn't bytes")
    };
    let key = XOnlyPublicKey::from_slice(key.as_ref())
      .expect("last item in v1 Taproot script wasn't x-only public key");
    Secp256k1::read_G(&mut key.public_key(Parity::Even).serialize().as_slice()).unwrap() -
      (ProjectivePoint::GENERATOR * self.output.offset())
  }

  fn presumed_origin(&self) -> Option<Address> {
    self.presumed_origin.clone()
  }

  fn balance(&self) -> ExternalBalance {
    ExternalBalance { coin: ExternalCoin::Bitcoin, amount: Amount(self.output.value()) }
```

**File:** processor/src/networks/bitcoin.rs (L145-165)
```rust
  fn read<R: io::Read>(mut reader: &mut R) -> io::Result<Self> {
    Ok(Output {
      kind: OutputType::read(reader)?,
      presumed_origin: {
        let mut io_reader = scale::IoReader(reader);
        let res = Option::<Vec<u8>>::decode(&mut io_reader)
          .unwrap()
          .map(|address| Address::try_from(address).unwrap());
        reader = io_reader.0;
        res
      },
      output: ReceivedOutput::read(reader)?,
      data: {
        let mut data_len = [0; 2];
        reader.read_exact(&mut data_len)?;

        let mut data = vec![0; usize::from(u16::from_le_bytes(data_len))];
        reader.read_exact(&mut data)?;
        data
      },
    })
```

**File:** processor/src/networks/bitcoin.rs (L313-346)
```rust
// Always construct the full scanner in order to ensure there's no collisions
fn scanner(
  key: ProjectivePoint,
) -> (Scanner, HashMap<OutputType, Scalar>, HashMap<Vec<u8>, OutputType>) {
  let mut scanner = Scanner::new(key).unwrap();
  let mut offsets = HashMap::from([(OutputType::External, Scalar::ZERO)]);

  let zero = Scalar::ZERO.to_repr();
  let zero_ref: &[u8] = zero.as_ref();
  let mut kinds = HashMap::from([(zero_ref.to_vec(), OutputType::External)]);

  let mut register = |kind, offset| {
    let offset = scanner.register_offset(offset).expect("offset collision");
    offsets.insert(kind, offset);

    let offset = offset.to_repr();
    let offset_ref: &[u8] = offset.as_ref();
    kinds.insert(offset_ref.to_vec(), kind);
  };

  register(
    OutputType::Branch,
    *BRANCH_OFFSET.get_or_init(|| Secp256k1::hash_to_F(KEY_DST, b"branch")),
  );
  register(
    OutputType::Change,
    *CHANGE_OFFSET.get_or_init(|| Secp256k1::hash_to_F(KEY_DST, b"change")),
  );
  register(
    OutputType::Forwarded,
    *FORWARD_OFFSET.get_or_init(|| Secp256k1::hash_to_F(KEY_DST, b"forward")),
  );

  (scanner, offsets, kinds)
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
