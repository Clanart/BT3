### Title
ReceivedOutput::read accepts untrusted (offset, script_pubkey) pairs without verifying they correspond, reporting unspendable/misattributed funds - (File: networks/bitcoin/src/wallet/mod.rs)

### Summary
The upstream bug class is a caller-reachable function consuming a user-controlled value without a validity check (missing `irq` bounds check in `kvm_vcpu_ioctl_interrupt`). The Serai analog is `ReceivedOutput::read` in `bitcoin-serai`, which deserializes an attacker-controlled `offset` scalar together with a `TxOut`/`OutPoint` and never checks that the offset actually derives the output's script key from a wallet key. Outputs produced honestly by `Scanner::scan_transaction` always satisfy `p2tr_script(key + offset*G) == output.script_pubkey`; nothing enforces that invariant on deserialized bytes.

### Finding Description
`Scanner::register_offset`/`scan_transaction` construct `ReceivedOutput { offset, output, outpoint }` only after matching `output.script_pubkey` against a script derived as `key + GENERATOR * offset`, so the `offset` is guaranteed to be the scalar that links the wallet key to the output key [1](#0-0) . `ReceivedOutput::read`, by contrast, reads each field independently — `Secp256k1::read_F`, `TxOut::consensus_decode`, `OutPoint::consensus_decode` — and returns the tuple with no consistency check whatsoever [2](#0-1) .

Downstream, the processor trusts the deserialized `offset` to recover the owning key: `Output::key` computes `script_pubkey_key - GENERATOR * offset` [3](#0-2) , and `scan`/`Output::read` feed `ReceivedOutput::read` directly on stored bytes [4](#0-3) . An attacker supplying crafted bytes to `ReceivedOutput::read` can therefore pair any victim P2TR `script_pubkey` with an arbitrary `offset`, causing `key()` to return `victim_key - offset*G` — a key the multisig does not hold — while `balance()` still reports the output's full value.

### Impact Explanation
This matches the "funds reported received that are not spendable" impact class. A forged `ReceivedOutput` is recorded with the victim's script_pubkey (so `balance()` reports real-looking BTC) but an offset that does not correspond to the scanner's key. The derived `key()` is wrong, so any spend attempt produces a `SignableTransaction`/signing session for a key whose shares don't exist — the reported funds can never actually be spent, and the offset misattribution can also misdirect scheduler/accounting logic that buckets outputs by `key()`.

### Likelihood Explanation
Reachable whenever untrusted bytes are fed to `ReceivedOutput::read` (explicitly an in-scope sink) — e.g., outputs serialized through untrusted storage or messages in an integrator. The attacker only needs to know a target P2TR script (public on-chain data) and choose any scalar; no signing capability or collusion is required. Severity is bounded because exploitation requires an integrator path that accepts externally supplied `ReceivedOutput`s rather than scanner-produced ones; the primitive itself does no signature forgery.

### Recommendation
Bind the offset to the output on deserialization. Either (a) store the wallet key (or a tag of it) inside `ReceivedOutput` and verify `p2tr_script_buf(key + GENERATOR * offset) == output.script_pubkey` in `read`, or (b) make `read` take the expected key/script set as a parameter so the relationship is checked at parse time, mirroring how `ThresholdKeys::read` re-validates `ThresholdParams::new` and share counts rather than trusting raw fields [5](#0-4) .

### Proof of Concept
```rust
// Attacker crafts bytes: valid scalar offset, victim's TxOut, arbitrary OutPoint
let mut buf = vec![];
buf.extend(attacker_chosen_offset.to_bytes());      // any Scalar
buf.extend(serialize(&TxOut {
    value: Amount::from_sat(100_000),
    script_pubkey: victim_p2tr_script,              // a real Serai vault script
}));
buf.extend(serialize(&OutPoint::new(real_txid, 0)));

// ReceivedOutput::read accepts it: no check that
// p2tr_script_buf(wallet_key + G*offset) == script_pubkey
let forged = ReceivedOutput::read::<&[u8]>(&mut buf.as_ref()).unwrap();

// Downstream: Output::key() returns victim_key - offset*G, a key nobody holds,
// while balance() reports 100_000 sats -> funds "received" yet unspendable.
```

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

**File:** networks/bitcoin/src/wallet/mod.rs (L185-212)
```rust
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
```

**File:** processor/src/networks/bitcoin.rs (L112-122)
```rust
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

**File:** crypto/dkg/src/lib.rs (L625-632)
```rust
    ThresholdKeys::new(
      ThresholdParams::new(t, n, i).map_err(io::Error::other)?,
      interpolation,
      secret_share,
      verification_shares,
    )
    .map_err(io::Error::other)
  }
```
