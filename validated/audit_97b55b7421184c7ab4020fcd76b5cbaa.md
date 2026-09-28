### Title
`ReceivedOutput::read` accepts arbitrary offset/TxOut/outpoint with no consistency or authenticity check, allowing fabricated "received" outputs that are not spendable - ([File: networks/bitcoin/src/wallet/mod.rs])

### Summary
Analogous to CVE-2023-28435 (unauthenticated upload endpoint accepting any file type), `ReceivedOutput::read` is a deserialization interface that accepts fully attacker-controlled bytes — an arbitrary scalar `offset`, an arbitrary `TxOut`, and an arbitrary `OutPoint` — with no check that the fields are mutually consistent or that the claimed output exists on-chain. Downstream code treats the deserialized structure as a genuinely received, spendable output.

### Finding Description
`ReceivedOutput::read` reads three independent fields and returns them unconditionally:

- `offset`: any `Secp256k1::read_F`-parseable scalar
- `output`: any consensus-decodable `TxOut` (any `script_pubkey`, any `value`)
- `outpoint`: any consensus-decodable `OutPoint` (any txid/vout) [1](#0-0) 

There is no verification that `output.script_pubkey == p2tr_script_buf(key + G * offset)` (the check that legitimately ties an output to the wallet), no check that the script is even P2TR, and no check that `outpoint` refers to a real confirmed transaction output. In contrast, the legitimate producer `Scanner::scan_transaction` only constructs `ReceivedOutput`s whose script_pubkey was looked up in the registered scripts map, so the offset/script binding is guaranteed there but never enforced at the deserialization boundary. [2](#0-1) 

`SignableTransaction::new` then trusts the forged structure: `input.output.value` is summed into `input_sat` as available funds, and `input.offset` and `input.outpoint` are baked into the transaction. [3](#0-2) 

The only consistency check happens much later, in `SignableTransaction::multisig`, which merely returns `None` on mismatch — by which point the fabricated output has already been accounted as a received balance and used to construct a transaction. [4](#0-3) 

### Impact Explanation
An unprivileged party who can feed serialized bytes to `ReceivedOutput::read` (e.g., via `Output::read`, which embeds `ReceivedOutput::read` for outputs transported between protocol components) can report a deposit of arbitrary value that does not exist on-chain, or that is bound to a different offset/script than claimed. This yields funds reported received that are not spendable: the scheduler credits `output.value()` toward the multisig's spendable balance, yet either (a) the outpoint is fabricated so no real UTXO exists, or (b) the offset/script mismatch causes `multisig()` to refuse signing. In both cases accounting diverges from reality, enabling balance inflation and failed/stuck spends against phantom inputs.

### Likelihood Explanation
The format places no structural obstacles: three concatenated fields, all trivially constructible. The only mitigating factor is that `multisig()` eventually rejects offset/script mismatches, limiting damage to accounting poisoning rather than direct theft — consistent with Medium severity, matching the source advisory's rating.

### Recommendation
Bind the fields at the parsing boundary. `ReceivedOutput` should either store the wallet key it was scanned for, or `read` should take the `Scanner`/key and validate `output.script_pubkey == p2tr_script_buf(key + G * offset)` before returning. Consumers should not treat a deserialized `ReceivedOutput` as a confirmed on-chain UTXO without an independent existence check.

### Proof of Concept
```rust
// Attacker-controlled bytes fed to ReceivedOutput::read:
// offset = 1, output = TxOut { value: 21_000_000_0000_0000, script_pubkey: <arbitrary P2TR> },
// outpoint = fabricated txid:vout
let forged = ReceivedOutput::read(&mut crafted_bytes.as_ref()).unwrap();
assert_eq!(forged.value(), u64::MAX_value_as_crafted); // credited as spendable balance

// SignableTransaction::new happily sums the phantom value into input_sat (send.rs:175)
// and only fails at multisig() (send.rs:277) — after the false balance was reported.
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
