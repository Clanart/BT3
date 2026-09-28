### Title
`ReceivedOutput::read` trusts a self-declared spend offset without binding it to the output's script, enabling unspendable funds to be reported as received - (File: networks/bitcoin/src/wallet/mod.rs)

### Summary
Analogous to the reported bug class — an overly permissive trust policy that accepts a broader principal than intended — `ReceivedOutput::read` deserializes an `(offset, output, outpoint)` triple and trusts the claimed `offset` without re-deriving that `p2tr_script_buf(scanner_key + offset * G) == output.script_pubkey`. Any bytes fed to `ReceivedOutput::read` can therefore assert an arbitrary key-derivation offset for an arbitrary output, producing a `ReceivedOutput` the wallet layer treats as spendable under `keys.offset(offset)` when it is not.

### Finding Description
`ReceivedOutput` couples three facts: the `Scalar` offset needed to spend, the `TxOut`, and the `OutPoint` [1](#0-0) . The legitimate producer, `Scanner::scan_transaction`, only ever emits an `offset` looked up from `self.scripts`, i.e. an offset already proven to satisfy `output.script_pubkey == p2tr(key + offset·G)` [2](#0-1) . That implicit invariant — offset ↔ script correspondence — is never enforced on the deserialization path: `read` accepts any `offset` from `Secp256k1::read_F`, any `TxOut`, and any `OutPoint`, and constructs the struct directly [3](#0-2) . Because the fields are private, `read` is the sole boundary where forged bytes can create an inconsistent `ReceivedOutput`.

### Impact Explanation
Downstream spending logic consumes `output.offset()` to derive `keys.offset(offset)` and produce a Schnorr signature for `key + offset·G`. If the offset does not match the output's script_pubkey, the resulting signature cannot satisfy the Taproot key-path spend: the output is reported received yet is not spendable. A crafted `ReceivedOutput` can also misattribute an outpoint, causing Serai to attempt to spend a non-existent or foreign UTXO. This satisfies the acceptance criterion "funds reported received that are not spendable."

### Likelihood Explanation
Exploitation requires untrusted bytes reaching `ReceivedOutput::read` — e.g., a serialized output transported or persisted outside the trusted scanner path (peer-provided data, coordinator-supplied payload). Within the pure local `scan_block`/`scan_transaction` flow the invariant holds, so likelihood is moderate; the flaw is a missing integrity check at a public deserialization boundary explicitly exposed for untrusted input.

### Recommendation
Either (a) remove `read`/`write` round-tripping of the unverifiable triple and require re-derivation via `Scanner`, or (b) extend `ReceivedOutput`/`read` to carry the base key and verify `p2tr_script_buf(key + G·offset) == output.script_pubkey` before constructing the value, rejecting mismatches with `io::Error`.

### Proof of Concept
```rust
// networks/bitcoin wallet PoC sketch
let key = /* even-Y ProjectivePoint the scanner watches */;
let mut buf = vec![];
// offset that does NOT correspond to the output's script_pubkey
buf.extend(Scalar::ONE.to_bytes());
// a TxOut paying to p2tr(key + 0*G) — i.e. the base address
let txout = TxOut { value: Amount::from_sat(100_000),
                    script_pubkey: p2tr_script_buf(key).unwrap() };
buf.extend(serialize(&txout));
buf.extend(serialize(&OutPoint::new(Txid::all_zeros(), 0)));

let forged = ReceivedOutput::read::<&[u8]>(&mut buf.as_slice()).unwrap();
assert_eq!(forged.offset(), Scalar::ONE);   // accepted
// Spending `forged` signs under key + 1*G, but the output requires key + 0*G:
// the signature can never satisfy the script_pubkey — funds are unspendable.
```

Caveat: I verified that `read` performs no offset↔script consistency check and that `scan_transaction` is the only in-repo producer that enforces the invariant. I could not fully trace every in-repo consumer of `ReceivedOutput::read` to confirm a live untrusted-bytes path in the processor, so the likelihood rests on `read` being a public API explicitly intended for untrusted input per the audit scope.

### Citations

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

**File:** networks/bitcoin/src/wallet/mod.rs (L199-213)
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
```
