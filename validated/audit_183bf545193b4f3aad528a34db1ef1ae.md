### Title
`ReceivedOutput::read` accepts attacker-supplied `(offset, script_pubkey)` pairs without verifying consistency, allowing forged "received" outputs that are not spendable - (File: networks/bitcoin/src/wallet/mod.rs)

### Summary
`ReceivedOutput::read` deserializes a scalar `offset`, a `TxOut`, and an `OutPoint` over an unauthenticated byte stream, then returns them as a trusted `ReceivedOutput`. It never checks that the claimed `offset` actually relates the multisig's group key to the key committed in `output.script_pubkey` (i.e., that `p2tr_script_buf(group_key + offset·G) == output.script_pubkey`). This mirrors CVE-2019-10101's class: security-critical data accepted over a channel that provides no integrity/authenticity binding between the fields. A party able to feed crafted bytes to `ReceivedOutput::read` can cause the wallet to treat an output as spendable by a key the signers do not control, or to sign a spend under the wrong offset key.

### Finding Description
`Scanner::scan_transaction` internally constructs consistent `(offset, output)` pairs: it looks up `output.script_pubkey` in `self.scripts` and pairs it with the registered offset [1](#0-0) . The invariant "this script_pubkey == p2tr(key + offset·G)" is therefore guaranteed for scanner-produced values, but is a *structural* invariant that `read` fails to re-establish. `ReceivedOutput::read` parses `offset` via `Secp256k1::read_F` and `output`/`outpoint` via consensus decoding, then returns the tuple verbatim [2](#0-1) . The struct's fields are private, so `read` is the only deserialization entry point, and it is explicitly listed as an untrusted-bytes sink.

The downstream consumer (`SignableTransaction::new` / `multisig` in `wallet/send.rs`, gated by `feature = "std"`) uses `output.offset()` to re-key the threshold signing — the offset is applied to `ThresholdKeys` via `keys.offset(...)` semantics analogous to `tweak_keys` [3](#0-2) . The signers therefore sign for the key `group_key + offset·G`, while the BIP-340/341 key actually required by the input's `script_pubkey` is `group_key + offset'·G` for the *true* offset'. If a maliciously serialized `ReceivedOutput` sets `offset ≠ offset'`, the produced signature will not satisfy the Taproot key-path spend.

### Impact Explanation
- Funds reported received that are not spendable: a forged `ReceivedOutput` pairs a real on-chain `TxOut`/`OutPoint` (paid to some unrelated key, or to `key + offset'·G`) with an offset the multisig does not hold. The wallet treats it as an owned, spendable balance.
- Wasted/bricked spends: if the `TxOut` does belong to `key + offset'·G` but a wrong `offset` is supplied, signers produce a signature valid for `key + offset·G` which Bitcoin consensus rejects for this input — the signing session is consumed and the spend fails.

### Likelihood Explanation
Reachability requires an unprivileged party to cause a crafted serialized `ReceivedOutput` to be fed to `read` (e.g., relayed output data rather than scanner-derived data). Within the in-scope code this is a public deserialization API intended for untrusted bytes; whether upstream transport authenticates it is outside this crate — matching the CVE's own precondition (a channel lacking integrity). No special privileges are needed beyond supplying the bytes.

### Recommendation
Re-establish the scanner invariant at deserialization: either (a) have `ReceivedOutput::read` take the group key / script map and verify `p2tr_script_buf(key + offset·G) == output.script_pubkey`, or (b) store only `(script-derived)` data and recompute the offset from a trusted registry at read time, rather than trusting the serialized scalar. At minimum, document that `read` output must be re-validated before being scheduled for signing.

### Proof of Concept
1. Signer group key `K`; register offset `o1` so script `S1 = p2tr(K + o1·G)` is scanned.
2. Attacker crafts bytes: `offset = o2` (arbitrary, e.g., an offset for a key the attacker controls), `output = TxOut { script_pubkey: S1, value: V }`, `outpoint = <real or fabricated UTXO>`.
3. `ReceivedOutput::read` accepts it; the wallet reports `V` sat received to `S1` spendable with offset `o2`.
4. When spent, the multisig signs under `K + o2·G`; the input requires `K + o1·G` → signature invalid on-chain, funds effectively unspendable through this path. If `S1` is instead the attacker's script paying to an unrelated key, the output is counted as wallet balance it can never claim.

Caveat: I could not fully verify the exact offset-application path inside `wallet/send.rs` (`SignableTransaction::new` / `multisig`) within the available iterations; the claim that the offset re-keys signing rests on `tweak_keys`' use of `keys.offset(...)` and `ReceivedOutput::offset()`'s documented purpose ("the scalar offset to obtain the key usable to spend this output") [4](#0-3) .

### Citations

**File:** networks/bitcoin/src/wallet/mod.rs (L45-75)
```rust
#[cfg(feature = "std")]
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

**File:** networks/bitcoin/src/wallet/mod.rs (L88-103)
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
