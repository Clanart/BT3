### Title
`ReceivedOutput::read` deserializes an unverified (offset, TxOut, OutPoint) triple — attacker-controlled bytes make the wallet claim and attempt to sign spends of outputs it does not own - (File: networks/bitcoin/src/wallet/mod.rs)

### Summary
The Checkmk CVE is a confused-deputy / unvalidated-indirection bug: an unprivileged user edits a reference (a symlink) that a privileged component dereferences without checking it still points at what it should. The Serai analog lives in the Bitcoin wallet's `ReceivedOutput` type. A `ReceivedOutput` is an indirect claim — "the UTXO at `outpoint` contains `output`, which pays to `key + offset·G`". `scan_transaction` produces internally-consistent triples (the `script_pubkey` is only emitted because it exists in the `scripts` map keyed to a registered offset), but `ReceivedOutput::read` accepts all three fields as untrusted bytes with no consistency check, and downstream code only re-verifies one of the three links.

### Finding Description
`ReceivedOutput::read` reads an arbitrary scalar `offset`, an arbitrary `TxOut`, and an arbitrary `OutPoint` from the byte stream and returns them as a `ReceivedOutput` with no validation. [1](#0-0) 

When that object reaches `SignableTransaction::new`, the `outpoint` is placed verbatim into `tx.input`, `output.value` is summed into `input_sat` for fee/change/funding checks, and `output` is stored into `prevouts` which is later committed by the Taproot sighash via `Prevouts::All`. [2](#0-1) [3](#0-2) 

The only consistency check anywhere is in `multisig`, which verifies `p2tr_script_buf(key + offset·G) == prevouts[i].script_pubkey` — i.e., it binds the offset to the script, but never binds the `outpoint` to the `TxOut`, and never binds `TxOut.value` to anything on-chain. [4](#0-3) 

So an attacker who can feed bytes to `ReceivedOutput::read` crafts `(offset = 0, TxOut { script_pubkey: p2tr_script_buf(group_key), value: attacker_chosen }, OutPoint::anything())`. The script check passes because the attacker set the script to the real key's address, while `outpoint` and `value` are entirely fabricated — exactly the symlink pattern: a pointer the privileged signer dereferences without confirming it points at the resource it claims to.

### Impact Explanation
Two concrete impacts, both reachable purely from attacker-supplied bytes:

1. **Funds reported received that are not spendable**: the forged `ReceivedOutput` reports an arbitrary `value()` at an `outpoint` that pays nothing (or pays someone else) to the multisig. The wallet's accounting treats it as owned.
2. **Signing an unintended/invalid transaction**: `input_sat` is inflated by the fabricated `value`, so `SignableTransaction::new` passes the `NotEnoughFunds` check and computes change/fee on phantom funds. The full FROST threshold signs a transaction committing (via `SighashType::Default` + `Prevouts::All`) to a nonexistent or mismatched prevout — a signature the network will reject, or which references a UTXO the multisig does not control. Repeated submissions burn signer effort and can stall legitimate spends queued behind the malformed batch.

Because every co-signer independently runs `multisig` and only checks the script↔offset binding, all honest participants will sign the same invalid transaction — there is no cross-check that detects the forged `outpoint`/`value`.

### Likelihood Explanation
Exploitation requires the attacker to deliver serialized `ReceivedOutput` bytes to a component that calls `ReceivedOutput::read` — the read path is explicitly untrusted-input-facing per the API surface. No cryptographic break, no collusion, and no validator privileges are needed; the forged object needs only to set `script_pubkey` to the (public, known) multisig address. Impact is capped at false accounting plus invalid-signature DoS rather than theft, since a transaction spending a nonexistent UTXO cannot confirm — hence Medium, not High.

### Recommendation
Either (a) make `ReceivedOutput::read` verify `p2tr_script_buf` consistency is impossible without the key, so instead revalidate at the trust boundary: in `SignableTransaction::new` or `multisig`, fetch the actual UTXO for each `outpoint` (or require callers to only pass scanner-produced `ReceivedOutput`s and mark `read` as trusted-input-only), or (b) store a commitment to the scan context in `ReceivedOutput` (e.g., the expected `script_pubkey` recomputed from `key + offset·G` — which `multisig` already can do — plus rejecting `ReceivedOutput`s whose `outpoint` was never emitted by the scanner) so fabricated triples are rejected before signing.

### Proof of Concept
```rust
// Attacker knows the multisig group key (public address) and target outpoint.
let key: ProjectivePoint = multisig_group_key; // even-Y, from the known address
let script = p2tr_script_buf(key).unwrap();

// Forge a ReceivedOutput claiming a huge balance at an outpoint the multisig
// does not own (or which doesn't exist).
let mut bytes = Vec::new();
bytes.extend(Scalar::ZERO.to_bytes());                 // offset = 0 => key itself
bytes.extend(bitcoin::consensus::serialize(&TxOut {
    value: Amount::from_sat(1_000_000_000),            // fabricated 10 BTC
    script_pubkey: script,
}));
bytes.extend(bitcoin::consensus::serialize(&OutPoint {
    txid: victim_txid, vout: 0,                        // points at someone else's UTXO
}));

let forged = ReceivedOutput::read(&mut bytes.as_slice()).unwrap();
assert_eq!(forged.value(), 1_000_000_000);             // reported as received

// SignableTransaction accepts it: input_sat = 10 BTC, passes NotEnoughFunds,
// and multisig() succeeds because script_pubkey == p2tr(key + 0*G).
let tx = SignableTransaction::new(vec![forged], &payments, Some(change), None, fee).unwrap();
// tx.multisig(&keys) returns Some(..) -> threshold signs a transaction
// spending a UTXO the multisig does not control / that doesn't exist.
```

### Citations

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

**File:** networks/bitcoin/src/wallet/send.rs (L273-284)
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
```

**File:** networks/bitcoin/src/wallet/send.rs (L373-386)
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
```
