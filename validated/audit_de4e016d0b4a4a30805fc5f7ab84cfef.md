### Title
`ReceivedOutput::read` accepts an offset/script pairing it never verifies, letting corrupted serialized outputs be treated as spendable — (File: `networks/bitcoin/src/wallet/mod.rs`)

### Summary
`ReceivedOutput` couples a scalar `offset` with a `TxOut`/`OutPoint`. The only place consistency between them is established is `Scanner::scan_transaction`, which inserts outputs whose `script_pubkey` was derived from `key + offset·G`. `ReceivedOutput::read` deserializes all three fields from untrusted bytes but performs no check that `p2tr_script_buf(key + offset·G) == output.script_pubkey` — or any integrity check at all. This is the analog of Tahoe-LAFS CVE-2012-0051 (mutable data returned on retrieval without integrity verification): a retrieval path hands back corrupted-but-accepted data.

### Finding Description
In `ReceivedOutput::read` (`networks/bitcoin/src/wallet/mod.rs:122-134`), the `offset` is read via `Secp256k1::read_F` (canonical scalar check only), then `TxOut` and `OutPoint` are consensus-decoded. Nothing binds `offset` to `output.script_pubkey`:

```rust
// networks/bitcoin/src/wallet/mod.rs:122-134
pub fn read<R: Read>(r: &mut R) -> io::Result<ReceivedOutput> {
  let offset = Secp256k1::read_F(r)?;
  ...
  output = TxOut::consensus_decode(&mut buf_r)...;
  outpoint = OutPoint::consensus_decode(&mut buf_r)...;
  Ok(ReceivedOutput { offset, output, outpoint })
}
```

Contrast with `Scanner::scan_transaction` (`wallet/mod.rs:199-214`), which only ever produces `ReceivedOutput`s whose offset was the registered preimage of that exact `script_pubkey`. Downstream code trusts this invariant:

- `Output::key()` (`processor/src/networks/bitcoin.rs:112-122`) recovers the owning group key as `script_key − offset·G`. With a mismatched offset this silently yields a *different* key rather than an error.
- The spend path re-keys `ThresholdView`/commitments by `output.offset()` when building the sighash commitments, so a corrupted offset produces a signature under the wrong tweaked key — invalid for the actual on-chain output.

### Impact Explanation
An attacker who can feed crafted bytes to `ReceivedOutput::read` (e.g., a corrupted/malicious serialized output record) causes funds to be reported as received under the multisig while being unspendable by it: the stored offset does not correspond to the output's script key, so every spending attempt produces an invalid BIP-340 signature. Alternatively, an offset can be paired with a script the attacker chose, poisoning the spendable-output set and causing the signer to construct transactions spending nonexistent or misattributed outpoints. Either way, integrity of retrieved mutable data (the output record) is never enforced — the exact failure mode of the reference advisory.

### Likelihood Explanation
Medium. Exploitation requires the attacker to control or corrupt the serialized bytes reaching `ReceivedOutput::read`, which in deployment are normally locally stored scanner results — but the type explicitly exposes a public deserialization API for untrusted input, and the fix (a binding check) is trivial. When reachable, the impact is loss of fund availability, matching the Medium severity of the source advisory.

### Recommendation
Either reject mismatched pairs in `ReceivedOutput::read` (requires passing the expected base key), or — cleaner — make the constructor private-ish and add `ReceivedOutput::new(offset, output, outpoint, key)` that asserts `p2tr_script_buf(key + offset·G) == output.script_pubkey`, having `read` call it. Downstream `Output::key()` can then remain a pure accessor rather than a silent re-derivation of an unverified relationship.

### Proof of Concept
```rust
use bitcoin_serai::wallet::{ReceivedOutput, Scanner, p2tr_script_buf};
use k256::{ProjectivePoint, Scalar};
use bitcoin_serai::bitcoin::{TxOut, OutPoint, Amount, ScriptBuf};

// Construct an output honestly scanned for offset `s`
let key = ProjectivePoint::GENERATOR; // even-Y assumption simplified for PoC
let s = Scalar::from(5u64);
let mut scanner = Scanner::new(key).unwrap();
let s_used = scanner.register_offset(s).unwrap();
let script = p2tr_script_buf(key + ProjectivePoint::GENERATOR * s_used).unwrap();

// Attacker crafts a serialized ReceivedOutput pairing that script with a DIFFERENT offset
let evil_offset = s_used + Scalar::ONE; // or any other scalar
let mut buf = vec![];
buf.extend(evil_offset.to_bytes());
// consensus-serialize TxOut { value, script_pubkey: script } and any OutPoint
// ... append serialized TxOut/OutPoint ...

let ro = ReceivedOutput::read(&mut buf.as_slice()).unwrap();
// Accepted: no error despite offset/script mismatch.
// ro.output().script_pubkey == script, but ro.offset() != s_used,
// so key() = script_key - evil_offset*G != key, and spends sign under
// the wrong tweaked key -> permanently unspendable reported funds.
``` [1](#0-0) [2](#0-1) [3](#0-2)

### Citations

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
