### Title
`ReceivedOutput::read` trusts unvalidated offset/script binding, causing funds to be reported as received which are not spendable - (File: networks/bitcoin/src/wallet/mod.rs)

### Summary

The analog to CVE-2022-24637 — where attacker-influenced bytes are later interpreted as trusted, privilege-carrying data without structural validation — exists in `ReceivedOutput::read` in `networks/bitcoin/src/wallet/mod.rs`. `ReceivedOutput` is the wallet's claim "this outpoint is spendable by our group key after applying `offset`". The `Scanner` constructs this invariant correctly (it only creates a `ReceivedOutput` after matching `output.script_pubkey` against `p2tr(key + G*offset)`), but the deserializer accepts arbitrary `offset`, `TxOut`, and `OutPoint` fields with no consistency check between them. Untrusted bytes fed to `ReceivedOutput::read` therefore produce an object asserting spendability that does not hold.

### Finding Description

`ReceivedOutput::read` performs three independent parses: a scalar via `Secp256k1::read_F`, a `TxOut` via consensus decode, and an `OutPoint` via consensus decode:

```rust
// networks/bitcoin/src/wallet/mod.rs
pub fn read<R: Read>(r: &mut R) -> io::Result<ReceivedOutput> {
    let offset = Secp256k1::read_F(r)?;
    let output;
    let outpoint;
    {
      let mut buf_r = BufReader::with_capacity(0, r);
      output = TxOut::consensus_decode(&mut buf_r)...;
      outpoint = OutPoint::consensus_decode(&mut buf_r)...;
    }
    Ok(ReceivedOutput { offset, output, outpoint })
}
``` [1](#0-0) 

The invariant `output.script_pubkey == p2tr_script_buf(scanner.key + G*offset)` — which is the only thing that makes the output spendable by `keys.offset(offset)` — is established by `Scanner::scan_transaction` (`self.scripts.get(&output.script_pubkey)`) at scan time, but is never re-validated on deserialization: [2](#0-1) 

Downstream, `TransactionSignMachine` / `SignableTransaction` consume `ReceivedOutput` and offset the threshold keys by `output.offset()` before producing BIP-340 signatures (`sig.sign(...)` per input, keyed via the offset share). The spend path derives the signing view from the untrusted `offset` field while the sighash commits to the actual prevout `script_pubkey`. A deserialization-path `ReceivedOutput` whose `offset` does not match its `output.script_pubkey` yields a key whose signatures are invalid for that output. [3](#0-2) 

Notably, the processor-side wrapper does assert `output.key() == key` (`processor/src/networks/bitcoin.rs`), but `bitcoin-serai`'s own `wallet/` crate — in scope and usable standalone — exposes `ReceivedOutput::read` as a public, untrusted-bytes entrypoint with no such check. [4](#0-3) 

### Impact Explanation

An attacker who supplies crafted `ReceivedOutput` bytes (e.g., `offset = k` paired with a `script_pubkey` for `key + G*j`, `j != k`, or any unrelated P2TR script) causes the wallet to record an input it believes is spendable under `key + G*k`. When spent via `TransactionSignMachine`, the threshold signing produces a signature valid for the offset key — which does not equal the output's actual Taproot key — so the resulting transaction is consensus-invalid and the funds are unspendable despite being reported received. This matches the accepted impact class "funds reported received that are not spendable". Severity: Medium (integrity violation causing fund loss/DoS of the wallet's UTXO set; requires untrusted bytes to reach `ReceivedOutput::read`, e.g., via replicated/forwarded output records or a compromised-but-not-arbitrary data path).

### Likelihood Explanation

The library explicitly exposes `ReceivedOutput::read`/`write`/`serialize` for round-tripping outputs through storage and transport (`write`/`serialize` exist precisely so outputs can be persisted and later re-read). Any pipeline that persists scanned outputs and reloads them — or relays outputs between components — feeds untrusted bytes to `read` without cryptographic binding between the three fields. The OWA analog holds: like the malformed `<?php` cache file that PHP silently treats as literal content, the deserializer treats a privilege-bearing claim ("I am spendable under offset k") as a plain container with no proof of the claim.

### Recommendation

On deserialization, verify the binding between `offset` and `output.script_pubkey`. Since `ReceivedOutput` does not carry the base key, either (a) include the base/derived key or the scanner context in the serialized form and check `p2tr_script_buf(base + G*offset) == output.script_pubkey`, or (b) store and check the x-only output key the offset was registered against. Alternatively, document `read` as trusting the producer and make consumers re-derive the expected script (as `processor`'s `Output::key()` does implicitly) — but the standalone `wallet/` API should not silently produce unspendable-input claims.

### Proof of Concept

```rust
// networks/bitcoin — conceptual PoC
use bitcoin::{OutPoint, TxOut, Amount, Txid, hashes::Hash};
use k256::{ProjectivePoint, Scalar};

let mut scanner = Scanner::new(key).unwrap();
// Legit output scanned: script = p2tr(key + G*offset_a)
let legit = scanner.scan_transaction(&tx).pop().unwrap();

// Attacker crafts bytes: keeps the TxOut (script for offset_a)
// but substitutes offset_b != offset_a
let mut buf = vec![];
buf.extend(Scalar::from(2u64).to_bytes());      // forged offset
buf.extend(bitcoin::consensus::encode::serialize(legit.output()));
buf.extend(bitcoin::consensus::encode::serialize(legit.outpoint()));
let forged = ReceivedOutput::read(&mut buf.as_slice()).unwrap();

// forged.offset() == 2, yet forged.output().script_pubkey corresponds to
// key + G*offset_a. Signing forged via SignableTransaction/TransactionSignMachine
// produces a BIP-340 signature for key + G*2, which is invalid for the prevout:
// the wallet reports the output as spendable but can never spend it.
assert_ne!(forged.offset(), legit.offset());
assert_eq!(forged.output(), legit.output()); // read accepted the mismatch
```

The vulnerability is the absence of any check inside `ReceivedOutput::read` (lines 122–134) tying the `offset` field to `output.script_pubkey`, allowing attacker-controlled bytes to masquerade as a valid spendable-output record — directly paralleling untrusted cache content being trusted as privileged data in CVE-2022-24637.

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

**File:** networks/bitcoin/src/wallet/mod.rs (L199-213)
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
```

**File:** networks/bitcoin/src/wallet/send.rs (L321-398)
```rust
pub struct TransactionSignMachine {
  tx: SignableTransaction,
  sigs: Vec<AlgorithmSignMachine<Secp256k1, Schnorr>>,
}

impl SignMachine<Transaction> for TransactionSignMachine {
  type Params = ();
  type Keys = ThresholdKeys<Secp256k1>;
  type Preprocess = Vec<Preprocess<Secp256k1, ()>>;
  type SignatureShare = Vec<SignatureShare<Secp256k1>>;
  type SignatureMachine = TransactionSignatureMachine;

  fn cache(self) -> CachedPreprocess {
    unimplemented!(
      "Bitcoin transactions don't support caching their preprocesses due to {}",
      "being already bound to a specific transaction"
    );
  }

  fn from_cache(
    (): (),
    _: ThresholdKeys<Secp256k1>,
    _: CachedPreprocess,
  ) -> (Self, Self::Preprocess) {
    unimplemented!(
      "Bitcoin transactions don't support caching their preprocesses due to {}",
      "being already bound to a specific transaction"
    );
  }

  fn read_preprocess<R: Read>(&self, reader: &mut R) -> io::Result<Self::Preprocess> {
    self.sigs.iter().map(|sig| sig.read_preprocess(reader)).collect()
  }

  fn sign(
    mut self,
    commitments: HashMap<Participant, Self::Preprocess>,
    msg: &[u8],
  ) -> Result<(TransactionSignatureMachine, Self::SignatureShare), FrostError> {
    if !msg.is_empty() {
      panic!("message was passed to the TransactionSignMachine when it generates its own");
    }

    let commitments = (0 .. self.sigs.len())
      .map(|c| {
        commitments
          .iter()
          .map(|(l, commitments)| (*l, commitments[c].clone()))
          .collect::<HashMap<_, _>>()
      })
      .collect::<Vec<_>>();

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
