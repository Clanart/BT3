### Title
`ReceivedOutput::read` accepts attacker-claimed offset/outpoint/value without binding the offset to the output's `script_pubkey` — funds reported received are not spendable (File: networks/bitcoin/src/wallet/mod.rs)

### Summary
Analogous to GHSA-93vf-569f-22cq — where an allowlisted `<style>` element's text content is never inspected, letting `url()`/`@import` pass through — `ReceivedOutput::read` in `networks/bitcoin/src/wallet/mod.rs` deserializes an "allowlisted" structure (scalar + `TxOut` + `OutPoint`) while never validating the inner relationship that makes the output actually spendable: that `p2tr_script_buf(key + G*offset)` equals `output.script_pubkey`, that the `outpoint` exists on-chain, or that `output.value` is the real UTXO value. Each field is syntactically validated (`Secp256k1::read_F` canonical scalar, `TxOut::consensus_decode`, `OutPoint::consensus_decode`) but the semantics — does this offset actually spend this output — are never checked, either at read time or downstream in `SignableTransaction::new` / `TransactionSignMachine::sign`, which re-key each input's signature by `output.offset()` and commit the claimed prevouts via `Prevouts::All`. [1](#0-0) [2](#0-1) 

### Finding Description
`ReceivedOutput` is the wallet's sole representation of a spendable UTXO. Honest construction goes through `Scanner::scan_transaction`, which only matches on `output.script_pubkey` against the registered `scripts` map and copies the remaining fields verbatim — it never inspects `value` or any other field of the `TxOut` (exactly the "matched tag, uninspected content" shape of the DOMSanitizer bug). [3](#0-2) 

The deserialization path is worse: `ReceivedOutput::read` accepts completely attacker-controlled bytes and performs only canonicality checks. There is no verification that:

- the claimed `offset` actually derives `output.script_pubkey` from the group's key (the only check that would bind the two fields),
- the `outpoint` references a real, unspent UTXO,
- `output.value` equals the on-chain amount.

Downstream, `SignableTransaction::new`/`TransactionSignMachine::sign` consume these outputs directly: each input is signed by re-keying the group key with `output.offset()` and the BIP-341 sighash is computed with `Prevouts::All(&self.tx.prevouts)`, committing to the attacker-claimed `TxOut` values. If `offset` doesn't match the script_pubkey, or the claimed value differs from the real one, the produced Schnorr signatures are invalid for that input — yet the wallet has already recorded the output as received spendable funds and signs the transaction. [4](#0-3) 

### Impact Explanation
- **Funds reported received that are not spendable**: an attacker who can supply a serialized `ReceivedOutput` (the type exposes `read`/`serialize` as a public interchange format) can cause the wallet to report a balance (via `value()`) for an outpoint that doesn't exist, doesn't pay to the multisig, or whose offset doesn't match its script. The spend attempt produces a transaction that fails signature verification at broadcast.
- **Signing of unusable inputs**: `TransactionSignMachine::sign` produces real threshold signatures over `Prevouts::All` including fabricated prevout data, burning a signing session on a transaction that can never confirm.
- A forged `ReceivedOutput` naming a *real* multisig outpoint but with a mismatched `offset` causes per-input re-keying to a key that doesn't control the output — again an invalid spend of funds the wallet believes it owns.

### Likelihood Explanation
Reachable whenever untrusted bytes are passed to `ReceivedOutput::read` — a listed in-scope surface — e.g., externally-sourced output records, or outputs relayed between components that didn't originate from `Scanner::scan_transaction`/`scan_block` on a verified chain view. No key compromise, colluding threshold, or malicious validator is required: only control of the serialized bytes. Note: outputs obtained via `Scanner` over a confirmed block are authentic, so the vulnerability is limited to consumers of deserialized `ReceivedOutput`s; severity Medium accordingly.

### Recommendation
In `ReceivedOutput::read` (or at `SignableTransaction::new`), require the caller's group `key` and verify `p2tr_script_buf(key + (ProjectivePoint::GENERATOR * offset)) == Some(output.script_pubkey)`. Consumers should additionally confirm the `outpoint` exists on-chain and that the recorded `output.value` matches the confirmed UTXO before reporting the balance or signing — i.e., inspect the "contents" of the accepted structure, not just its encoding.

### Proof of Concept
```rust
use bitcoin::{TxOut, OutPoint, Amount, ScriptBuf, Txid, hashes::Hash};
use bitcoin_serai::wallet::ReceivedOutput;
use k256::{Scalar, ProjectivePoint, elliptic_curve::group::Group};

// Attacker claims an output paying to a script NOT derived from the group's key
// (or a fabricated outpoint entirely), with any offset/value.
let mut buf = Vec::new();
buf.extend(Scalar::ZERO.to_bytes());            // offset = 0 (claimed)
let txout = TxOut {
  value: Amount::from_sat(1_000_000),
  script_pubkey: ScriptBuf::new(),              // arbitrary / foreign script
};
buf.extend(bitcoin::consensus::serialize(&txout));
buf.extend(bitcoin::consensus::serialize(&OutPoint {
  txid: Txid::all_zeros(), vout: 0,             // fabricated outpoint
}));

// Accepted: only canonicality is checked; offset/script_pubkey/outpoint/value
// consistency is never verified.
let forged = ReceivedOutput::read(&mut buf.as_slice()).unwrap();
assert_eq!(forged.value(), 1_000_000); // "received" balance that is unspendable
// Passing `forged` to SignableTransaction::new produces signatures over
// Prevouts::All that commit to the claimed data; the resulting tx fails
// input verification on the real chain.
```

Files: `networks/bitcoin/src/wallet/mod.rs` (`ReceivedOutput::read` lines 122–133, `Scanner::scan_transaction` lines 199–214), `networks/bitcoin/src/wallet/send.rs` (`SignableTransaction::new`/`TransactionSignMachine::sign` with `Prevouts::All`, lines 150–157, 373–395).

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

**File:** networks/bitcoin/src/wallet/send.rs (L61-99)
```rust
impl SignableTransaction {
  fn calculate_weight_vbytes(
    inputs: usize,
    payments: &[(ScriptBuf, u64)],
    change: Option<&ScriptBuf>,
  ) -> (u64, u64) {
    // Expand this a full transaction in order to use the bitcoin library's weight function
    let mut tx = Transaction {
      version: Version(2),
      lock_time: LockTime::ZERO,
      input: vec![
        TxIn {
          // This is a fixed size
          // See https://developer.bitcoin.org/reference/transactions.html#raw-transaction-format
          previous_output: OutPoint::default(),
          // This is empty for a Taproot spend
          script_sig: ScriptBuf::new(),
          // This is fixed size, yet we do use Sequence::MAX
          sequence: Sequence::MAX,
          // Our witnesses contains a single 64-byte signature
          witness: Witness::from_slice(&[vec![0; 64]])
        };
        inputs
      ],
      output: payments
        .iter()
        // The payment is a fixed size so we don't have to use it here
        // The script pub key is not of a fixed size and does have to be used here
        .map(|payment| TxOut {
          value: Amount::from_sat(payment.1),
          script_pubkey: payment.0.clone(),
        })
        .collect(),
    };
    if let Some(change) = change {
      // Use a 0 value since we're currently unsure what the change amount will be, and since
      // the value is fixed size (so any value could be used here)
      tx.output.push(TxOut { value: Amount::ZERO, script_pubkey: change.clone() });
    }
```

**File:** networks/bitcoin/src/wallet/send.rs (L373-395)
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
```
