### Title
ReceivedOutput::read accepts untrusted (offset, TxOut) pairs with no binding between the offset and the script_pubkey, letting forged "received outputs" be reported as spendable Serai funds - (File: networks/bitcoin/src/wallet/mod.rs)

### Summary

The external report concerns a permissionless `withdraw(address token, ...)` that lets anyone make the contract call `transfer` on an arbitrary, unvalidated address. The analogous flaw in Serai's in-scope code is deserialization paths that accept attacker-supplied fields without validating that the fields are consistent with each other. `ReceivedOutput::read` reads an arbitrary `Scalar` offset, an arbitrary `TxOut`, and an arbitrary `OutPoint` from untrusted bytes, and returns them as a spendable output without ever checking that `output.script_pubkey` is the P2TR script derived from `key + offset * G`. The `Scanner` enforces this binding when it produces outputs (it only yields `ReceivedOutput`s whose `script_pubkey` is in the registered `scripts` map), but the `read` path bypasses that invariant entirely.

### Finding Description

`Scanner::scan_transaction` maintains the invariant that `offset` and `output.script_pubkey` are bound: an output is only emitted when `self.scripts.get(&output.script_pubkey)` yields a registered offset, i.e., `script_pubkey == p2tr_script_buf(key + offset * G)`. [1](#0-0) 

`ReceivedOutput::read`, however, deserializes the three fields independently from a byte stream with no cross-check:

```rust
// networks/bitcoin/src/wallet/mod.rs
pub fn read<R: Read>(r: &mut R) -> io::Result<ReceivedOutput> {
    let offset = Secp256k1::read_F(r)?;                       // arbitrary scalar
    output = TxOut::consensus_decode(&mut buf_r)?;            // arbitrary script_pubkey/value
    outpoint = OutPoint::consensus_decode(&mut buf_r)?;       // arbitrary outpoint
    Ok(ReceivedOutput { offset, output, outpoint })
}
``` [2](#0-1) 

Nothing requires `output.script_pubkey` to be a P2TR output to the key the offset implies, nothing requires the `outpoint` to reference a real transaction, and `value()` is trusted verbatim. Downstream consumers treat the `ReceivedOutput` as spendable under `key + offset * G` (e.g., the processor's `Output::key()` reconstructs the spend key by subtracting `GENERATOR * offset` from the script's x-only key, which only works if the binding held). [3](#0-2) 

### Impact Explanation

Like the liquidator's `withdraw`, this is an unvalidated-attacker-supplied-target issue: bytes an unprivileged party controls are trusted as a coherent spendable output. A crafted `ReceivedOutput` carrying a valid-looking offset plus a `TxOut` whose `script_pubkey` pays to the attacker (or to an unspendable/unknown key, or a non-P2TR script) will be accepted as Serai-owned funds. The result is funds reported received that are not spendable: any attempt to spend them produces a signature for `key + offset * G` that does not correspond to the UTXO's actual locking script, so the spend transaction is invalid on-chain. For malformed non-P2TR scripts, the downstream `key()` path panics instead of erroring. [4](#0-3) 

### Likelihood Explanation

Any context where `ReceivedOutput::read` is fed bytes not produced by `ReceivedOutput::write`/`Scanner::scan_transaction` on the honest path is reachable by an unprivileged party; the rules of engagement explicitly include untrusted bytes passed to `ReceivedOutput::read` as a valid attacker surface. The exploit needs only valid canonical scalar/consensus encodings, which are trivially satisfiable.

### Recommendation

Either perform the binding check at deserialization time — e.g., have `ReceivedOutput::read` take the expected `key`/`Scanner` (or the script map) and reject outputs whose `script_pubkey` does not equal `p2tr_script_buf(key + offset * G)` — or clearly scope `read` to trusted internal data and add a separate validating constructor for any externally supplied `ReceivedOutput`. At minimum, validate that `output.script_pubkey` is a v1 P2TR script before accepting the output.

### Proof of Concept

1. Compute `script_pubkey` for a key the attacker controls, or an unspendable script, e.g., `ScriptBuf::new_p2tr_tweaked(TweakedPublicKey::dangerous_assume_tweaked(attacker_xonly))` — or even a non-P2TR script.
2. Construct a `TxOut` with that `script_pubkey` and any `value`.
3. Serialize `Scalar::ZERO.to_bytes() || serialize(tx_out) || serialize(any_outpoint)` and feed it to `ReceivedOutput::read`.
4. `read` returns `Ok(ReceivedOutput)` which downstream code treats as spendable under the base key with `Scalar::ZERO` offset — but the reported value is either unspendable by Serai or causes a panic in `key()`, while `value()`/`balance()` reports the funds as received.

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
