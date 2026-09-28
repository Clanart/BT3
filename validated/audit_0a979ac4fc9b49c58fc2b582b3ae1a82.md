### Title

Untrusted `ReceivedOutput` deserialization bypasses `Scanner` authorization and enables signing arbitrary wallet UTXOs - ([File: networks/bitcoin/src/wallet/mod.rs](networks/bitcoin/src/wallet/mod.rs))

### Summary

`ReceivedOutput::read` accepts an attacker-controlled scalar offset, `TxOut`, and `OutPoint` without proving that the object was produced by `Scanner` or that the referenced on-chain output was previously registered as spendable by the wallet. [1](#0-0) 

### Finding Description

The reported bug class is insufficient validation of an untrusted path-like component: `basic-ftp` filters only exact `"."` and `".."`, then joins an attacker-controlled filename into a restricted directory.

The analogous boundary in Serai is `Scanner`: it is intended to be the only authority that maps observed Bitcoin outputs to key offsets. `Scanner::new` registers the base key, `register_offset` maintains a map of permitted derived output scripts, and `scan_transaction` emits a `ReceivedOutput` only when the transaction output’s `script_pubkey` is present in that map. [2](#0-1) [3](#0-2) 

`ReceivedOutput::read` bypasses that map entirely. It accepts any canonical scalar offset and any syntactically valid `TxOut`/`OutPoint`, then returns a value indistinguishable from an output legitimately discovered by `Scanner`. [1](#0-0) 

The sink is `SignableTransaction::multisig`, which trusts each supplied `ReceivedOutput`, applies the supplied offset to the threshold keys, verifies only that the supplied `TxOut` script equals the derived script, and creates a FROST signing machine for that input. [4](#0-3) 

`TransactionSignMachine::sign` then calculates the Taproot signature hash for the attacker-selected `OutPoint`/`TxOut` pairing and obtains FROST signature shares for it. [5](#0-4) 

### Impact Explanation

An unprivileged party who can submit serialized `ReceivedOutput` bytes can escape the intended “registered scanner output” namespace and cause Serai to sign a transaction spending a different wallet UTXO than the one authorized by the scanner-derived input list.

Because Bitcoin transaction data is public, the attacker can identify a wallet-controlled `OutPoint`, copy its actual `TxOut`, and choose the known offset that derives its P2TR key. The resulting forged `ReceivedOutput` passes `multisig`’s script check and causes the threshold signing protocol to sign a spend of that UTXO. [6](#0-5) 

This is a concrete unauthorized-message-signing issue: the attacker-selected serialized input changes the Taproot sighash that is passed to `AlgorithmSignMachine::sign`. [7](#0-6) 

### Likelihood Explanation

The precondition is that an application feeds adversary-controlled serialized outputs into `ReceivedOutput::read` and subsequently includes the decoded value in `SignableTransaction::new`. That path is reachable without compromising validators, forging signatures, controlling a Bitcoin node, or obtaining key material.

The attacker must choose an `OutPoint` that actually refers to a wallet UTXO and use the corresponding offset/script pair. Such pairs are publicly observable after the wallet output exists, and for the base-key output the required offset is `Scalar::ZERO`. [8](#0-7) 

The issue requires a caller to treat `ReceivedOutput` serialization as proof of scanner provenance, even though the serialized format carries no such authentication. If `ReceivedOutput` bytes are only ever produced and consumed internally by the same trusted process, the exposed surface is reduced.

### Recommendation

Do not allow `ReceivedOutput::read` to mint scanner-authorized values from unauthenticated bytes.

Prefer one of:

- Remove the public `read` constructor and require all spendable inputs to originate from `Scanner::scan_transaction`/`Scanner::scan_block`.
- Add an authenticated provenance tag or MAC over `offset || output || outpoint || wallet_key`.
- Store and reload a scanner registration identifier, then re-derive the expected `ScriptBuf` and verify it is present in the `Scanner`’s registered `scripts` map before accepting the decoded output.
- If deserialization must remain public, rename/document it as an unchecked representation and add a separate `Scanner::validate_received_output` method that reconstructs the expected key/script and verifies prior registration and observed UTXO provenance.

At minimum, `SignableTransaction::multisig` should not treat deserialized `ReceivedOutput` provenance as equivalent to a `Scanner` result.

### Proof of Concept

```rust
// Conceptual PoC over public Bitcoin data.

// Let `wallet_utxo` be any confirmed UTXO paying to the wallet's base P2TR key.
let forged_bytes = {
    let mut bytes = Vec::new();

    // Base-key outputs use offset zero.
    bytes.extend_from_slice(Scalar::ZERO.to_bytes().as_ref());

    // Copy the exact TxOut from the public UTXO.
    bytes.extend_from_slice(&bitcoin::consensus::encode::serialize(&wallet_txout));

    // Copy its public outpoint.
    bytes.extend_from_slice(&bitcoin::consensus::encode::serialize(&wallet_outpoint));

    bytes
};

// This succeeds despite the output never being returned by Scanner.
let forged = ReceivedOutput::read(&mut forged_bytes.as_slice()).unwrap();

// Build an otherwise legitimate transaction request.
let signable = SignableTransaction::new(
    vec![forged],
    &payments,
    change,
    None,
    fee_per_vbyte,
).unwrap();

// The attacker-selected input reaches FROST signing.
let machine = signable.multisig(&threshold_keys).unwrap();
let (sign_machine, _preprocesses) = machine.preprocess(&mut OsRng);

// Once peer shares are supplied, `complete` returns a transaction whose input
// references `wallet_outpoint`, even though that output was not authorized by
// Scanner for this transaction.
```

The critical mismatch is that `Scanner::scan_transaction` proves membership in the registered script set before constructing `ReceivedOutput`, while `ReceivedOutput::read` reconstructs the same privileged type from raw offset, output, and outpoint fields without enforcing that membership. [9](#0-8) [10](#0-9)

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

**File:** networks/bitcoin/src/wallet/mod.rs (L151-195)
```rust
/// A transaction scanner capable of being used with HDKD schemes.
#[derive(Clone, Debug)]
pub struct Scanner {
  key: ProjectivePoint,
  scripts: HashMap<ScriptBuf, Scalar>,
}

impl Scanner {
  /// Construct a Scanner for a key.
  ///
  /// Returns None if this key can't be scanned for.
  pub fn new(key: ProjectivePoint) -> Option<Scanner> {
    let mut scripts = HashMap::new();
    scripts.insert(p2tr_script_buf(key)?, Scalar::ZERO);
    Some(Scanner { key, scripts })
  }

  /// Register an offset to scan for.
  ///
  /// Due to Bitcoin's requirement that points are even, not every offset may be used.
  /// If an offset isn't usable, it will be incremented until it is. If this offset is already
  /// present, None is returned. Else, Some(offset) will be, with the used offset.
  ///
  /// This means offsets are surjective, not bijective, and the order offsets are registered in
  /// may determine the validity of future offsets.
  ///
  /// The offsets registered must be securely generated. Arbitrary offsets may introduce a script
  /// path into the output, allowing the output to be spent by satisfaction of an arbitrary script
  /// (not by the signature of the key).
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
```

**File:** networks/bitcoin/src/wallet/mod.rs (L198-214)
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
  }
```

**File:** networks/bitcoin/src/wallet/send.rs (L270-284)
```rust
  /// Create a multisig machine for this transaction.
  ///
  /// Returns None if the wrong keys are used.
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
