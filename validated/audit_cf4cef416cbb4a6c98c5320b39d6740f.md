### Title
Untrusted `ReceivedOutput` bytes can claim a non-existent spendable Bitcoin UTXO - (File: `networks/bitcoin/src/wallet/mod.rs`)

### Summary
`ReceivedOutput::read` accepts an arbitrary scalar offset, `TxOut`, and `OutPoint` without proving that the referenced transaction output exists or that the `TxOut` belongs to that outpoint. `SignableTransaction` then treats the encoded value as spendable and causes the threshold signer to commit to that unverified UTXO metadata through `Prevouts::All`. An unprivileged party can therefore provide bytes describing a large, non-existent output paying to the group key, causing funds to be reported or consumed as received even though they cannot be spent.

### Finding Description
`ReceivedOutput` is documented as “a spendable output,” but its fields are independently decoded: a scalar from `Secp256k1::read_F`, a `TxOut`, and an `OutPoint`. [1](#0-0) 

The decoded object exposes the attacker-controlled `TxOut` amount through `ReceivedOutput::value`. [2](#0-1) 

`SignableTransaction::new` adds the attacker-controlled `output.value` to the input total and uses the attacker-controlled `outpoint` as the transaction input without checking UTXO existence, spendability, or maturity. [3](#0-2) 

The only consistency check before creating the FROST machines is that `keys.offset(offset).group_key()` produces the supplied `script_pubkey`; this does not validate the `outpoint` or that the supplied `TxOut` was actually recorded under it. [4](#0-3) 

During signing, the untrusted `prevouts` vector is committed via `Prevouts::All`, so the forged amount and script become part of every Taproot signature hash. [5](#0-4) 

### Impact Explanation
An attacker can encode an output nominally paying the group but referencing a fake, spent, mismatched, or otherwise unspendable outpoint. [6](#0-5) 

Any accounting path that accepts these bytes as a “spendable output” will report value that was never received or cannot be spent. [7](#0-6) 

Any signing path that consumes the object can produce threshold signatures for a transaction Bitcoin will reject, while still binding all signers to the forged `OutPoint` and `TxOut` metadata. [3](#0-2) [8](#0-7) 

### Likelihood Explanation
The attacker needs only public information: the group key or an offset-derived key and ordinary Bitcoin consensus encodings. [9](#0-8) 

Using `offset = 0` and `script_pubkey = p2tr_script_buf(group_key)` satisfies the only key consistency check in `multisig` for an ordinary even Taproot group key. [4](#0-3) 

No signature, blockchain proof, UTXO proof, or ownership evidence is requested by `ReceivedOutput::read`. [6](#0-5) 

### Recommendation
Do not expose `ReceivedOutput::read` as an unauthenticated constructor for spendable funds. [6](#0-5) 

Deserialize into an explicitly untrusted claim type, then require confirmation from a trusted scan or node lookup that reconstructs the `ReceivedOutput` from chain data. [10](#0-9) 

Before `SignableTransaction::new` or `multisig` consumes an input, verify the outpoint exists, is unspent, is mature, and has exactly the supplied `TxOut` value and script. [11](#0-10) [4](#0-3) 

### Proof of Concept
```rust
use bitcoin::{
  absolute::LockTime,
  consensus::encode::serialize,
  transaction::{OutPoint, TxOut},
  Amount, ScriptBuf, Txid,
};
use frost::curve::{Ciphersuite, Secp256k1};
use bitcoin_serai::wallet::{p2tr_script_buf, ReceivedOutput, SignableTransaction};
use k256::{ProjectivePoint, Scalar};

// Public, group-known Taproot key.
let group_key: ProjectivePoint = /* threshold group key */;

// Attacker-controlled bytes consumed by ReceivedOutput::read.
let mut forged = Vec::new();
forged.extend(Scalar::ZERO.to_repr());                    // offset = 0
forged.extend(serialize(&TxOut {
  value: Amount::from_sat(100_000_000),                  // claimed funds
  script_pubkey: p2tr_script_buf(group_key).unwrap(),    // valid group script
}));
forged.extend(serialize(&OutPoint {
  txid: Txid::all_zeros(),                               // non-existent input
  vout: 0,
}));

let claimed = ReceivedOutput::read(&mut forged.as_slice()).unwrap();
assert_eq!(claimed.value(), 100_000_000);

// Accepted as a spendable input and included in the signed prevout commitment.
let signable = SignableTransaction::new(
  vec![claimed],
  &[(ScriptBuf::new_op_return(&[]), 546)],
  None,
  None,
  1,
).unwrap();

// For valid ThresholdKeys, multisig checks only offset/script consistency.
// It signs Prevouts::All containing the fake amount and fake outpoint.
```

The signed transaction commits to the forged prevout but is invalid on Bitcoin because the outpoint does not reference the claimed `TxOut`. [8](#0-7)

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

**File:** networks/bitcoin/src/wallet/mod.rs (L88-133)
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

**File:** networks/bitcoin/src/wallet/send.rs (L150-185)
```rust
  pub fn new(
    mut inputs: Vec<ReceivedOutput>,
    payments: &[(ScriptBuf, u64)],
    change: Option<ScriptBuf>,
    data: Option<Vec<u8>>,
    fee_per_vbyte: u64,
  ) -> Result<SignableTransaction, TransactionError> {
    if inputs.is_empty() {
      Err(TransactionError::NoInputs)?;
    }

    if payments.is_empty() && change.is_none() && data.is_none() {
      Err(TransactionError::NoOutputs)?;
    }

    for (_, amount) in payments {
      if *amount < DUST {
        Err(TransactionError::DustPayment)?;
      }
    }

    if data.as_ref().map_or(0, Vec::len) > 80 {
      Err(TransactionError::TooMuchData)?;
    }

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

**File:** networks/bitcoin/src/wallet/send.rs (L273-281)
```rust
  pub fn multisig(self, keys: &ThresholdKeys<Secp256k1>) -> Option<TransactionMachine> {
    let mut sigs = vec![];
    for i in 0 .. self.tx.input.len() {
      let offset = keys.clone().offset(self.offsets[i]);
      if p2tr_script_buf(offset.group_key())? != self.prevouts[i].script_pubkey {
        None?;
      }

      sigs.push(AlgorithmMachine::new(Schnorr::new(), keys.clone().offset(self.offsets[i])));
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
