### Title
`ReceivedOutput::read` trusts an attacker-supplied `(offset, TxOut)` pair without verifying the script_pubkey actually derives from `key + offset*G` — (File: networks/bitcoin/src/wallet/mod.rs)

### Summary
CVE-2019-9854 is a verification-bypass class bug: a path was validated in sanitized form, but the object actually used was reassembled from the unsanitized input components, so the check and the use disagreed. The analog in Serai's in-scope code is `ReceivedOutput`: the *scanner* establishes the binding between an on-chain `script_pubkey` and the scalar `offset` needed to spend it, but `ReceivedOutput::read` deserializes `offset` and `TxOut` as independent attacker-controlled fields and never re-derives/verifies the binding. Any consumer that deserializes a `ReceivedOutput` trusts a pairing that was never validated.

### Finding Description
The offset-to-script binding is established only inside `Scanner`: `register_offset` inserts `p2tr_script_buf(key + GENERATOR * offset)` into `scripts`, and `scan_transaction` looks up `self.scripts.get(&output.script_pubkey)` to pair a real output with the correct offset [1](#0-0) . That is the "path verification step" — the script pubkey is the checked artifact, and the offset is what makes the output spendable.

`ReceivedOutput::read`, however, reads `offset` via `Secp256k1::read_F` and then consensus-decodes an arbitrary `TxOut` and `OutPoint`, returning the struct with no consistency check [2](#0-1) . The struct exposes `offset()`, `output()`, `outpoint()`, and `value()` independently [3](#0-2) , and `write`/`serialize` round-trip exactly the raw fields [4](#0-3)  — mirroring the CVE pattern where the final object is assembled from the passed-in components rather than from the verified/sanitized output.

Downstream, the wallet treats the pair as authoritative: `SignableTransaction` consumes `Vec<ReceivedOutput>` as spendable inputs and signs each input with the key reconstructed from `key + offset*G` [5](#0-4) . If the `script_pubkey` inside the deserialized `TxOut` does not equal `p2tr_script_buf(key + offset*G)` (which `read` never checks — and cannot be a real P2TR match unless the attacker knows a consistent offset), the prevout's actual script requires a different private key, so the produced signature/witness is invalid and the UTXO is unspendable.

### Impact Explanation
An unprivileged party who can feed bytes to `ReceivedOutput::read` (listed as an untrusted-bytes sink in scope) can cause the wallet to report and account funds as received that are not actually spendable by the group key. Effects: the scheduler/wallet books the output's `value()` as available balance, and any spend attempt produces a transaction whose signature does not satisfy the real `script_pubkey` — the input is permanently unspendable while being counted as held funds. This matches the accepted impact class "funds reported received that are not spendable." A variant pairs a valid scanned offset with a *different* real on-chain outpoint paying to an attacker-chosen P2TR key: still unspendable, and worse, it can displace legitimate output tracking.

### Likelihood Explanation
Reachability is through any path where `ReceivedOutput`s are persisted/deserialized rather than freshly produced by `Scanner::scan_transaction`/`scan_block` — e.g., DB or message round-trips via `read`/`Output::read` (which embeds `ReceivedOutput::read` [6](#0-5) ). Exploitation requires no secret knowledge — only the ability to supply malformed serialized output records — but does require that the consumer accepts records not produced by the local scanner, which confines this to deserialization entry points rather than arbitrary network input. Medium likelihood, Medium/High impact class.

### Recommendation
Bind the checked artifact to the used artifact, as the fixed LibreOffice did by building the URL solely from the sanitized path. Concretely: either (a) make `ReceivedOutput` construction infallible-proof by storing the scanner key and adding `ReceivedOutput::read_for(key, reader)` that asserts `p2tr_script_buf(key + GENERATOR * offset) == Some(output.script_pubkey)` after decode; or (b) drop `offset` from the serialized form entirely and re-derive it at read time via the `Scanner`'s `scripts` map, so the offset used for signing is always produced by the same lookup that validated the script_pubkey — never from the wire. At minimum, document and enforce that `read` output must be re-validated against a `Scanner` before being passed to `SignableTransaction::new`.

### Proof of Concept
```rust
// networks/bitcoin context; requires a Scanner-registered key
let key: ProjectivePoint = /* group key, even-Y */;
let mut scanner = Scanner::new(key).unwrap();
let offset = scanner.register_offset(Scalar::random(&mut OsRng)).unwrap();

// Attacker crafts a TxOut paying to THEIR OWN P2TR key,
// paired with the victim's valid `offset`:
let attacker_key = ProjectivePoint::GENERATOR * Scalar::random(&mut OsRng);
let evil = ReceivedOutput {
    offset,                                   // valid offset (wrong: belongs to another script)
    output: TxOut {
        value: Amount::from_sat(100_000),
        script_pubkey: p2tr_script_buf(attacker_key).unwrap(), // attacker-controlled script
    },
    outpoint: OutPoint::new(real_txid, 0),    // a real on-chain outpoint
};

let bytes = evil.serialize();
let decoded = ReceivedOutput::read(&mut bytes.as_slice()).unwrap(); // accepted, no binding check

// SignableTransaction::new(vec![decoded], ...) will sign with key + offset*G,
// which does NOT control `script_pubkey` — the input is counted as 100k sats
// received but can never be spent by the group key.
```
`read` accepts the mismatched pair because it decodes `offset` and `TxOut` independently and performs no `p2tr_script_buf(key + GENERATOR*offset) == script_pubkey` verification [2](#0-1) .

Caveat: I could not fully trace every `ReceivedOutput::read` call site to confirm a directly attacker-reachable deserialization path in this iteration; the finding stands on the missing binding check itself, which mirrors the CVE-2019-9854 pattern of using unverified components where a verified pairing was assumed.

### Citations

**File:** networks/bitcoin/src/wallet/mod.rs (L99-118)
```rust
impl ReceivedOutput {
  /// The offset for this output.
  pub fn offset(&self) -> Scalar {
    self.offset
  }

  /// The Bitcoin output for this output.
  pub fn output(&self) -> &TxOut {
    &self.output
  }

  /// The outpoint for this output.
  pub fn outpoint(&self) -> &OutPoint {
    &self.outpoint
  }

  /// The value of this output.
  pub fn value(&self) -> u64 {
    self.output.value.to_sat()
  }
```

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

**File:** networks/bitcoin/src/wallet/mod.rs (L136-148)
```rust
  /// Write a ReceivedOutput to a generic satisfying Write.
  pub fn write<W: Write>(&self, w: &mut W) -> io::Result<()> {
    w.write_all(&self.offset.to_bytes())?;
    w.write_all(&serialize(&self.output))?;
    w.write_all(&serialize(&self.outpoint))
  }

  /// Serialize a ReceivedOutput to a `Vec<u8>`.
  pub fn serialize(&self) -> Vec<u8> {
    let mut res = Vec::new();
    self.write(&mut res).unwrap();
    res
  }
```

**File:** networks/bitcoin/src/wallet/mod.rs (L180-213)
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
```

**File:** networks/bitcoin/src/wallet/send.rs (L373-398)
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
  }
```

**File:** processor/src/networks/bitcoin.rs (L145-166)
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
  }
```
