I need to inspect `SignableTransaction::new`, fee/change/dust math, and how scanned outputs become scheduler inputs.### Title
Attacker-controlled `ReceivedOutput` offset makes a scanned output unspendable, permanently locking reported-received funds - (File: networks/bitcoin/src/wallet/mod.rs)

### Summary
The Cooler bug class: a counterparty engineers a state where the protocol must deliver value to them (or to a derived position), the delivery fails by construction, and the honest party's collateral is forfeited. In Serai's Bitcoin wallet, `ReceivedOutput` couples a `Scalar` offset (the secret needed to spend) with a `TxOut` (the on-chain funds). `ReceivedOutput::read` accepts these two fields completely independently — there is no check that `p2tr_script_buf(group_key + offset * G) == output.script_pubkey`, nor that `group_key + offset * G` is even-Y. Any consumer that deserializes attacker-influenced `ReceivedOutput`s (the listed `read` sink) can be handed an output that is reported as received yet can never be signed for: `SignableTransaction::multisig` detects the mismatch only by returning `None`, failing the whole transaction.

### Finding Description
`ReceivedOutput::read` deserializes `offset` via `Secp256k1::read_F` and `output`/`outpoint` via `consensus_decode`, then returns them as a tuple with no consistency check [1](#0-0) . The spend path later assumes the invariant that the offset produces the output's script: `multisig` computes `keys.clone().offset(self.offsets[i])` and returns `None` if `p2tr_script_buf(offset.group_key())` is `None` (odd-Y tweaked key) or does not equal `prevouts[i].script_pubkey` [2](#0-1) .

Two distinct attacker-reachable inconsistencies exist:

1. **Mismatched offset**: any scalar not equal to the offset whose tweaked key produced `output.script_pubkey` causes `multisig` to return `None`.
2. **Parity-incorrect offset**: `register_offset` exists precisely because only even-Y tweaked keys are usable — it increments the offset until `p2tr_script_buf` returns `Some` [3](#0-2) . `read` skips this normalization entirely, so a serialized offset resolving to an odd-Y key produces an output that can never be spent, and `register_offset`'s surjectivity means even offsets differing from the *registered* one can decode to a different (but wrong-for-this-script) tweak.

Notably, the honest generation path also has an asymmetry: `register_offset` may return `offset + n` (surjective, order-dependent [4](#0-3) ), so a caller that serializes a `ReceivedOutput` built with the *requested* offset rather than the *registered* one produces bytes that `read` accepts but `multisig` rejects — the same unspendable state reachable without any attacker at all.

### Impact Explanation
Funds reported received are not spendable. The scanner/consumer records a `ReceivedOutput` (a real on-chain `TxOut` locked to the multisig-derived script), but `SignableTransaction::multisig` silently fails for that input, aborting construction/signing of the entire transaction. A plan including the poisoned input fails its FROST attempt; if the tainted `ReceivedOutput` persists in the input set, every re-attempt fails identically, locking not just the attacker's output but every honest input bundled with it — the analog of the borrower permanently unable to repay while the lender keeps the collateral. The failure is `None`-typed (no blameable participant), so retry logic cannot remediate it.

### Likelihood Explanation
`ReceivedOutput::read` is an explicitly attacker-fed deserialization sink, and the honest path is itself fragile because of `register_offset`'s offset-incrementing behavior. No secret material, validator status, or collusion is required — only the ability to supply bytes to `read` or to trigger an offset/script mismatch. Severity Medium–High: permanent freezing of funds accepted as received, exploitable via public inputs.

### Recommendation
Bind the offset to the output at the trust boundary:

- In `ReceivedOutput::read` (or a new `verify(key)` / a fallible constructor used by `read`), check `p2tr_script_buf(group_key + GENERATOR * offset) == Some(output.script_pubkey)` and reject otherwise. Since `read` lacks the group key, require a `verify(&self, key: ProjectivePoint)` that all consumers call before queuing the output for spending.
- Alternatively store the script bound to the offset (as `Scanner` does internally via `scripts: HashMap<ScriptBuf, Scalar>`) and reject `ReceivedOutput`s whose `outpoint`/`script` pair was never produced by `scan_transaction`/`scan_block` for a known key.
- When `register_offset` increments an offset, ensure serialized `ReceivedOutput`s always carry the returned (normalized) offset, and consider having `read` re-run the parity normalization (`offset += 1` until `p2tr_script_buf` succeeds) so on-disk offsets are self-healing — though explicit verification is safer than silent correction.

### Proof of Concept
```rust
// networks/bitcoin/src/wallet — conceptual PoC
let key = even_key();                       // multisig group key
let mut scanner = Scanner::new(key).unwrap();
let real_offset = scanner.register_offset(Scalar::random(&mut OsRng)).unwrap();
let real_script = p2tr_script_buf(key + ProjectivePoint::GENERATOR * real_offset).unwrap();

// Attacker supplies a ReceivedOutput whose output is real but whose offset is wrong:
let forged = ReceivedOutput {
  offset: real_offset + Scalar::ONE,        // or any scalar, incl. one giving an odd-Y key
  output: TxOut { script_pubkey: real_script, value: Amount::from_sat(100_000) },
  outpoint: real_outpoint,
};
let bytes = forged.serialize();
let decoded = ReceivedOutput::read(&mut bytes.as_slice()).unwrap(); // accepted — no check

let tx = SignableTransaction::new(vec![decoded], &payments, change, None, fee).unwrap();
assert!(tx.multisig(&keys).is_none());      // returns None: funds recorded, never spendable
```
The same failure occurs with `offset` chosen so `key + offset*G` is odd-Y (`p2tr_script_buf` returns `None`), since `read` performs none of `register_offset`'s parity normalization.

### Citations

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

**File:** networks/bitcoin/src/wallet/mod.rs (L174-179)
```rust
  /// This means offsets are surjective, not bijective, and the order offsets are registered in
  /// may determine the validity of future offsets.
  ///
  /// The offsets registered must be securely generated. Arbitrary offsets may introduce a script
  /// path into the output, allowing the output to be spent by satisfaction of an arbitrary script
  /// (not by the signature of the key).
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

**File:** networks/bitcoin/src/wallet/send.rs (L273-285)
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
  }
```
