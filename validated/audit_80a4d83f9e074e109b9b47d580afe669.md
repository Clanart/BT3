### Title
Unauthenticated serialized outputs let attackers fabricate unspendable received funds - ([File: networks/bitcoin/src/wallet/mod.rs])

### Summary
`ReceivedOutput::read` deserializes an arbitrary scalar offset, `TxOut`, and `OutPoint` without proving that the outpoint exists or that the object came from blockchain scanning. [1](#0-0)  A forged record using the multisig’s known Taproot `script_pubkey` and a nonexistent outpoint is subsequently accepted by `SignableTransaction` and can reach the threshold-signing path. [2](#0-1) [3](#0-2) 

### Finding Description
`ReceivedOutput` is the wallet’s representation of an output spendable by a threshold key, but its public deserializer trusts all three security-relevant fields supplied by the byte stream: `offset`, `output`, and `outpoint`. [4](#0-3) [1](#0-0) 

When the resulting object is passed to `SignableTransaction::new`, the implementation trusts its declared value and outpoint when constructing transaction inputs. [2](#0-1)  `SignableTransaction::multisig` checks only that `offset` transforms the threshold key into the declared `script_pubkey`; it does not check UTXO existence, confirmed status, coinbase maturity, duplicate inputs, or consistency with a scanner-derived output. [5](#0-4) 

The forged `TxOut` is then committed as a Taproot previous output through `Prevouts::All`, causing each signer to authorize a transaction whose input cannot resolve on the Bitcoin blockchain. [6](#0-5) 

### Impact Explanation
An attacker can cause the wallet to report or process funds that are not spendable and induce threshold participants to produce signatures for a transaction that Bitcoin consensus will always reject because its previous outpoint is nonexistent or already spent. [7](#0-6)  This can consume a signing attempt and persist a false spendable output in systems that treat deserialized `ReceivedOutput`s as authenticated scan results. [1](#0-0) 

### Likelihood Explanation
The multisig’s Taproot address is public, so an attacker can construct a syntactically valid `TxOut` paying it while pairing that output with any arbitrary `OutPoint`. [8](#0-7)  No signature, chain proof, RPC lookup, or scanner provenance is required by `ReceivedOutput::read`. [1](#0-0) 

### Recommendation
Separate untrusted serialized representations from scanner-authenticated outputs, such as an `UnverifiedReceivedOutput`, and require UTXO resolution before constructing `SignableTransaction`. [9](#0-8)  Before signing, verify each outpoint on the selected chain, compare the returned amount and `script_pubkey`, reject duplicate outpoints, enforce confirmation and coinbase-maturity requirements, and fail if the previous transaction cannot be resolved. [6](#0-5) 

### Proof of Concept
The following attacker-controlled bytes deserialize successfully and pass the wallet’s key-to-script check, despite referring to a nonexistent outpoint:

```rust
use bitcoin::{Amount, OutPoint, Transaction, TxOut, Txid};
use bitcoin::consensus::serialize;
use frost::curve::Secp256k1;
use k256::Scalar;

// `group_key` is the public, even-Y threshold Taproot key.
let wallet_script = p2tr_script_buf(group_key).unwrap();

let forged_txout = TxOut {
  value: Amount::from_sat(100_000),
  script_pubkey: wallet_script,
};

// Any nonexistent or already-spent outpoint suffices.
let fake_outpoint = OutPoint {
  txid: Txid::all_zeros(),
  vout: 0,
};

let mut encoded = Vec::new();
encoded.extend(Scalar::ZERO.to_bytes());
encoded.extend(serialize(&forged_txout));
encoded.extend(serialize(&fake_outpoint));

// This creates a semantically forged output purely from untrusted bytes.
let forged = ReceivedOutput::read(&mut encoded.as_slice()).unwrap();

let tx = SignableTransaction::new(
  vec![forged],
  &[(payment_script, 10_000)],
  None,
  None,
  1,
).unwrap();

// The offset/script check passes, and the transaction reaches threshold signing.
let machine = tx.multisig(&threshold_keys).unwrap();
```

`SignableTransaction::multisig` returns a machine because the forged `TxOut` uses the wallet’s script, while `TransactionSignMachine::sign` commits the fabricated previous output into the sighash with `Prevouts::All`. [3](#0-2) [6](#0-5)

### Citations

**File:** networks/bitcoin/src/wallet/mod.rs (L77-86)
```rust
/// Return the Taproot address payload for a public key.
///
/// If the key is odd, this will return None.
pub fn p2tr_script_buf(key: ProjectivePoint) -> Option<ScriptBuf> {
  if key.to_encoded_point(true).tag() != Tag::CompressedEvenY {
    return None;
  }

  Some(ScriptBuf::new_p2tr_tweaked(TweakedPublicKey::dangerous_assume_tweaked(x_only(&key))))
}
```

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

**File:** networks/bitcoin/src/wallet/send.rs (L150-156)
```rust
  pub fn new(
    mut inputs: Vec<ReceivedOutput>,
    payments: &[(ScriptBuf, u64)],
    change: Option<ScriptBuf>,
    data: Option<Vec<u8>>,
    fee_per_vbyte: u64,
  ) -> Result<SignableTransaction, TransactionError> {
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

**File:** networks/bitcoin/src/wallet/send.rs (L373-427)
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
}

pub struct TransactionSignatureMachine {
  tx: Transaction,
  sigs: Vec<AlgorithmSignatureMachine<Secp256k1, Schnorr>>,
}

impl SignatureMachine<Transaction> for TransactionSignatureMachine {
  type SignatureShare = Vec<SignatureShare<Secp256k1>>;

  fn read_share<R: Read>(&self, reader: &mut R) -> io::Result<Self::SignatureShare> {
    self.sigs.iter().map(|sig| sig.read_share(reader)).collect()
  }

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
```
