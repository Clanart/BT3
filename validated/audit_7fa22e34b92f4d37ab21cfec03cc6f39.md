### Title
`SignableTransaction::multisig` validates claimed input scripts against attacker-controlled offsets instead of a fixed registered set, allowing unregistered key-path inputs into a signed transaction - (networks/bitcoin/src/wallet/send.rs)

### Summary

Analogous to a path traversal where a name is checked for consistency but never confined to the restricted directory, `SignableTransaction::multisig` checks that each input's `script_pubkey` equals `p2tr(group_key + offset·G)` — but **both** the `offset` scalar and the `script_pubkey` come from the same untrusted `ReceivedOutput`. The check is self-consistent and vacuous: any offset resolves to a "valid" script, escaping the restricted set of offsets registered in `Scanner::scripts`. An unprivileged party who can feed bytes to `ReceivedOutput::read` (an explicitly reachable deserialization endpoint) can attach arbitrary inputs — including their own UTXOs or outputs under never-registered offset keys — to a transaction the FROST multisig will sign, directing real multisig funds to attacker-chosen outputs.

### Finding Description

`ReceivedOutput` is deserialized from untrusted bytes in `ReceivedOutput::read`, which reads the `offset`, the full `TxOut` (value + `script_pubkey`), and the `outpoint` verbatim with no binding to any registered key set [1](#0-0) . `SignableTransaction::new` stores the attacker-supplied `offsets` and `prevouts` verbatim and uses the claimed `output.value` for funding math [2](#0-1) [3](#0-2) .

The only confinement check happens in `multisig`: for each input it derives `keys.clone().offset(self.offsets[i])` and requires `p2tr_script_buf(offset.group_key()) == self.prevouts[i].script_pubkey` [4](#0-3) . Since the attacker chooses `offsets[i]` *and* `prevouts[i].script_pubkey`, this equality always holds for a properly crafted input — the "restricted directory" (the `Scanner`'s registered `scripts` map [5](#0-4) ) is never consulted. `register_offset`'s own documentation warns that arbitrary offsets are dangerous and must be securely generated [6](#0-5) , yet `multisig` accepts offsets with no provenance check at all.

Signing then proceeds normally: `TransactionSignMachine::sign` builds the sighash over `Prevouts::All(&self.tx.prevouts)` and produces a valid key-spend signature for every input, including the injected one [7](#0-6) .

### Impact Explanation

An attacker creates a real UTXO paying to `p2tr(group_key + o·G)` for an offset `o` they chose (they need only send funds there; the multisig provides the key-path signature — no discrete log required), then submits a `ReceivedOutput` encoding `{offset: o, that TxOut, that outpoint}`. When this input is bundled with genuine multisig-owned inputs in `SignableTransaction::new`, the threshold group produces a fully valid signed transaction. If the injected input flows into a transaction whose payments/change are attacker-influenced, genuine multisig inputs are spent to attacker-chosen destinations — concrete signing of an unintended spend and theft of funds. Even absent payment control, the signing session signs a transaction committing to attacker-fabricated prevout data (values, outpoints, offsets) that was never registered or chain-verified, enabling deposits "received" under unregistered offsets that bypass the scanner's allowlist semantics.

### Likelihood Explanation

Exploitation requires a path where untrusted `ReceivedOutput` bytes reach `SignableTransaction::new`/`multisig` — precisely the deserialization surface (`ReceivedOutput::read`) exposed for outputs persisted and reloaded by downstream consumers. No collusion, malicious validator, or key material is needed; the attacker only needs one public network interaction (their own deposit transaction) plus the ability to supply the serialized output. The primitives (`read`, `new`, `multisig`, `sign`) compose as designed — the flaw is the missing membership check, not a misuse edge case, since the code provides no mechanism to bind offsets to the registered set.

### Recommendation

Bind each input to the restricted set at signing time: `multisig` (or `SignableTransaction::new`) should verify that each `offsets[i]` is a registered/derived offset — e.g., carry the `Scanner`'s `scripts` map (or a commitment to it) and require `self.scripts.get(&prevouts[i].script_pubkey) == Some(&offsets[i])`, matching the resolution path used in `scan_transaction` [8](#0-7) . Additionally, prevout `value`s should be verified against chain data rather than trusted from serialized `ReceivedOutput`s, since the claimed value feeds both fee math and the sighash.

### Proof of Concept

```rust
// Attacker side: pick an arbitrary offset never registered in Scanner
let o = Scalar::from(0xdeadbeefu64);
let attacker_key = group_key + (ProjectivePoint::GENERATOR * o);
// ensure even-Y; increment o until p2tr_script_buf returns Some
let script = p2tr_script_buf(attacker_key).unwrap();

// Attacker broadcasts a real deposit to `script`, obtaining real outpoint P
let forged_bytes = {
    let mut b = o.to_bytes().to_vec();
    b.extend(serialize(&TxOut { value: Amount::from_sat(10_000), script_pubkey: script.clone() }));
    b.extend(serialize(&P));
    b
};

// Fed to the reachable read endpoint
let fake_input = ReceivedOutput::read(&mut forged_bytes.as_slice()).unwrap();

// Bundled with a genuine multisig-owned UTXO
let tx = SignableTransaction::new(
    vec![real_serai_input, fake_input],
    &[(attacker_addr, 100_000)],   // attacker-influenced payment
    None, None, 20,
).unwrap();

// multisig() accepts: script equality holds because BOTH sides were attacker-chosen
let machine = tx.multisig(&keys[&Participant::new(1).unwrap()]).unwrap();
// sign() produces a valid tx spending the real Serai input to attacker_addr
```

`p2tr_script_buf(offset.group_key()) == prevouts[i].script_pubkey` is satisfied by construction for any `o`, so the only barrier — which was intended to prove "this input belongs to us" — proves nothing beyond self-consistency.

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

**File:** networks/bitcoin/src/wallet/mod.rs (L153-165)
```rust
pub struct Scanner {
  key: ProjectivePoint,
  scripts: HashMap<ScriptBuf, Scalar>,
}

impl Scanner {
  /// Construct a Scanner for a key.
  ///
  /// Returns None if this key can't be scanned for.
  pub fn new(key: ProjectivePoint) -> Option<Scanner> {
    let mut scripts = HashMap::new();
    scripts.insert(p2tr_script_buf(key)?, Scalar::ZERO);
    Some(Scanner { key, scripts })
```

**File:** networks/bitcoin/src/wallet/mod.rs (L177-179)
```rust
  /// The offsets registered must be securely generated. Arbitrary offsets may introduce a script
  /// path into the output, allowing the output to be spent by satisfaction of an arbitrary script
  /// (not by the signature of the key).
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

**File:** networks/bitcoin/src/wallet/send.rs (L175-176)
```rust
    let input_sat = inputs.iter().map(|input| input.output.value.to_sat()).sum::<u64>();
    let offsets = inputs.iter().map(|input| input.offset).collect();
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

**File:** networks/bitcoin/src/wallet/send.rs (L373-397)
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

    Ok((TransactionSignatureMachine { tx: self.tx.tx, sigs }, shares))
```
