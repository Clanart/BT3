### Title
Forged `ReceivedOutput` bytes are accepted as spendable deposits without chain or key validation - (File: networks/bitcoin/src/wallet/mod.rs)

### Summary
Medium: `ReceivedOutput::read` trusts attacker-supplied bytes for the offset, `TxOut`, and `OutPoint`, allowing an unprivileged party to fabricate a received Bitcoin output that reports value but does not exist or is not spendable.

### Finding Description
`ReceivedOutput::read` deserializes `offset`, `output`, and `outpoint` directly from the caller-controlled stream, with no proof that the outpoint exists on-chain, that the `TxOut` matches the referenced output, or that the script is bound to the expected wallet key and offset. [1](#0-0) 

By contrast, `Scanner::scan_transaction` constructs `ReceivedOutput` only after matching an observed transaction output script against the scanner’s registered scripts and calculating the real transaction outpoint. [2](#0-1) 

`SignableTransaction::new` then trusts the deserialized `output.value`, `offset`, and `outpoint` as transaction inputs and previous outputs. [3](#0-2)  `SignableTransaction::multisig` checks only that the supplied offset derives a key whose Taproot script equals the supplied `TxOut` script; it does not verify that the claimed outpoint exists or contains that `TxOut`. [4](#0-3) 

This permits a byte-level forgery analogous to the Curve front-end substitution: untrusted data is presented in the same representation as a genuine scanner result, but carries a fabricated deposit.

### Impact Explanation
An attacker can cause the wallet layer to report an arbitrary balance for a nonexistent or incorrectly represented UTXO. If the fabricated output uses a script derived from the wallet’s key and claimed offset, `SignableTransaction::multisig` accepts it and the threshold wallet signs a spend of a nonexistent or mismatched input. [5](#0-4) 

The resulting transaction is not spendable under Bitcoin consensus, but accounting or coordination code that treats `ReceivedOutput::read` as evidence of receipt can credit unavailable funds, allocate nonexistent liquidity, or generate doomed spends. This satisfies the “funds reported received that are not spendable” impact class.

### Likelihood Explanation
The attack requires only the ability to feed bytes to `ReceivedOutput::read`; no validator privileges, private-key material, malformed curve point, or compromised node is required. The attacker needs the expected wallet script, which is public, and can use offset zero or another known registered offset to satisfy the later key/script check.

The issue is not reachable through `Scanner::scan_transaction` itself because that path derives the object from an observed transaction. [6](#0-5)  It is reachable when serialized outputs cross an untrusted boundary such as RPC, storage restoration, relayed metadata, or another component that accepts externally supplied `ReceivedOutput` bytes.

### Recommendation
Do not expose `ReceivedOutput::read` as an authenticated representation of a spendable output. Either:

- restrict deserialization to trusted local storage and document that it performs no UTXO or ownership validation;
- require the expected `Scanner`/wallet key during deserialization and reject outputs whose `script_pubkey` does not equal `p2tr_script_buf(key + offset * G)`; and
- require callers to resolve the claimed `OutPoint` against a trusted Bitcoin view and compare the returned `TxOut` byte-for-byte before treating the object as spendable.

The strongest fix is to make deserialization produce an unverified claim type, then add a separate verification step that checks script binding and on-chain outpoint contents before conversion to `ReceivedOutput`.

### Proof of Concept
The wire format is simply `offset || consensus(TxOut) || consensus(OutPoint)`. For a wallet whose base Taproot script is `wallet_script`, the following constructs a forged claim for a nonexistent output:

```rust
use bitcoin::{Amount, OutPoint, TxOut, Txid, consensus::encode::serialize, hashes::Hash};
use bitcoin_serai::wallet::ReceivedOutput;
use k256::Scalar;

let wallet_script = /* the wallet's base or registered-offset P2TR ScriptBuf */;
let mut bytes = Scalar::ZERO.to_bytes().to_vec();

bytes.extend(serialize(&TxOut {
  value: Amount::from_sat(100_000),
  script_pubkey: wallet_script,
}));

bytes.extend(serialize(&OutPoint {
  txid: Txid::from_raw_hash(Hash::all_zeros()),
  vout: 0,
}));

let forged = ReceivedOutput::read(&mut bytes.as_slice()).unwrap();
assert_eq!(forged.value(), 100_000);
```

Because `SignableTransaction::new` uses `forged.outpoint` and `forged.output` directly, and `multisig` checks only the offset-derived script against `forged.output.script_pubkey`, a forged output naming the wallet script passes that local consistency check despite the referenced outpoint being nonexistent. [7](#0-6) [4](#0-3)

### Citations

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

**File:** networks/bitcoin/src/wallet/mod.rs (L199-210)
```rust
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

**File:** networks/bitcoin/src/wallet/send.rs (L275-282)
```rust
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
