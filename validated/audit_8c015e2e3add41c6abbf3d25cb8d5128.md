### Title
Unauthenticated `ReceivedOutput` deserialization permits forging spendable inputs and requesting unauthorized threshold signatures - ([File: networks/bitcoin/src/wallet/mod.rs])

### Summary
`ReceivedOutput::read` accepts an offset, `TxOut`, and `OutPoint` from untrusted bytes without authenticating that a `Scanner` produced the object or that the referenced output exists on-chain [1](#0-0) . `SignableTransaction::new` trusts those fields as spend inputs [2](#0-1) , and `multisig` only checks that `script_pubkey` equals the key derived from `keys.offset(offset)` [3](#0-2) . A public caller who knows a multisig Taproot script can therefore deserialize a fabricated `ReceivedOutput`, construct a withdrawal transaction paying themselves, and have each input signed with a Taproot sighash committing to the forged `OutPoint` and supplied `TxOut` [4](#0-3) .

### Finding Description
`ReceivedOutput` carries the authorization-sensitive claim that a specific UTXO is controlled by the threshold key under a specific offset [5](#0-4) . Its deserializer nevertheless treats all three fields as self-asserted data: it reads a scalar, decodes an arbitrary `TxOut`, and decodes an arbitrary `OutPoint`, then returns the object without a MAC, scanner digest, block proof, or UTXO existence check [1](#0-0) .

The spend path preserves that trust boundary failure. `SignableTransaction::new` copies the attacker-controlled value into the input total, the offset into the signing offsets, and the attacker-controlled `outpoint` into the transaction input [2](#0-1) . The only consistency check performed before constructing the per-input FROST machines is whether the supplied script equals `p2tr_script_buf(keys.offset(offset).group_key())`; an attacker can satisfy this publicly for the base offset or any known registered offset [3](#0-2) . Signing then computes `taproot_key_spend_signature_hash` over the attacker-influenced transaction and `Prevouts::All` [6](#0-5) .

### Impact Explanation
If an exposed ingestion path accepts serialized `ReceivedOutput` values and forwards the resulting `SignableTransaction` to threshold signing, an unprivileged party can obtain signatures for inputs and destinations the scanner never reported. For a genuine unspent output paying the multisig, the forged `ReceivedOutput` can reference that real `OutPoint`, satisfy the script check with its public offset, and authorize a valid transfer to attacker-chosen outputs. For a nonexistent or spent `OutPoint`, the code still produces a threshold signature over an unintended message, even though Bitcoin consensus will reject the transaction.

### Likelihood Explanation
Exploitation requires an integrator-facing path where untrusted bytes reach `ReceivedOutput::read` and the resulting object is used to initiate `SignableTransaction::new`/`multisig`. The required script and offset are public: the base output uses `Scalar::ZERO`, while registered offsets are inferable from the corresponding public Taproot script [7](#0-6) . No private key, validator compromise, malformed curve point, or malicious peer is needed because the deserializer itself accepts the forged claim.

### Recommendation
Do not let `ReceivedOutput::read` create a signing-capable object from unauthenticated bytes. Require scanned outputs to carry authenticated provenance, such as a keyed digest over `offset || TxOut || OutPoint` plus chain context, or reconstruct `ReceivedOutput` only through `Scanner::scan_transaction`/`scan_block` after validating the transaction and block. Before signing, independently verify the `OutPoint` exists, is unspent, has the expected value and script, and is not an immature coinbase output. Treat deserialized outputs as requests for lookup, not authoritative spend capabilities.

### Proof of Concept
```rust
use bitcoin::{
  consensus::encode::serialize,
  Amount, OutPoint, ScriptBuf, TxOut, Txid,
};
use frost::{curve::{Ciphersuite, Secp256k1}, Participant, ThresholdKeys};
use bitcoin_serai::wallet::{
  p2tr_script_buf, ReceivedOutput, SignableTransaction,
};
use std::collections::HashMap;
use zeroize::Zeroizing;

fn forged_input_bytes(multisig_key: k256::ProjectivePoint) -> Vec<u8> {
  // Any known multisig-controlled P2TR script works; offset zero is publicly usable.
  let script: ScriptBuf = p2tr_script_buf(multisig_key).unwrap();

  // For a live exploit, set this to an unspent UTXO paying `script`.
  // For the signing-forgery demonstration, even a fabricated OutPoint is accepted.
  let txout = TxOut {
    value: Amount::from_sat(100_000),
    script_pubkey: script,
  };
  let outpoint = OutPoint {
    txid: Txid::from_raw_hash(bitcoin::hashes::Hash::all_zeros()),
    vout: 0,
  };

  let mut bytes = Secp256k1::F::ZERO.to_repr().as_ref().to_vec();
  bytes.extend(serialize(&txout));
  bytes.extend(serialize(&outpoint));
  bytes
}

fn forge_sign_request(
  keys: &ThresholdKeys<Secp256k1>,
  attacker_script: ScriptBuf,
) {
  let group_key = keys.group_key();
  let serialized = forged_input_bytes(group_key);

  // No authentication distinguishes this forged object from Scanner output.
  let forged = ReceivedOutput::read(&mut serialized.as_slice()).unwrap();

  let spend = SignableTransaction::new(
    vec![forged],
    &[(attacker_script, 90_000)],
    None,
    None,
    10,
  )
  .unwrap();

  // This only checks `p2tr(base + offset) == supplied script_pubkey`, not
  // whether the supplied outpoint came from authenticated scanning.
  let machine = spend.multisig(keys).unwrap();
}
```
The resulting `machine` enters normal FROST preprocessing, and `TransactionSignMachine::sign` generates shares for sighashes committing to the forged input and attacker-selected transaction outputs [4](#0-3) .

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

**File:** networks/bitcoin/src/wallet/mod.rs (L151-165)
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
