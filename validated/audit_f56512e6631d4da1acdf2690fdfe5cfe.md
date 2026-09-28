### Title
Forged `ReceivedOutput` via deserialization — arbitrary (offset, outpoint) pairing not bound to a scanned script — (`networks/bitcoin/src/wallet/mod.rs`)

### Summary
The bug class in CVE-2021-44665 is *path/identifier confusion*: an unvalidated identifier supplied in an input selects a resource outside the authorized set. In `bitcoin-serai` the analog is `ReceivedOutput::read`: the authoritative path (`Scanner::scan_transaction`) binds every reported output to a `Scalar` offset only via the `scripts: HashMap<ScriptBuf, Scalar>` map that `register_offset` built, so `offset` is provably the scalar that derives `output.script_pubkey`. `ReceivedOutput::read` deserializes `offset`, `output`, and `outpoint` from raw bytes with **no check** that `p2tr_script_buf(key + G*offset) == output.script_pubkey` — i.e., the serialized bytes act like the untrusted filename in `download.php`, letting an attacker "traverse" to any (offset, outpoint) combination outside the set the `Scanner` authorized.

### Finding Description
- `Scanner::new`/`register_offset` populate `scripts` keyed by `ScriptBuf`; `scan_transaction` only emits `ReceivedOutput { offset, output, outpoint }` when `self.scripts.get(&output.script_pubkey)` returns the offset — the offset↔output binding is enforced [1](#0-0) .
- `ReceivedOutput::read` reads a `Scalar` via `Secp256k1::read_F`, then consensus-decodes an arbitrary `TxOut` and `OutPoint`, and returns them paired unconditionally — the binding is dropped [2](#0-1) .
- There is no stored `key`/`ScriptBuf` on `ReceivedOutput`, so downstream code (spend path in `send.rs`, coin selection, `Prevouts::All` sighash commitment) has no way to detect that the supplied offset does not derive the output's script key.

### Impact Explanation
Funds reported received that are not spendable, and forged "deposit" claims. An attacker who can feed serialized `ReceivedOutput`s to a component (e.g., cross-process persistence/queue messages carrying `ReceivedOutput::read` payloads) can either (a) claim receipt of arbitrary UTXOs — including outputs that never paid any registered script — inflating reported balances, or (b) pair a real wallet-owned output with a wrong offset, so the spend path derives a key that does not control the output and produces an invalid Taproot signature. In case (b) the wallet records value it can never spend, and coin selection may build transactions around unspendable inputs, permanently inflating fees/DoS-ing signing rounds for "deposits" that do not exist.

### Likelihood Explanation
Reachable wherever `ReceivedOutput::read` consumes bytes not produced by a trusted `Scanner` — the scan prompt's own threat model lists `ReceivedOutput::read` as a sink for untrusted bytes. The defect is unconditional (no validation exists), so exploitability reduces to whether attacker-influenced serialization reaches the reader in a given deployment. Severity Medium: it corrupts reported funds and can waste signing rounds/fees, but cannot directly extract the group key or forge a valid spend.

### Recommendation
Either eliminate the trust gap or restore the binding:
1. Re-derive the expected script inside `read`/`scan`: store or pass the scanner's base `key` and verify `p2tr_script_buf(key + GENERATOR * offset) == output.script_pubkey` before accepting a deserialized `ReceivedOutput` (mirrors the check `scan_transaction` performs via `self.scripts`).
2. Alternatively, treat `read` as trusted-persistence-only and never deserialize `ReceivedOutput`s from untrusted transports — enforce this by API boundary rather than by the type.

### Proof of Concept
```rust
// Attacker supplies bytes claiming receipt of a UTXO never paid to the wallet.
let mut buf = vec![];
// offset = arbitrary scalar, e.g. 0 (never registered)
buf.extend(Secp256k1::read_F… /* serialize Scalar::ZERO */ 0u32.to_le_bytes());
// output = any TxOut, e.g. one paying the attacker's own address
buf.extend(serialize(&attacker_txout));
buf.extend(serialize(&real_outpoint)); // any unspent outpoint
let forged = ReceivedOutput::read(&mut &buf[..]).unwrap(); // accepted
// Scanner would never have emitted this: attacker_txout.script_pubkey is not
// in `scripts`. Downstream treats `forged.value()` as received wallet funds;
// spending derives key = group_key + 0 ≠ controller of attacker_txout →
// invalid signature / unspendable reported output.
```

**Uncertainty note:** I could not fully trace whether a production component feeds attacker-influenced bytes into `ReceivedOutput::read` (versus only trusting `Scanner` output persisted locally). If `read` is only ever used on locally-generated trusted bytes, this reduces to a hardening gap rather than a reachable vulnerability, and no analog exists.

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
