### Title
`SignableTransaction` trusts attacker-supplied prevout amounts — the "first step" (funding output) is never verified, so `complete` emits a transaction that consensus rejects - (File: networks/bitcoin/src/wallet/send.rs)

### Summary
The Blueprint bug class is a two-step operation where the second step runs under a modality that assumes the first step already happened (funds moved / state established) when it never did. In `bitcoin-serai`, `SignableTransaction::new` and `SignableTransaction::multisig` build and sign a spend entirely from `ReceivedOutput` values supplied via `ReceivedOutput::read`. The only verification performed is that the derived, offset group key's `p2tr_script_buf` equals the declared `script_pubkey` — the declared `value` (the "first step" deposit) is never checked against the actual on-chain UTXO. Signing then commits to those fabricated prevouts via `Prevouts::All`, so `complete` returns a fully-"successful" `Transaction` that every Bitcoin node will reject. The second step (spend) is executed on a first step that never existed — exactly the reported modality mismatch.

### Finding Description
`ReceivedOutput::read` ( [1](#0-0) ) deserializes an untrusted `offset`, `TxOut` (including `value`), and `outpoint` with no consistency check that the `outpoint` actually resolves to that `TxOut` on chain.

`SignableTransaction::new` then uses the attacker-chosen `output.value` for all accounting: `input_sat` ( [2](#0-1) ), the `NotEnoughFunds` check ( [3](#0-2) ), the change-amount computation ( [4](#0-3) ), and stores the fabricated `TxOut` as the prevout committed in the sighash ( [5](#0-4) ).

`multisig` verifies only `p2tr_script_buf(offset.group_key()) == prevouts[i].script_pubkey` ( [6](#0-5) ) — i.e., "is this key able to spend an output with this script," never "does an output with this value exist at this outpoint." `sign` then commits all declared prevouts via `Prevouts::All` + `taproot_key_spend_signature_hash` ( [7](#0-6) ), and `complete` assembles witnesses and returns `Ok(tx)` with no further validation ( [8](#0-7) ).

Because BIP-341 key-spend sighashes commit to each prevout's `amount` and `script_pubkey`, any divergence between the declared `TxOut` and the real UTXO produces a Schnorr signature that fails `SIGHASH` validation on every node. The honest signers' ceremony completes, `complete` returns `Ok`, and the resulting transaction is unbroadcastable.

Two dishonest variants are reachable purely from `ReceivedOutput::read` bytes:

1. **Inflated value**: attacker claims a larger input amount than the real UTXO. `NotEnoughFunds` passes, a large `needed_fee`/change is computed against phantom funds, and the produced transaction's signatures are invalid. If the discrepancy is large, the transaction's implied `fee()` ( [9](#0-8) ) far exceeds what exists.
2. **Deflated value**: real funds exist, but the declared amount is lower. `NotEnoughFunds` may wrongly fire (spurious `Err`), or signing succeeds against wrong prevouts and again yields an invalid transaction.

### Impact Explanation
An unprivileged party who can feed crafted `ReceivedOutput` bytes (the documented untrusted path: `ReceivedOutput::read`, e.g., outputs relayed through the message queue / coordinator rather than produced locally by `Scanner::scan_transaction`) causes honest FROST participants to run a full threshold signing ceremony whose output is a transaction that consensus rejects — funds that appear spendable (`multisig` returned `Some`, `complete` returned `Ok`) are in fact not spendable. This burns nonce commitments and forces the ceremony to be rediscovered/restarted; repeated injections can continuously stall withdrawals of a vault whose inputs are described by untrusted `ReceivedOutput`s. Per the rubric this is the "funds reported received that are not spendable" / "second step executed on a first step that never happened" outcome. Severity: Medium — denial of spendability of threshold-held funds, reachable from unauthenticated bytes, no key leakage required.

### Likelihood Explanation
The vulnerability is deterministic: any mismatch between the serialized `TxOut` and the on-chain UTXO produces an invalid signature with probability 1, while every in-crate check (`NoInputs`, `NotEnoughFunds`, `multisig`'s script equality, sighash construction) still passes. The only prerequisite is that `ReceivedOutput`s arrive via `ReceivedOutput::read` from a source the attacker can influence — precisely the untrusted-bytes surface the scope rules allow — rather than from the local `Scanner`, which builds them from real transaction data ( [10](#0-9) ). No collusion, no validator misbehavior, and no leaked key material is needed; a single corrupted field suffices.

### Recommendation
Verify the first step before running the second: in `SignableTransaction::new` or `multisig`, confirm each `ReceivedOutput`'s `(outpoint → TxOut)` binding against the chain (or require callers to only feed outputs obtained from `Scanner::scan_transaction`/`scan_block` over verified blocks, documenting that `ReceivedOutput::read` bytes are untrusted). At minimum, `multisig` should additionally authenticate the prevout — e.g., recompute/verify the output against the referenced transaction — so a fabricated `value` is rejected with `None` before preprocesses are generated. Optionally, sanity-check `fee() == needed_fee` bounds before signing so inflated prevouts cannot masquerade as a funded transaction.

### Proof of Concept
Conceptually (Rust pseudo-code, in-scope `networks/bitcoin/src/wallet` API):

```rust
// Attacker serializes a ReceivedOutput whose outpoint is real but whose
// TxOut.value is inflated (or whose outpoint is entirely fabricated).
let mut bytes = Vec::new();
// offset matching the vault key so p2tr_script_buf check passes
bytes.extend(real_offset.to_bytes());
// TxOut { value: 10_000_000 /* claimed */, script_pubkey: vault_p2tr }
bytes.extend(serialize(&forged_txout));
// OutPoint of a real (but smaller) deposit, or a nonexistent one
bytes.extend(serialize(&real_outpoint));

let output = ReceivedOutput::read(&mut &bytes[..]).unwrap();

// Honest signers build the spend — all checks pass because input_sat is
// taken from the attacker-supplied value.
let stx = SignableTransaction::new(vec![output], &payments, change, None, fee_rate).unwrap();
let machine = stx.multisig(&keys).unwrap();   // Some: script_pubkey matches
let (sign_machine, preprocess) = machine.preprocess(&mut rng);
let (sig_machine, shares) = sign_machine.sign(commitments, b"").unwrap();
let tx = sig_machine.complete(shares).unwrap(); // Ok: witnesses assembled

// Broadcasting `tx` fails at every node: the BIP-341 sighash committed to
// prevouts (amount, script_pubkey) that don't match the real UTXO, so the
// Schnorr signature is invalid. The ceremony "succeeded" yet funds are
// unspendable — the second step ran on a first step that never occurred.
```

The `Scanner`-derived path is safe only because `scan_transaction` clones the `TxOut` verbatim from a real transaction ( [11](#0-10) ); nothing enforces that all `ReceivedOutput`s consumed by `SignableTransaction` originate there, and `read` explicitly accepts the same structure from arbitrary bytes.

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

**File:** networks/bitcoin/src/wallet/send.rs (L137-141)
```rust
  /// Returns the fee this transaction will use.
  pub fn fee(&self) -> u64 {
    self.prevouts.iter().map(|prevout| prevout.value.to_sat()).sum::<u64>() -
      self.tx.output.iter().map(|prevout| prevout.value.to_sat()).sum::<u64>()
  }
```

**File:** networks/bitcoin/src/wallet/send.rs (L175-176)
```rust
    let input_sat = inputs.iter().map(|input| input.output.value.to_sat()).sum::<u64>();
    let offsets = inputs.iter().map(|input| input.offset).collect();
```

**File:** networks/bitcoin/src/wallet/send.rs (L215-221)
```rust
    if input_sat < (payment_sat + needed_fee) {
      Err(TransactionError::NotEnoughFunds {
        inputs: input_sat,
        payments: payment_sat,
        fee: needed_fee,
      })?;
    }
```

**File:** networks/bitcoin/src/wallet/send.rs (L228-233)
```rust
      if let Some(value) = input_sat.checked_sub(payment_sat + fee_with_change) {
        if value >= DUST {
          tx_outs.push(TxOut { value: Amount::from_sat(value), script_pubkey: change });
          weight = weight_with_change;
          needed_fee = fee_with_change;
        }
```

**File:** networks/bitcoin/src/wallet/send.rs (L253-255)
```rust
      prevouts: inputs.drain(..).map(|input| input.output).collect(),
      needed_fee,
    })
```

**File:** networks/bitcoin/src/wallet/send.rs (L275-279)
```rust
    for i in 0 .. self.tx.input.len() {
      let offset = keys.clone().offset(self.offsets[i]);
      if p2tr_script_buf(offset.group_key())? != self.prevouts[i].script_pubkey {
        None?;
      }
```

**File:** networks/bitcoin/src/wallet/send.rs (L373-391)
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
```

**File:** networks/bitcoin/src/wallet/send.rs (L413-428)
```rust
  fn complete(
    mut self,
    mut shares: HashMap<Participant, Self::SignatureShare>,
  ) -> Result<Transaction, FrostError> {
    for (input, schnorr) in self.tx.input.iter_mut().zip(self.sigs.drain(..)) {
      let sig = schnorr.complete(
        shares.iter_mut().map(|(l, shares)| (*l, shares.remove(0))).collect::<HashMap<_, _>>(),
      )?;

      let mut witness = Witness::new();
      witness.push(sig);
      input.witness = witness;
    }

    Ok(self.tx)
  }
```
