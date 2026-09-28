### Title
Unauthenticated `ReceivedOutput` deserialization permits fake UTXO injection - (File: networks/bitcoin/src/wallet/mod.rs)

### Summary
`ReceivedOutput::read` accepts an attacker-supplied scalar offset, `TxOut`, and `OutPoint` without verifying that the outpoint exists or that the represented output was actually observed on-chain. `SignableTransaction` subsequently trusts the encoded value and outpoint as spendable funds, while `multisig` checks only that the output script matches the key derived with the supplied offset. This can report nonexistent funds as received and produce a signed transaction spending an unspendable outpoint. [1](#0-0) [2](#0-1) [3](#0-2) 

### Finding Description
`ReceivedOutput::read` deserializes the output’s spending offset, value/script, and claimed outpoint as three independent, unauthenticated fields. [1](#0-0)  There is no transaction proof, outpoint lookup, or authentication tag tying the claimed `OutPoint` to a confirmed transaction containing the encoded `TxOut`. [1](#0-0) 

`SignableTransaction::new` treats `input.output.value` as available input value and uses `input.outpoint` directly as the transaction input reference. [2](#0-1)  The later `multisig` check validates only that `prevouts[i].script_pubkey` equals the P2TR script for `keys.offset(offset)`, so a fake output using a known wallet script and its corresponding offset passes this check. [4](#0-3) 

### Impact Explanation
An attacker who can supply serialized `ReceivedOutput` bytes can make the wallet account for arbitrary value at a nonexistent outpoint. [5](#0-4)  Because the output script can be copied from or generated for the wallet’s public P2TR key, the fake entry can pass the transaction machine’s key/script consistency check. [4](#0-3)  The resulting transaction will be signed but cannot be confirmed because its referenced outpoint does not contain the claimed output. [6](#0-5) 

### Likelihood Explanation
This requires the attacker to reach a call site that deserializes untrusted `ReceivedOutput` bytes, but no private keys, validator authority, malformed encodings, or blockchain reorganization are needed. [1](#0-0)  The attacker only needs a P2TR script accepted for the target key and an offset producing that script, such as offset zero when scanning the untweaked registered key. [7](#0-6) [8](#0-7) 

### Recommendation
Treat `ReceivedOutput` as authenticated wallet state rather than a self-validating public proof. Serialized outputs should be authenticated, or deserialization should be followed by a chain lookup confirming that the claimed outpoint exists, has the encoded value/script, and is mature before exposing it as spendable. [1](#0-0) [9](#0-8) 

### Proof of Concept
```rust
use bitcoin::{Amount, OutPoint, Txid, hashes::Hash};
use frost::curve::Secp256k1;
use ciphersuite::Ciphersuite;
use bitcoin_serai::wallet::{p2tr_script_buf, ReceivedOutput, SignableTransaction};

// `wallet_key` is the public key registered with Scanner.
let script = p2tr_script_buf(wallet_key).unwrap();
let fake_output = bitcoin::TxOut {
  value: Amount::from_sat(100_000),
  script_pubkey: script,
};
let fake_outpoint = OutPoint {
  txid: Txid::from_byte_array([0x41; 32]),
  vout: 0,
};

let mut bytes = Vec::new();
bytes.extend(Scalar::ZERO.to_bytes()); // Offset matching `wallet_key`.
bytes.extend(bitcoin::consensus::serialize(&fake_output));
bytes.extend(bitcoin::consensus::serialize(&fake_outpoint));

let received = ReceivedOutput::read(&mut bytes.as_slice()).unwrap();
let signable = SignableTransaction::new(
  vec![received],
  &[(payment_script, 10_000)],
  None,
  None,
  10,
).unwrap();

// This succeeds because only the fake output's script_pubkey is checked.
let machine = signable.multisig(&keys).unwrap();
```

`ReceivedOutput::read` accepts the forged tuple, `SignableTransaction::new` trusts its value and outpoint, and `multisig` accepts the output because the script matches the offset-derived key despite the outpoint being nonexistent. [1](#0-0) [2](#0-1) [3](#0-2)

### Citations

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

**File:** networks/bitcoin/src/wallet/mod.rs (L163-165)
```rust
    let mut scripts = HashMap::new();
    scripts.insert(p2tr_script_buf(key)?, Scalar::ZERO);
    Some(Scanner { key, scripts })
```

**File:** networks/bitcoin/src/wallet/mod.rs (L180-194)
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
        }
        None => offset += Scalar::ONE,
      }
```

**File:** networks/bitcoin/src/wallet/mod.rs (L216-226)
```rust
  /// Scan a block.
  ///
  /// This will also scan the coinbase transaction which is bound by maturity. If received outputs
  /// must be immediately spendable, a post-processing pass is needed to remove those outputs.
  /// Alternatively, scan_transaction can be called on `block.txdata[1 ..]`.
  pub fn scan_block(&self, block: &Block) -> Vec<ReceivedOutput> {
    let mut res = Vec::new();
    for tx in &block.txdata {
      res.extend(self.scan_transaction(tx));
    }
    res
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

**File:** networks/bitcoin/src/wallet/send.rs (L274-281)
```rust
    let mut sigs = vec![];
    for i in 0 .. self.tx.input.len() {
      let offset = keys.clone().offset(self.offsets[i]);
      if p2tr_script_buf(offset.group_key())? != self.prevouts[i].script_pubkey {
        None?;
      }

      sigs.push(AlgorithmMachine::new(Schnorr::new(), keys.clone().offset(self.offsets[i])));
```

**File:** networks/bitcoin/src/wallet/send.rs (L417-425)
```rust
    for (input, schnorr) in self.tx.input.iter_mut().zip(self.sigs.drain(..)) {
      let sig = schnorr.complete(
        shares.iter_mut().map(|(l, shares)| (*l, shares.remove(0))).collect::<HashMap<_, _>>(),
      )?;

      let mut witness = Witness::new();
      witness.push(sig);
      input.witness = witness;
    }
```
