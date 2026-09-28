### Title
`ReceivedOutput::read` accepts untrusted `offset`/`script_pubkey` pairs without verifying their correspondence, deferring the check to signing where it panics — reported funds become unspendable / signer DoS - (File: networks/bitcoin/src/wallet/mod.rs)

### Summary
This is a TOCTOU-class (check/use mismatch) analog to CVE-2024-28718: the binding between an output's scalar `offset` and its `script_pubkey` is established at scan time (`Scanner::scan_transaction` derives the offset by looking up the script), but `ReceivedOutput::read` deserializes `offset`, `output` (TxOut), and `outpoint` as three independent, unauthenticated fields with no consistency check. The only place the correspondence is re-verified is `SignableTransaction::multisig`, which returns `None` on mismatch — and the sole caller turns that into a panic.

### Finding Description
`ReceivedOutput` stores three attacker-controlled fields:

- `offset` — the HDKD scalar used to derive the spend key,
- `output` — the `TxOut` (script_pubkey + value),
- `outpoint`. [1](#0-0) 

`read` performs no check that `output.script_pubkey == p2tr_script_buf(key + G * offset)` — the invariant `Scanner::scan_transaction` guarantees when outputs are discovered organically: [2](#0-1) 

The mismatch is only detected later, at "use" time, in `SignableTransaction::multisig`, which returns `None` if `p2tr_script_buf(offset.group_key()) != prevouts[i].script_pubkey`: [3](#0-2) 

The caller in the processor treats `None` as unreachable and panics: [4](#0-3) 

Meanwhile `Output::balance()` reports `output.value()` to Serai as received funds regardless of spendability, and `Output::key()` independently recomputes the presumed spend key from the raw script bytes minus `offset` — a second consumer of the same unchecked invariant: [5](#0-4) [6](#0-5) 

### Impact Explanation
An unprivileged party who can feed bytes to `ReceivedOutput::read` (the struct is serialized/deserialized across processor components and storage) can submit a `ReceivedOutput` whose `output.value` is large (credited as received Bitcoin via `balance()`) while its `offset` does not match `script_pubkey`. The funds are reported received but are not spendable: when the coordinator attempts to spend the output, `multisig` returns `None` and `attempt_sign` panics via `.expect("used the wrong keys")`, permanently stalling that signing attempt and denying availability of the signer. Alternatively, a `script_pubkey` that isn't P2TR at all causes `Output::key()` to panic on `assert!(script.is_p2tr())`. Either way, attacker-supplied bytes cause received-funds accounting to diverge from spendable reality and crash the processor.

### Likelihood Explanation
The vulnerability requires an attacker to influence serialized `ReceivedOutput`/`Output` bytes. In the honest path (`Scanner`), the invariant holds, so the bug is latent; but `read` is explicitly an untrusted-bytes entry point per the codebase's own threat surface, and the same encoding is consumed by `Output::read` in the processor. Severity is Medium: no key material leaks and no unintended message is signed (the deferred check still prevents a bad signature), but funds are credited as received that can never be spent, plus a reliable panic/DoS.

### Recommendation
Validate the `offset` ↔ `script_pubkey` binding inside `ReceivedOutput::read` (or add a `verify(&self, key: &ProjectivePoint)` called immediately after deserialization) rather than deferring it to `multisig`. Additionally, `attempt_sign` should not `.expect` on `multisig`'s `None` — it should treat a malformed stored output as a recoverable error and slash/discard the output instead of panicking.

### Proof of Concept
```rust
// Attacker crafts a ReceivedOutput with a mismatched offset/script.
let real = scanner.scan_transaction(&tx).pop().unwrap();
let mut buf = real.serialize();

// Overwrite the leading scalar `offset` with a different scalar.
let evil_offset = Scalar::ONE;
buf[.. 32].copy_from_slice(evil_offset.to_bytes().as_ref());

// ReceivedOutput::read accepts it silently — no consistency check.
let forged = ReceivedOutput::read(&mut buf.as_slice()).unwrap();

// balance() reports the full value as received funds...
assert_eq!(forged.value(), real.value());

// ...but at spend time, multisig returns None and the processor panics:
// transaction.actual.multisig(&keys).expect("used the wrong keys")
let tx = SignableTransaction::new(vec![forged], &payments, None, None, FEE).unwrap();
assert!(tx.multisig(&keys).is_none()); // -> expect() panics in attempt_sign
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

**File:** processor/src/networks/bitcoin.rs (L128-130)
```rust
  fn balance(&self) -> ExternalBalance {
    ExternalBalance { coin: ExternalCoin::Bitcoin, amount: Amount(self.output.value()) }
  }
```

**File:** processor/src/networks/bitcoin.rs (L836-842)
```rust
  async fn attempt_sign(
    &self,
    keys: ThresholdKeys<Self::Curve>,
    transaction: Self::SignableTransaction,
  ) -> Result<Self::TransactionMachine, NetworkError> {
    Ok(transaction.actual.clone().multisig(&keys).expect("used the wrong keys"))
  }
```
