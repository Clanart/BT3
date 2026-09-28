### Title
`ReceivedOutput::read` binds attacker-controlled bytes into (offset, output, outpoint) without any consistency check — a crafted `ReceivedOutput` claims an arbitrary scalar offset for an arbitrary outpoint - (File: networks/bitcoin/src/wallet/mod.rs)

### Summary
CVE-2018-1273 is an improper-neutralization flaw: attacker-supplied parameters are bound into object properties, letting crafted input dictate how a bound object is interpreted downstream. The reachable analog in Serai is `ReceivedOutput::read` in `networks/bitcoin/src/wallet/mod.rs`: it deserializes a scalar `offset`, a `TxOut`, and an `OutPoint` directly from untrusted bytes and constructs a `ReceivedOutput` that downstream signing code treats as "an output our key can spend," without ever verifying that `offset * G + key` actually produces `output.script_pubkey` or that `outpoint` corresponds to `output`.

### Finding Description
`Scanner` is the legitimate producer of `ReceivedOutput`s: `Scanner::register_offset` maps script_pubkeys to offsets ( [1](#0-0) ) and `Scanner::scan_transaction` only emits a `ReceivedOutput` when `output.script_pubkey` is found in the registered `scripts` map, binding `offset`/`output`/`outpoint` consistently ( [2](#0-1) ). `ReceivedOutput::read`, however, is a raw "bind whatever the bytes say" path:

- `let offset = Secp256k1::read_F(r)?;` — any canonical scalar is accepted.
- `TxOut::consensus_decode` / `OutPoint::consensus_decode` — any value/script/outpoint is accepted.
- The struct is then returned with no re-derivation of `p2tr_script_buf(key + offset*G)` and no check that `outpoint`'s txid/vout resolves to `output` ( [3](#0-2) ).

The signer path uses `offset()` to re-key (`tweak_keys`/`scale`-style offsets, [4](#0-3) ) and uses `output()`/`outpoint()`/`value()` to build prevouts and fee/change math in `send.rs`. A `ReceivedOutput` deserialized from attacker bytes can therefore inject: a fabricated outpoint/value pair (funds reported received that don't exist), an output whose script doesn't match the claimed offset (funds reported received that are not spendable by this key), or inconsistent prevout data fed into the sighash commitment.

### Impact Explanation
An unprivileged party who can feed serialized bytes to `ReceivedOutput::read` (e.g., any transport or storage path where `ReceivedOutput`s are round-tripped rather than produced locally by `Scanner`) obtains the exact analog of the Spring binder flaw: crafted parameters bound into a trusted object. Consequences matching the accepted impact classes:

- Funds reported received that are not spendable: an `offset`/`script_pubkey` mismatch yields an output the threshold key cannot sign for, or a fabricated `outpoint` referencing a nonexistent UTXO.
- Signing an unintended message: a mismatched `outpoint`/`output` pair is committed into the sighash, producing a signature over a transaction that cannot be validly broadcast or that burns the wrong amount to fees.

### Likelihood Explanation
The flaw is deterministic — any byte string of the right shape parses. Exploitability depends on whether an attacker can reach the `read` path; the struct's public `serialize`/`read` pair indicates these objects are intended to cross a trust boundary (processor/coordinator messages and storage), so attacker influence over stored or relayed outputs is a realistic precondition. Rated Medium: the injection is trivial, but it requires a caller that consumes `ReceivedOutput`s from an untrusted source rather than locally scanning.

### Recommendation
Make `ReceivedOutput` construction privileged: either remove `read` from untrusted-input reachability, or have `read` take the `Scanner`/`key` and re-derive `p2tr_script_buf(key + offset*G)`, rejecting inputs where it doesn't equal `output.script_pubkey` — mirroring how `Scanner::scan_transaction` establishes the binding. At minimum, document `read` as requiring trusted input and re-scan the chain to confirm `outpoint → output`.

### Proof of Concept
```
// Deserialize a forged ReceivedOutput claiming an arbitrary offset for an
// arbitrary outpoint/value. read() performs no binding check.
let mut bytes = Scalar::ONE.to_bytes().to_vec();          // offset = 1
bytes.extend(serialize(&TxOut {
  value: Amount::from_sat(1_000_000_000),                 // claimed 10 BTC
  script_pubkey: victim_script,                           // or any script
}));
bytes.extend(serialize(&OutPoint::new(Txid::all_zeros(), 0))); // nonexistent UTXO
let forged = ReceivedOutput::read(&mut bytes.as_slice()).unwrap();
// forged.offset()=1, forged.output()=10BTC@victim_script, forged.outpoint()=fake
// — accepted even though p2tr_script_buf(key + 1*G) != victim_script and the
// outpoint never existed. Downstream signing would commit to this prevout.
```
The root cause is the same improper neutralization as CVE-2018-1273: raw input fields are bound into a security-relevant object (`offset`, `output`, `outpoint`) without the consistency invariant the legitimate producer (`Scanner`) enforces.

### Citations

**File:** networks/bitcoin/src/wallet/mod.rs (L46-75)
```rust
pub fn tweak_keys(keys: ThresholdKeys<Secp256k1>) -> ThresholdKeys<Secp256k1> {
  // Adds the unspendable script path per
  // https://github.com/bitcoin/bips/blob/master/bip-0341.mediawiki#cite_note-23
  let keys = {
    use k256::elliptic_curve::{
      bigint::{Encoding, U256},
      ops::Reduce,
      group::GroupEncoding,
    };
    let tweak_hash = TapTweakHash::hash(&keys.group_key().to_bytes().as_slice()[1 ..]);
    /*
      https://github.com/bitcoin/bips/blob/master/bip-0340.mediawiki#cite_ref-13-0 states how the
      bias is negligible. This reduction shouldn't ever occur, yet if it did, the script path
      would be unusable due to a check the script path hash is less than the order. That doesn't
      impact us as we don't want the script path to be usable.
    */
    keys.offset(<Secp256k1 as Ciphersuite>::F::reduce(U256::from_be_bytes(
      *tweak_hash.to_raw_hash().as_ref(),
    )))
  };

  let needs_negation = needs_negation(&keys.group_key());
  keys
    .scale(<_ as subtle::ConditionallySelectable>::conditional_select(
      &Scalar::ONE,
      &-Scalar::ONE,
      needs_negation,
    ))
    .expect("scaling keys by 1 or -1 yet interpreted as 0?")
}
```

**File:** networks/bitcoin/src/wallet/mod.rs (L122-134)
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
  }
```

**File:** networks/bitcoin/src/wallet/mod.rs (L180-196)
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
    }
  }
```

**File:** networks/bitcoin/src/wallet/mod.rs (L199-214)
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
      }
    }
    res
  }
```
