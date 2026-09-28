### Title
Unvalidated `ReceivedOutput` value/outpoint lets attacker-crafted bytes cause the FROST multisig to sign a sighash committing to a fabricated prevout amount - (File: networks/bitcoin/src/wallet/send.rs)

### Summary
`ReceivedOutput::read` accepts an arbitrary `TxOut` (including its `value`) and `OutPoint` with no consistency check, and `SignableTransaction`/`TransactionSignMachine` then trust that value both for fee/funding arithmetic and for the BIP-341 `Prevouts::All` commitment inside every input's sighash. This is the Serai analog of "oracle output used without validation": a value that is supposed to reflect an on-chain fact (the UTXO's true amount and outpoint) is consumed verbatim to build the message that gets threshold-signed.

### Finding Description
`ReceivedOutput::read` deserializes `offset`, `TxOut`, and `OutPoint` straight from untrusted bytes with only a canonical-scalar check on the offset; the claimed value and outpoint are never verified against anything. [1](#0-0) 

`SignableTransaction::new` sums `input.output.value` into `input_sat` (used for the `NotEnoughFunds` check) and copies the full attacker-controlled `TxOut` list into `self.prevouts`. [2](#0-1) [3](#0-2) 

`SignableTransaction::multisig` validates only the prevout `script_pubkey` against the offset group key — it never checks that `prevouts[i].value` or `outpoint` correspond to a real, unspent UTXO. [4](#0-3) 

`TransactionSignMachine::sign` then computes `taproot_key_spend_signature_hash(i, &Prevouts::All(&self.tx.prevouts), Default)`, which per BIP-341 commits to every input's amount and scriptPubKey. A fabricated amount therefore changes the signed message itself. [5](#0-4) 

### Impact Explanation
An attacker who feeds crafted bytes to `ReceivedOutput::read` (e.g., an inflated or understated `value`, or an outpoint pointing at a nonexistent/different UTXO) causes the threshold group to produce a FROST signature over a sighash that commits to a false prevout amount. Under BIP-341 the signature is bound to that amount, so the resulting transaction is invalid on-chain — the signature ceremony is consumed on an unintended message and the "received" funds are not spendable as signed. If the value is inflated, `NotEnoughFunds`/change logic is computed against phantom balance; if understated, the real surplus is implied as fee in fee accounting while the signature still fails to validate. In both cases the multisig emits a valid FROST signature over a message whose committed prevout data does not reflect chain state — the exact "wrong/stale oracle value signed and acted upon" pattern.

### Likelihood Explanation
Exploitation requires an attacker to supply serialized `ReceivedOutput` bytes (or a stale/incorrect scan result) into the component constructing `SignableTransaction`. The `read` API is public and the struct is cloneable/serializable across process boundaries, so any deserialization boundary where outputs are persisted, forwarded, or accepted from another party is a reachable path. Severity is bounded to Medium/High because the forged signature is unbroadcastable (invalid witness), limiting impact to signing an unintended message and burning the signing ceremony/fee accounting rather than direct theft of funds.

### Recommendation
- In `SignableTransaction::new` or `multisig`, verify each `ReceivedOutput` against the actual UTXO: confirm the `outpoint` resolves and that the on-chain `TxOut` equals `output` (amount and `script_pubkey`), or at minimum that `script_pubkey` and `value` match the entry recorded by `Scanner`/`get_outputs`.
- Treat `ReceivedOutput::read` output as untrusted: add a `verify`-style method that recomputes `p2tr_script_buf(key + G*offset)` and requires it to equal `output.script_pubkey`, rejecting outputs whose offset doesn't regenerate the claimed script before they ever reach the signer.
- Document that `fee()`/`needed_fee()` are meaningless if `prevouts` values do not reflect confirmed UTXOs.

### Proof of Concept
```rust
use std::io::Cursor;
use bitcoin::{TxOut, OutPoint, Amount, ScriptBuf, hashes::Hash, Txid};
use serai_bitcoin::wallet::{ReceivedOutput, SignableTransaction};
use frost::curve::{Secp256k1, Ciphersuite};
use frost::ThresholdKeys;

// Attacker crafts a serialized ReceivedOutput claiming an inflated value
let mut bytes = vec![];
bytes.extend(Secp256k1::read_F /* zero offset serialization */); // offset = Scalar::ZERO
// TxOut { value: 1_000_000_000, script_pubkey: <victim key's p2tr script> }
// OutPoint { txid: <any>, vout: 0 }
let forged = ReceivedOutput::read(&mut bytes.as_slice()).unwrap();

// SignableTransaction::new trusts forged.output.value:
//   - input_sat uses the fake 1 BTC
//   - prevouts stores the fake TxOut
// multisig() only checks script_pubkey == p2tr_script_buf(offset_key),
// NOT that the UTXO at outpoint has that value.
// sign() commits Prevouts::All(&prevouts) into taproot_key_spend_signature_hash,
// so the produced FROST signature commits to the fabricated amount and the
// transaction is unbroadcastable — a signature over an unintended message.
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

**File:** networks/bitcoin/src/wallet/send.rs (L175-176)
```rust
    let input_sat = inputs.iter().map(|input| input.output.value.to_sat()).sum::<u64>();
    let offsets = inputs.iter().map(|input| input.offset).collect();
```

**File:** networks/bitcoin/src/wallet/send.rs (L253-255)
```rust
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

**File:** networks/bitcoin/src/wallet/send.rs (L373-391)
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
```
