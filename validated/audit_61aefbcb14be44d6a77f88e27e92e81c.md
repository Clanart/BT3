### Title
`ReceivedOutput::read` accepts an attacker-claimed offset and arbitrary `script_pubkey`, forging "received" outputs that are not spendable - (File: networks/bitcoin/src/wallet/mod.rs)

### Summary
`ReceivedOutput::read` deserializes an offset scalar, a `TxOut`, and an `OutPoint` with no consistency check between them. Unlike `Scanner::scan_transaction`, which derives the offset by looking up the output's `script_pubkey` in a map keyed on the wallet's own scripts, `read` trusts whatever bytes it is fed. An unprivileged party can therefore feed bytes to `ReceivedOutput::read` that claim an arbitrary offset for an output whose `script_pubkey` does not correspond to `key + offset` (or to the wallet key at all), producing an authentic-looking `ReceivedOutput` for funds that were never received or cannot be spent — the direct analog of forging a `CardTopup` event for a payment that never occurred.

### Finding Description
`Scanner` maintains `scripts: HashMap<ScriptBuf, Scalar>` mapping each legitimate P2TR script (the tweaked key, and each registered offset key) to its offset. `scan_transaction` only yields a `ReceivedOutput` when `output.script_pubkey` is found in that map, and it fills `offset` from the map — so the invariant `output.script_pubkey == p2tr(key + offset)` always holds for scanned outputs (`mod.rs:199-214`).

`ReceivedOutput::read` (`mod.rs:122-134`) breaks that invariant:

```rust
pub fn read<R: Read>(r: &mut R) -> io::Result<ReceivedOutput> {
  let offset = Secp256k1::read_F(r)?;
  ...
  output = TxOut::consensus_decode(&mut buf_r)...;
  outpoint = OutPoint::consensus_decode(&mut buf_r)...;
  Ok(ReceivedOutput { offset, output, outpoint })
}
```

All three fields — the spend offset, the entire output (value and `script_pubkey`), and the claimed location — come straight from untrusted bytes. There is no `Scanner`/`key` parameter and no check that `self.scripts.get(&output.script_pubkey) == Some(offset)`, or even that `output.script_pubkey` is a registered P2TR script of this wallet at all.

The fields are also private with only getter methods (`mod.rs:99-118`), so the only way to construct a `ReceivedOutput` with an inconsistent `(offset, output)` pair is through `read` — meaning `read` is precisely where validation is required and absent.

### Impact Explanation
A `ReceivedOutput` is the wallet's record of "funds received, spendable by applying `offset` to the key". Downstream spend logic (`send.rs`) consumes `ReceivedOutput`s, applies `offset` to derive the signing key, and builds a transaction spending `outpoint`. A forged `ReceivedOutput` therefore reports funds as received and spendable that either (a) do not exist on-chain at `outpoint`, (b) exist but pay a `script_pubkey` not controlled by `key + offset`, or (c) exist but correspond to a different offset than claimed — so they are not spendable as recorded. This is exactly the accepted impact "funds reported received that are not spendable" and mirrors the report's forged `CardTopup` event driving backend accounting for a topup that never happened.

### Likelihood Explanation
Any path that accepts serialized `ReceivedOutput`s from an untrusted peer (e.g., a processor/coordinator channel shipping detected outputs to a signer, per the prompt's explicitly allowed `ReceivedOutput::read` reachability) can carry forged bytes. The attacker needs no keys, no threshold collusion, and no validator privileges — only the ability to supply bytes to `read`.

### Recommendation
Make `ReceivedOutput::read` take the `Scanner` (or the wallet key plus registered scripts) and re-derive the offset from `output.script_pubkey`, rejecting bytes whose `script_pubkey` is not registered or whose claimed `offset` does not match `self.scripts[&output.script_pubkey]`. At minimum, verify `p2tr_script_buf(key + offset) == Some(output.script_pubkey)` before returning `Ok`.

### Proof of Concept
```rust
// Given wallet key K and Scanner::new(K) registering script S0 = p2tr(K).
// Attacker serializes a forged ReceivedOutput:
let forged_offset = Scalar::ZERO;                       // claims the untweaked key
let fake_output = TxOut {
  value: Amount::from_sat(1_000_000),
  script_pubkey: ScriptBuf::new_p2tr_tweaked(
    TweakedPublicKey::dangerous_assume_tweaked(attacker_xonly) // attacker-controlled script
  ),
};
let fake_outpoint = OutPoint::new(Txid::from_byte_array([0xAA; 32]), 0);
// write forged_offset || consensus(fake_output) || consensus(fake_outpoint)
let received = ReceivedOutput::read(&mut &bytes[..]).unwrap();
// received.offset() == 0, received.value() == 1_000_000 sats,
// yet no coins exist at fake_outpoint and the script is not the wallet's.
// Scanner::scan_transaction on a real tx paying this script would never
// emit this ReceivedOutput, but read accepts it unconditionally.
```