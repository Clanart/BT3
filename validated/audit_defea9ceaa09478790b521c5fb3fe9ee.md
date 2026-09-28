### Title
`ReceivedOutput::read` accepts an attacker-supplied offset/`TxOut`/`OutPoint` tuple with no binding validation, letting phantom inputs be signed as real deposits - ([File: networks/bitcoin/src/wallet/mod.rs](networks/bitcoin/src/wallet/mod.rs))

### Summary
The external report's bug class is deferred, ordering-sensitive validation: `checkSuccess` inspects ambient `returndata` and only yields a correct answer if invoked immediately after the call it is meant to validate. Serai's analog is `ReceivedOutput`. The type's invariants (the `offset` derives the `output.script_pubkey`, the `outpoint` identifies a real on-chain output paying that script) are established only by `Scanner::scan_transaction` at construction time. `ReceivedOutput::read`, an explicitly supported untrusted-bytes entry point, reconstructs the struct from raw bytes and validates nothing; the only downstream check is `SignableTransaction::multisig`, which verifies internal consistency that the attacker fully controls.

### Finding Description
`Scanner::scan_transaction` creates a `ReceivedOutput` only after looking up `output.script_pubkey` in the registered `scripts` map, binding the stored `offset` to a script the wallet key can actually spend ( [1](#0-0) ). `ReceivedOutput::read` deserializes `offset`, `output` (`TxOut`), and `outpoint` straight from the reader with no such check — it never computes `p2tr_script_buf(key + offset·G)` nor compares it to `output.script_pubkey`, and it obviously cannot confirm the `outpoint` exists ( [2](#0-1) ).

The only later validation lives in `SignableTransaction::multisig`, which checks `p2tr_script_buf(offset.group_key())? == self.prevouts[i].script_pubkey` ( [3](#0-2) ). Like `checkSuccess`, this check only measures the *last* values presented to it — and both `prevouts[i].script_pubkey` and `offsets[i]` come verbatim from the attacker-crafted `ReceivedOutput` (see `inputs.iter().map(|input| input.offset)` and `prevouts` at [4](#0-3)  and [5](#0-4) ). An attacker can make the check pass trivially: pick any `o`, compute `p2tr_script_buf(key + o·G)` themselves, and set that as the `TxOut`'s script with an arbitrary `value` and a fabricated `OutPoint`.

The forged input then flows through `TransactionSignMachine::sign`, where each input's claimed `prevout` is committed via `Prevouts::All` into the BIP-341 sighash and threshold-signed ( [6](#0-5) ), and `complete` emits a fully witnessed `Transaction` ( [7](#0-6) ).

### Impact Explanation
The signed Bitcoin transaction spends a nonexistent or mis-valued prevout, so it is invalid on-chain — yet the signing pipeline accepted it and the wallet logic reported the output as received and spendable. This is the "funds reported received that are not spendable" impact class: a data source able to feed serialized `ReceivedOutput`s (e.g., a scanner/output provider whose results are relayed to signers) can inject phantom deposits with arbitrary amounts, causing the threshold network to sign transactions that cannot confirm, inflating perceived balances and burning fees on invalid transactions.

### Likelihood Explanation
Reachability is limited to deployments where `ReceivedOutput`s arrive from an untrusted serializer rather than from a local `Scanner` over verified blocks — a plausible pattern when scan results are transmitted between components. When reachable, no threshold collusion or key compromise is needed; the attacker's bytes pass every check in the pipeline by construction. Severity is Medium: no secret material is exposed and no unintended *valid* spend occurs, but invalid signatures/commitments and false deposit accounting are produced purely from public input bytes.

### Recommendation
Either reject `ReceivedOutput`s not produced by `Scanner`, or validate at read/sign time that the `outpoint` resolves to a real confirmed output whose `script_pubkey` equals `p2tr_script_buf(group_key + offset·G)` — i.e., move the binding check next to the use rather than relying on the provenance of construction, mirroring the report's recommendation to replace ambient validation with explicit interfaces.

### Proof of Concept
```rust
// Attacker knows the wallet group key `key` (public).
let o = Scalar::ONE; // arbitrary offset
let script = p2tr_script_buf(key + ProjectivePoint::GENERATOR * o).unwrap();
let fake = ReceivedOutput::read(&mut &{
    let mut b = vec![];
    b.extend(o.to_bytes());                                   // offset
    b.extend(serialize(&TxOut { value: Amount::from_sat(1_000_000), script_pubkey: script }));
    b.extend(serialize(&OutPoint::null()));                   // fabricated outpoint
    b
}[..]).unwrap();

let stx = SignableTransaction::new(vec![fake], &payments, None, None, 1).unwrap();
// Passes: offset `o` provably maps to `script`
let machine = stx.multisig(&keys).unwrap();
// Threshold signing commits Prevouts::All to a phantom 1M-sat prevout;
// the emitted transaction is invalid, yet was signed as a real spend.
```
The `multisig` check succeeds because the attacker controls both sides of the comparison — the exact ordering/ambient-validation flaw the `checkSuccess` report describes. [8](#0-7)

### Citations

**File:** networks/bitcoin/src/wallet/mod.rs (L122-134)
```rust
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

**File:** networks/bitcoin/src/wallet/mod.rs (L180-196)
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

**File:** networks/bitcoin/src/wallet/send.rs (L252-255)
```rust
      offsets,
      prevouts: inputs.drain(..).map(|input| input.output).collect(),
      needed_fee,
    })
```

**File:** networks/bitcoin/src/wallet/send.rs (L273-285)
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

**File:** networks/bitcoin/src/wallet/send.rs (L413-428)
```rust
  fn complete(
    mut self,
    mut shares: HashMap<Participant, Self::SignatureShare>,
  ) -> Result<Transaction, FrostError> {
    for (input, schnorr) in self.tx.input.iter_mut().zip(self.sigs.drain(..)) {
      let sig = schnorr.complete(
        shares.iter_mut().map(|(l, shares)| (*l, shares.remove(0))).collect::<HashMap<_, _>>(),
      )?;

      let mut witness = Witness::new();
      witness.push(sig);
      input.witness = witness;
    }

    Ok(self.tx)
  }
```
