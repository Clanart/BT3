### Title
`ReceivedOutput`/`Output` deserialize an attacker-claimed scalar `offset` that is never verified against the output's `script_pubkey`, misattributing outputs to arbitrary multisig keys — ([File: networks/bitcoin/src/wallet/mod.rs](networks/bitcoin/src/wallet/mod.rs), [processor/src/networks/bitcoin.rs](processor/src/networks/bitcoin.rs))

### Summary
Analogous to the msq bug where dApp-supplied `name`/`symbol` were displayed while a separate, unverified `assetId` was acted upon, Serai's Bitcoin `ReceivedOutput` pairs a caller-supplied key `offset` with a `TxOut`/`OutPoint` and never checks that `output.script_pubkey == p2tr(group_key + offset*G)`. The `offset` is only "metadata" asserted alongside the output; nothing binds it to the output's actual key. Downstream code then trusts this pairing.

### Finding Description
`ReceivedOutput::read` deserializes `offset`, `output`, and `outpoint` independently, with no consistency check. [1](#0-0) 

The only place the offset is legitimately bound is inside `Scanner::scan_transaction`, which derives `offset` from its internal `scripts` map keyed by `script_pubkey`. [2](#0-1) 

But when a `ReceivedOutput` (or the processor's `Output` wrapping it) arrives as untrusted bytes, `Output::key()` reconstructs the "owning" multisig key by subtracting `offset*G` from whatever x-only key sits in the claimed `script_pubkey`. [3](#0-2) 

An unprivileged party feeding `ReceivedOutput::read`/`Output::read` can therefore claim an output paying to an arbitrary Taproot key `K` while supplying `offset = o` such that `K - o*G` equals any point they choose — e.g., a victim validator-set group key. The output's `balance()` (`output.value`) is then reported as received under a key that cannot spend it. [4](#0-3) 

The same unbound pair feeds `SignableTransaction::new(inputs: Vec<ReceivedOutput>, …)`, which uses the claimed `output.value` for fee/funds accounting and the claimed `outpoint`/`offset` to build the sighash the multisig is asked to sign — signing math proceeds against metadata that was never verified against the real output key.

### Impact Explanation
Funds reported as received under a multisig key that cannot spend them (output credited to the wrong/even an attacker's key), and signing sessions driven by a forged `offset`/`output` pairing producing unusable signatures or wasted fees. This maps to the accepted impact classes "funds reported received that are not spendable" and signatures produced over attacker-constructed input data.

### Likelihood Explanation
Reachable wherever serialized `Output`/`ReceivedOutput` bytes cross a trust boundary (the prompt's explicit in-scope read surface includes `ReceivedOutput::read`). The forged pair needs only a valid scalar and a syntactically valid P2TR `TxOut`/`OutPoint` — trivially constructible. Severity Medium/High depending on whether the surrounding protocol credits or attempts to spend such outputs before on-chain confirmation of the true key.

### Recommendation
Either make `ReceivedOutput` unforgeable (only constructible via `Scanner`, sealing the fields) or add a verification step: given the expected group key, recompute `p2tr_script_buf(key + offset*G)` and require it to equal `output.script_pubkey` before trusting `key()`, `balance()`, or passing the output to `SignableTransaction::new`. Alternatively, store only `(outpoint, offset)` and resolve the `TxOut` from chain data rather than trusting the embedded copy.

### Proof of Concept
```rust
// Given base group key G_key the scanner watches, attacker crafts:
let forged_offset = Scalar::from(123u64); // arbitrary
let attacker_key = G_key + ProjectivePoint::GENERATOR * forged_offset;
let txout = TxOut {
  value: Amount::from_sat(1_000_000),
  script_pubkey: p2tr_script_buf(attacker_key).unwrap(), // even-Y required
};
let outpoint = OutPoint::new(real_txid, 0); // any outpoint the attacker controls
// serialize (offset, txout, outpoint) and feed to ReceivedOutput::read;
// Output::key() then reports attacker_key - forged_offset*G == G_key,
// crediting the attacker's output to the victim multisig while the
// victim cannot sign for it (its offset map never registered this pairing).
```
Consistency is never checked because `read` trusts both halves independently, exactly like `protected_handleAddAssetAccount` trusting dApp-supplied `name`/`symbol` while acting on `assetId`.

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

**File:** processor/src/networks/bitcoin.rs (L112-122)
```rust
  fn key(&self) -> ProjectivePoint {
    let script = &self.output.output().script_pubkey;
    assert!(script.is_p2tr());
    let Instruction::PushBytes(key) = script.instructions_minimal().last().unwrap().unwrap() else {
      panic!("last item in v1 Taproot script wasn't bytes")
    };
    let key = XOnlyPublicKey::from_slice(key.as_ref())
      .expect("last item in v1 Taproot script wasn't x-only public key");
    Secp256k1::read_G(&mut key.public_key(Parity::Even).serialize().as_slice()).unwrap() -
      (ProjectivePoint::GENERATOR * self.output.offset())
  }
```

**File:** processor/src/networks/bitcoin.rs (L128-130)
```rust
  fn balance(&self) -> ExternalBalance {
    ExternalBalance { coin: ExternalCoin::Bitcoin, amount: Amount(self.output.value()) }
  }
```
