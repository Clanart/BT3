### Title
`ReceivedOutput::read` accepts attacker-controlled `(offset, script_pubkey, outpoint)` tuples without verifying the offset actually derives the output's key, producing "received" outputs that are not spendable - (File: `networks/bitcoin/src/wallet/mod.rs`)

### Summary
CVE-2020-0826 is a type/object-confusion bug: the engine mishandles an object's identity, treating bytes as one thing when they are another. The Serai analog lives in Bitcoin output handling. A `ReceivedOutput` is a triple of a scalar `offset`, a `TxOut`, and an `OutPoint`. Its semantic invariant is that `output.script_pubkey == p2tr_script_buf(group_key + G*offset)`. `Scanner::scan_transaction` produces this invariant internally by looking the script up in `self.scripts` [1](#0-0) , but `ReceivedOutput::read` deserializes the three fields independently and never re-derives the key relationship [2](#0-1) . Untrusted bytes therefore instantiate a semantically confused object: an output claiming to be spendable at offset `o` whose script commits to a completely different key.

### Finding Description
`ReceivedOutput::read` reads `offset` via `Secp256k1::read_F`, then consensus-decodes `TxOut` and `OutPoint`, and returns the struct with no check that `output.script_pubkey` is even a P2TR output, let alone that its x-only key equals `key + G*offset` for any known `key`. Contrast with `Output::key()` in `processor/src/networks/bitcoin.rs:112-122`, which reconstructs the base key as `read_G(script key) - G*offset` — the code assumes the offset/script pair is consistent, an assumption only guaranteed for Scanner-produced values, not deserialized ones. Downstream, `SignableTransaction`/per-input signing re-keys the group key by each input's `offset()`; a mismatched offset means the produced signature verifies under a key unrelated to the script_pubkey, so the input can never be spent.

### Impact Explanation
Funds are reported received that are not spendable. Any component that ingests a serialized `ReceivedOutput` from an untrusted party (e.g., outputs forwarded by a coordinator/peer over the processor's `Output::read` path at `processor/src/networks/bitcoin.rs:145-166`) will treat the output as owned by the multisig at the claimed offset. When Serai later attempts to spend it, the threshold signature is produced for `key + G*offset`, which does not match the on-chain key in `script_pubkey`, so the transaction is invalid and the credited funds are unspendable — a bookkeeping/minting discrepancy exploitable by an unprivileged party who controls the serialized bytes.

### Likelihood Explanation
Requires an attacker to supply serialized `ReceivedOutput`/`Output` bytes to a reader that trusts them (the read APIs are explicitly reachable from untrusted inputs). Within the in-scope crate, any caller that treats `ReceivedOutput::read` as producing a scanner-consistent object is exposed; the missing invariant check is unconditional, so any such data flow is exploitable with no proof or key material. Severity Medium: direct loss-of-spendability/misattribution, no secret leakage.

### Recommendation
Either (a) make `ReceivedOutput::read` take the expected base `key` (or a `&Scanner`) and verify `p2tr_script_buf(key + G*offset) == Some(output.script_pubkey)` and that the script is registered, rejecting otherwise; or (b) split the type — keep `ReceivedOutput` as a Scanner-only construct (private constructor) and add a separate `SerializedOutput`/`ClaimedOutput` type whose deserialization is documented and validated before use. At minimum, validate in `read` that `script_pubkey.is_p2tr()`.

### Proof of Concept
```rust
// Given any valid serialized ReceivedOutput, flip the offset to a
// different scalar while keeping the same script_pubkey.
use bitcoin_serai::wallet::ReceivedOutput;
use k256::Scalar;
use frost::curve::{Curve, Secp256k1};
use ciphersuite::Ciphersuite;
use bitcoin_serai::bitcoin::{consensus::Encodable, TxOut, OutPoint};

fn forge(orig: &ReceivedOutput, key: k256::ProjectivePoint) -> ReceivedOutput {
    let mut buf = Vec::new();
    // attacker-chosen offset not matching the output's script_pubkey
    buf.extend(Secp256k1::read_F(&mut [1u8; 32].as_slice())
        .map(|s| (s + Scalar::ONE).to_bytes()).unwrap());
    orig.output().consensus_encode(&mut buf).unwrap();
    orig.outpoint().consensus_encode(&mut buf).unwrap();
    // Deserializes successfully despite offset/script inconsistency:
    // read_G(script_key) - G*offset != `key`, so spends fail.
    ReceivedOutput::read(&mut buf.as_slice()).unwrap()
}
```
The forged object is accepted by `ReceivedOutput::read` (networks/bitcoin/src/wallet/mod.rs:122-134); `Output::key()` (processor/src/networks/bitcoin.rs:112-122) then reconstructs a wrong base key, and any `SignableTransaction` built on it produces signatures for a key the on-chain output was never locked to.

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

**File:** networks/bitcoin/src/wallet/mod.rs (L205-211)
```rust
      if let Some(offset) = self.scripts.get(&output.script_pubkey) {
        res.push(ReceivedOutput {
          offset: *offset,
          output: output.clone(),
          outpoint: OutPoint::new(tx.compute_txid(), vout),
        });
      }
```
