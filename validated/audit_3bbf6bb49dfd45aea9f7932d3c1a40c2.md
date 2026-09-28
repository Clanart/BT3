### Title
Deserialized `ReceivedOutput` can report phantom funds and create permanently unspendable transactions - ([File: networks/bitcoin/src/wallet/mod.rs](networks/bitcoin/src/wallet/mod.rs))

### Summary
`ReceivedOutput::read` accepts an offset, `TxOut`, and `OutPoint` as independent serialized fields without authenticating them against each other or against the Bitcoin UTXO set. An attacker who can supply bytes consumed by `ReceivedOutput::read` can therefore claim that a nonexistent or incorrectly valued output belongs to the threshold wallet.

This is analogous to the Velar accounting imbalance: the wallet’s accounting sum uses the declared `output.value`, while the actual spendable ledger value is whatever exists at `outpoint` on chain. These values can disagree, causing funds to be accounted as received while the resulting Bitcoin transaction can never be spent or confirmed.

### Finding Description
`ReceivedOutput` represents an output as three attacker-controlled fields:

```rust
pub struct ReceivedOutput {
  offset: Scalar,
  output: TxOut,
  outpoint: OutPoint,
}
```

The deserializer reads each field independently and returns the aggregate without validating that `outpoint` exists, that the referenced UTXO contains `output.value`, or that the UTXO has the encoded `script_pubkey`. [1](#0-0) 

`SignableTransaction::new` then trusts `input.output.value` when calculating the available input balance, and trusts `input.outpoint` when constructing the transaction inputs. [2](#0-1) 

The only ownership check during multisig construction verifies that `offset` derives the `script_pubkey` stored in the supplied `TxOut`; it does not prove that the referenced on-chain UTXO has that script or amount. [3](#0-2) 

Because Taproot signing commits to the supplied `Prevouts::All`, a transaction whose encoded `TxOut` does not match the actual UTXO produces signatures over false prevout data. If the outpoint does not exist, the transaction is invalid regardless of the signatures. [4](#0-3) 

### Impact Explanation
An attacker can cause the wallet to report a deposit that is not spendable and include its declared amount in `input_sat`. This can make the accounting layer believe a payment is funded when it is not, or produce a signed transaction that Bitcoin consensus rejects.

If the fake output has a valid wallet-derived `script_pubkey` but a nonexistent `outpoint`, `multisig` succeeds and the transaction is signed, yet can never be broadcast successfully. If the outpoint exists but the encoded amount differs from the real UTXO amount, the Taproot prevout commitment is invalid. In both cases, funds shown as received cannot fund the intended spend.

This is a bounded loss-of-accounting-integrity and transaction-liveness issue rather than direct theft of an existing wallet UTXO.

### Likelihood Explanation
The attack is reachable wherever serialized `ReceivedOutput` values cross a trust boundary and are passed to `ReceivedOutput::read`. No validator key compromise or malicious signer is required; the attacker only supplies malformed accounting bytes.

Exploitation does require a caller to treat serialized `ReceivedOutput` data as authoritative rather than reconstructing it exclusively from `Scanner::scan_transaction` results. Since the public API explicitly exposes a deserializer and the resulting type is consumed directly by `SignableTransaction::new`, that trust assumption is not enforced by the code.

### Recommendation
Bind every field of `ReceivedOutput` to verified chain state.

- Prefer constructing `ReceivedOutput` only through `Scanner::scan_transaction`, which obtains `TxOut` and `OutPoint` from the same confirmed transaction. [5](#0-4) 
- If untrusted deserialization must be supported, verify `outpoint` against the UTXO set before use and require the retrieved UTXO to equal `output` exactly.
- Verify that `p2tr_script_buf(group_key + G * offset)` equals `output.script_pubkey` before accounting the output as spendable.
- Reject duplicate outpoints in `SignableTransaction::new`.
- Consider renaming or restricting `ReceivedOutput::read` so it cannot be mistaken for a trust-establishing parser.

### Proof of Concept
The following conceptual test demonstrates that deserialization and transaction construction accept an output whose reported value has no corresponding UTXO:

```rust
use bitcoin::{
  consensus::Encodable,
  Amount, OutPoint, TxOut, Txid,
  hashes::Hash,
};
use bitcoin_serai::wallet::{p2tr_script_buf, ReceivedOutput, SignableTransaction};
use frost::curve::Secp256k1;
use k256::{ProjectivePoint, Scalar};

let key = /* even threshold group key */;
let script = p2tr_script_buf(key).unwrap();

let fake = {
  let mut bytes = Vec::new();

  // offset = 0, matching `script`
  bytes.extend(Scalar::ZERO.to_bytes());

  // Claimed output: wallet script and arbitrarily large value.
  TxOut {
    value: Amount::from_sat(100_000_000),
    script_pubkey: script.clone(),
  }
  .consensus_encode(&mut bytes)
  .unwrap();

  // Nonexistent outpoint.
  OutPoint {
    txid: Txid::from_byte_array([0x41; 32]),
    vout: 0,
  }
  .consensus_encode(&mut bytes)
  .unwrap();

  ReceivedOutput::read(&mut bytes.as_slice()).unwrap()
};

assert_eq!(fake.value(), 100_000_000);

let signable = SignableTransaction::new(
  vec![fake],
  &[(script, 50_000)],
  None,
  None,
  10,
)
.unwrap();
```

`SignableTransaction` accepts the phantom 100 BTC-equivalent input because its balance calculation trusts the encoded `TxOut`. [2](#0-1)  For wallet keys matching `script`, `multisig` also accepts the offset/script relationship. [3](#0-2)  The resulting transaction can be signed but is unspendable because its input references no existing UTXO.

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
