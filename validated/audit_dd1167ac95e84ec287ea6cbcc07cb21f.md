### Title
`ReceivedOutput::read` accepts an attacker-controlled spend offset never checked against the output's `script_pubkey`, producing outputs the wallet cannot spend - (File: networks/bitcoin/src/wallet/mod.rs)

### Summary
The reported bug class is a security-relevant value that is parsed and stored but never enforced (the PraisonAI `--api-key` flag is parsed into `config` yet never wired into a check). The analog in Serai is `ReceivedOutput::read` in `networks/bitcoin/src/wallet/mod.rs`, which deserializes a spend `offset` scalar, a `TxOut`, and an `OutPoint` from untrusted bytes as three independent fields. The correctness-critical invariant — that `output.script_pubkey` equals the Taproot script derived from `scanner.key + G * offset` — is never checked anywhere in the deserialization path, even though `Scanner::scan_transaction` is the only code that ever produces a consistent `(offset, output)` pair.

### Finding Description
`Scanner` maintains `scripts: HashMap<ScriptBuf, Scalar>` mapping each registered script to the offset that derives it, and `scan_transaction` builds `ReceivedOutput` only by looking up `output.script_pubkey` in that map, so honest construction guarantees the offset spends the output [1](#0-0) . However, `ReceivedOutput::read` — an explicitly in-scope entry point for untrusted bytes — parses `offset`, `output`, and `outpoint` independently and stores them verbatim, with no consistency check [2](#0-1) . The offset is the value later used to derive the signing key for that UTXO (`pub fn offset()` exposes it for exactly this purpose [3](#0-2) ). Because the script-to-offset binding is dropped at the serialization boundary and never re-established on `read`, a forged blob pairs any victim `TxOut`/`OutPoint` (e.g. a real confirmed deposit, or an output paying a third party) with a scalar that does not derive its `script_pubkey` — the byte-level equivalent of an `--api-key` value that is stored in config but never consulted.

### Impact Explanation
A `ReceivedOutput` reconstructed via `read` is treated downstream exactly like a scanner-produced one: it reports a balance (`value()` returns `output.value` [4](#0-3) ) and the wallet will select it for spending using `offset()` to produce the signing key. If the offset does not derive the output's `script_pubkey` (which requires `p2tr_script_buf(key + G*offset) == output.script_pubkey`, a relation only `Scanner` can form [5](#0-4) ), the signed transaction's key-path spend fails BIP-341 verification and is rejected by the network — funds are reported received that are not spendable, one of the accepted impact classes. If the outpoint is fabricated entirely, the node may also attempt spends of non-existent UTXOs, burning fee negotiations/rebroadcast cycles.

### Likelihood Explanation
Reachability requires a path where a peer-fed byte stream reaches `ReceivedOutput::read` — precisely the untrusted-input class the audit scope enumerates. Any component that round-trips scanner results over an untrusted channel (e.g., output data relayed between processors or restored from coordinator-supplied storage) inherits this. The attacker needs no key material and no validator position: they just serialize a scalar, a `TxOut`, and an `OutPoint`. This is a Medium-severity availability/funds-integrity issue rather than key compromise.

### Recommendation
`ReceivedOutput::read` should re-derive the expected `script_pubkey` from the scanner's base key and the parsed offset (e.g., take the `Scanner`/`key` as an argument, or require callers to verify `scanner.scripts.get(&output.script_pubkey) == Some(&offset)` and reject otherwise). Serialization formats that cannot be self-verified should be documented as requiring this check, or the struct should be made unforgeable by keeping construction private to `Scanner`.

### Proof of Concept
```rust
// Conceptual: attacker-controlled bytes fed to ReceivedOutput::read
use bitcoin::{OutPoint, ScriptBuf, TxOut, Amount, Txid, hashes::Hash};
use k256::Scalar;
use networks_bitcoin::wallet::ReceivedOutput; // bitcoin-serai

// Attacker picks a real confirmed deposit output paying to the multisig script,
// but pairs it with a garbage offset that does not derive that script_pubkey.
let mut bytes = Vec::new();
bytes.extend(Scalar::from(0xdeadbeefu64).to_bytes());          // wrong offset
bytes.extend(bitcoin::consensus::encode::serialize(&TxOut {    // real victim output
    value: Amount::from_sat(100_000),
    script_pubkey: ScriptBuf::from_bytes(multisig_p2tr_script),
}));
bytes.extend(bitcoin::consensus::encode::serialize(&OutPoint::new(
    Txid::from_raw_hash(real_txid), 0,
)));

let output = ReceivedOutput::read(&mut bytes.as_slice()).unwrap();

// Downstream: wallet reports the funds and tries to spend using output.offset().
// The derived key key + G*0xdeadbeef does not equal the script_pubkey key,
// so the BIP-340 signature fails BIP-341 verification: the output is reported
// received yet is unspendable. No consistency check rejected the forged pairing.
```

Note: I did not have time to fully exhaust other candidates in scope (e.g., `encryption.rs` ECDH PoP handling, MuSig duplicate-encoding checks); this finding is the cleanest reachable analog of "parsed but never enforced" found in the inspected code.

### Citations

**File:** networks/bitcoin/src/wallet/mod.rs (L100-104)
```rust
  /// The offset for this output.
  pub fn offset(&self) -> Scalar {
    self.offset
  }

```

**File:** networks/bitcoin/src/wallet/mod.rs (L115-118)
```rust
  /// The value of this output.
  pub fn value(&self) -> u64 {
    self.output.value.to_sat()
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

**File:** networks/bitcoin/src/wallet/mod.rs (L180-196)
```rust
  pub fn register_offset(&mut self, mut offset: Scalar) -> Option<Scalar> {
    // This loop will terminate as soon as an even point is found, with any point having a ~50%
    // chance of being even
    // That means this should terminate within a very small amount of iterations
    loop {
      match p2tr_script_buf(self.key + (ProjectivePoint::GENERATOR * offset)) {
        Some(script) => {
          if self.scripts.contains_key(&script) {
            None?;
          }
          self.scripts.insert(script, offset);
          return Some(offset);
        }
        None => offset += Scalar::ONE,
      }
    }
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
