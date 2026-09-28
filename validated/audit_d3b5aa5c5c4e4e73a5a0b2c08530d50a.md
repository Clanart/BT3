### Title
Attacker-controlled scalar offset and output data in `ReceivedOutput::read` let untrusted bytes re-key the spend path and fabricate spendable funds - (File: networks/bitcoin/src/wallet/mod.rs)

### Summary
The CVE-2024-11263 class is a trusted base value (`gp`) combined with a relocatable offset that an attacker can steer so accesses land outside the intended region. In bitcoin-serai, the analogous shape is `ReceivedOutput.offset`: a scalar that is added to the wallet's group key (`keys.clone().offset(offset)` / `group_key * scalar + G * offset`) to decide which key "owns" a UTXO. `ReceivedOutput::read` accepts both the offset and the `TxOut`/`OutPoint` verbatim from the reader, and `SignableTransaction::new` then trusts the claimed `value` and applies the claimed offset without ever proving the claimed outpoint exists or that the offset actually relates to the stored script beyond a self-consistent check.

### Finding Description
`ReceivedOutput::read` deserializes three attacker-controlled fields: `offset` via `Secp256k1::read_F`, `output` (a full `TxOut`, including `value` and `script_pubkey`), and `outpoint` [1](#0-0) . These bytes are consumed unvalidated by `SignableTransaction::new`, which sums `input.output.value` into `input_sat` for fee/funding math and copies `input.offset` into `self.offsets` [2](#0-1) .

Later, `SignableTransaction::multisig` derives the spend key as `keys.clone().offset(self.offsets[i])` and only checks `p2tr_script_buf(offset.group_key()) != self.prevouts[i].script_pubkey` [3](#0-2) . Since `prevouts[i].script_pubkey` came from the same untrusted `TxOut`, the check is self-referential: an attacker can pick *any* offset `o`, compute `script_pubkey = p2tr(key + G*o)` (the wallet's group key is public), and the check passes. There is no verification that `outpoint` refers to a real UTXO, that it pays to that script, or that `offset` is one the `Scanner` actually registered — `Scanner` itself only ever maps `script_pubkey -> offset` [4](#0-3) .

Mirroring the CVE, a base value (the group key / funding accounting) is "relaxed" by an attacker-supplied displacement (`offset`, `value`, `outpoint`) into an arbitrary, inconsistent position.

### Impact Explanation
Two concrete impacts:

1. **Funds reported received that are not spendable / corrupted accounting**: a forged `ReceivedOutput` inflates `input_sat`, so `SignableTransaction::new` constructs a transaction believing it is funded (payments and change sized off phantom value). The resulting signed transaction references a nonexistent or unrelated outpoint and is invalid on-chain — the wallet believes it spent/received funds it cannot spend.

2. **Signing an unintended message**: because `multisig`'s only integrity check is satisfiable with attacker-chosen `offset` + `script_pubkey`, the FROST threshold will produce a valid BIP-340 signature committing (via `Prevouts::All`, `send.rs:375`) to a prevout set containing the fabricated output, authorizing a transaction the wallet should never have constructed. Combined with real inputs, the fabricated input invalidates the whole spend.

### Likelihood Explanation
Reachable whenever `ReceivedOutput::read` / `serialize` bytes cross a trust boundary (the prompt's threat model explicitly includes untrusted bytes fed to `ReceivedOutput::read`), e.g., outputs relayed or stored from an untrusted source rather than produced by the local `Scanner`. No collusion, key leakage, or unsafe code is required — just control of the serialized bytes. Severity: Medium (requires attacker write access to the output set consumed by the wallet; on success it corrupts funding decisions and produces invalid/misbudgeted signed transactions).

### Recommendation
- Bind `ReceivedOutput` to its provenance: store/verify that the `outpoint` exists on-chain (or was produced by `Scanner::scan_*`) and that the `script_pubkey` equals `p2tr(base_key + G*offset)` computed from the *wallet's* key — the check in `multisig` should compare against the scanner-derived script for a *registered* offset, not the attacker-supplied `prevouts[i].script_pubkey`.
- Treat deserialized `ReceivedOutput`s as untrusted: re-validate `offset` against the `Scanner`'s `scripts` map before admitting the output into `SignableTransaction::new`.

### Proof of Concept
```rust
// networks/bitcoin context; attacker controls `bytes` fed to ReceivedOutput::read
let base = keys.group_key(); // public wallet key
let o = Scalar::from(0u64);  // any offset; attacker picks script accordingly
let forged = ReceivedOutput {
    offset: o,
    output: TxOut {
        value: Amount::from_sat(1_000_000), // phantom value
        script_pubkey: p2tr_script_buf(base + ProjectivePoint::GENERATOR * o).unwrap(),
    },
    outpoint: OutPoint::null(), // references nothing on-chain
};
let mut buf = forged.serialize();
let ro = ReceivedOutput::read(&mut buf.as_slice()).unwrap(); // accepted verbatim
let stx = SignableTransaction::new(vec![ro], &payments, Some(change), None, fee).unwrap();
// stx believes it has 1_000_000 sat of input; multisig check passes because
// prevouts[0].script_pubkey was attacker-set to p2tr(base + G*o)
let machine = stx.multisig(&keys).unwrap(); // proceeds to threshold-sign a tx spending nothing
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

**File:** networks/bitcoin/src/wallet/mod.rs (L205-212)
```rust
      if let Some(offset) = self.scripts.get(&output.script_pubkey) {
        res.push(ReceivedOutput {
          offset: *offset,
          output: output.clone(),
          outpoint: OutPoint::new(tx.compute_txid(), vout),
        });
      }
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

**File:** networks/bitcoin/src/wallet/send.rs (L275-282)
```rust
    for i in 0 .. self.tx.input.len() {
      let offset = keys.clone().offset(self.offsets[i]);
      if p2tr_script_buf(offset.group_key())? != self.prevouts[i].script_pubkey {
        None?;
      }

      sigs.push(AlgorithmMachine::new(Schnorr::new(), keys.clone().offset(self.offsets[i])));
    }
```
