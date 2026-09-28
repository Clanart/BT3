### Title
Deserialized Bitcoin outputs can claim arbitrary ownership and value - (File: networks/bitcoin/src/wallet/mod.rs)

### Summary

`ReceivedOutput::read` accepts a scalar offset, `TxOut`, and `OutPoint` entirely from serialized input without proving that the encoded `TxOut` is the output referenced by the `OutPoint`. A consumer can therefore deserialize a “received” output that points to a nonexistent output, an output with a different value, or an output not actually controlled by the wallet. `SignableTransaction::multisig` only checks that the claimed `prevouts[i].script_pubkey` matches the offset key, not that the referenced on-chain output contains that script and amount. [1](#0-0) [2](#0-1) 

### Finding Description

`ReceivedOutput` stores three attacker-controlled claims: the spend offset, the alleged output contents, and the alleged outpoint. [3](#0-2)  Its deserializer reads each field independently and immediately returns the aggregate object. [4](#0-3)  `SignableTransaction::new` then copies the arbitrary outpoint into the spending `TxIn` and the arbitrary `TxOut` into `prevouts`. [5](#0-4) [6](#0-5) 

The later ownership check is limited to comparing the attacker-supplied script in `prevouts[i]` with the script derived from the wallet key plus attacker-supplied offset. [7](#0-6)  No lookup verifies that `previous_output` exists or that the chain output at that location has the same value and `script_pubkey`. [2](#0-1) 

### Impact Explanation

This is a “funds reported received that are not spendable” analog. Serialized input can create an apparent wallet output and carry it all the way into transaction construction and signing because the claimed script is self-consistent with the claimed offset. The resulting transaction nevertheless spends an absent or mismatched UTXO, so it cannot be validly confirmed as represented. If the outpoint is real but the serialized value is false, the generated Taproot signature commits to false prevout data and will not be valid for the actual UTXO. [8](#0-7) 

### Likelihood Explanation

The input is a byte string consumed by `ReceivedOutput::read`, which the threat model explicitly treats as untrusted. Constructing the object requires only a canonical scalar and ordinary Bitcoin consensus encodings; `Secp256k1::read_F` enforces scalar canonicity but does not authenticate semantic correctness. [9](#0-8)  The exploit does not require control of the wallet key or network peers, only the ability to supply a forged serialized `ReceivedOutput` to a component that trusts that representation.

### Recommendation

Treat `ReceivedOutput` serialization as an internal record and authenticate its origin, or resolve every `outpoint` against the blockchain and require the returned `TxOut` to equal the serialized `TxOut` before accepting it. At minimum, make `multisig`/`SignableTransaction` construction take independently fetched UTXO data rather than trusting the embedded `output` and `outpoint` pair. Components should not call `ReceivedOutput::read` for unsigned, attacker-supplied records without a chain-validity check. [4](#0-3) [2](#0-1) 

### Proof of Concept

```rust
use bitcoin::{
  consensus::encode::serialize,
  Amount, OutPoint, TxOut, Txid,
};
use frost::curve::Secp256k1;
use k256::Scalar;
use std::io::Write;

use bitcoin_serai::wallet::{p2tr_script_buf, ReceivedOutput};

fn forged_received_output(wallet_key: k256::ProjectivePoint) -> Vec<u8> {
  let mut bytes = Vec::new();

  // Canonical scalar offset 0: spending as the untweaked wallet key.
  bytes.extend_from_slice(&Scalar::ZERO.to_bytes());

  // Claim that the referenced output pays to our key and has a large value.
  let forged_txout = TxOut {
    value: Amount::from_sat(1_000_000),
    script_pubkey: p2tr_script_buf(wallet_key).unwrap(),
  };
  bytes.extend_from_slice(&serialize(&forged_txout));

  // Point at an outpoint we do not control, or one that does not exist.
  let forged_outpoint = OutPoint {
    txid: "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
      .parse::<Txid>()
      .unwrap(),
    vout: 0,
  };
  bytes.extend_from_slice(&serialize(&forged_outpoint));

  bytes
}

// Accepted without checking that the outpoint contains the encoded TxOut:
let forged = ReceivedOutput::read(&mut forged_received_output(key).as_slice()).unwrap();
```

Passing `forged` into `SignableTransaction::new` embeds its arbitrary `OutPoint` as an input and its arbitrary `TxOut` as the committed prevout. `multisig` then accepts the input whenever `forged_txout.script_pubkey` matches the key derived from the encoded offset, even though the referenced output may be absent, have a different amount, or belong to another script.

### Citations

**File:** networks/bitcoin/src/wallet/mod.rs (L88-97)
```rust
/// A spendable output.
#[derive(Clone, PartialEq, Eq, Debug)]
pub struct ReceivedOutput {
  // The scalar offset to obtain the key usable to spend this output.
  offset: Scalar,
  // The output to spend.
  output: TxOut,
  // The TX ID and vout of the output to spend.
  outpoint: OutPoint,
}
```

**File:** networks/bitcoin/src/wallet/mod.rs (L120-133)
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

**File:** networks/bitcoin/src/wallet/send.rs (L245-255)
```rust
    Ok(SignableTransaction {
      tx: Transaction {
        version: Version(2),
        lock_time: LockTime::ZERO,
        input: tx_ins,
        output: tx_outs,
      },
      offsets,
      prevouts: inputs.drain(..).map(|input| input.output).collect(),
      needed_fee,
    })
```

**File:** networks/bitcoin/src/wallet/send.rs (L273-283)
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

**File:** networks/bitcoin/src/wallet/send.rs (L373-386)
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
```

**File:** crypto/ciphersuite/src/lib.rs (L74-83)
```rust
  fn read_F<R: Read>(reader: &mut R) -> io::Result<Self::F> {
    let mut encoding = <Self::F as PrimeField>::Repr::default();
    reader.read_exact(encoding.as_mut())?;

    // ff mandates this is canonical
    let res = Option::<Self::F>::from(Self::F::from_repr(encoding))
      .ok_or_else(|| io::Error::other("non-canonical scalar"));
    encoding.as_mut().zeroize();
    res
  }
```
