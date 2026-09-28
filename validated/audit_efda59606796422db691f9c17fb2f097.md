### Title
`ReceivedOutput::read` accepts attacker-controlled `offset`/`TxOut`/`OutPoint` triplets without validating that the offset actually derives the output's `script_pubkey`, letting an unprivileged party inject unspendable or foreign outputs as "received" - ([File: networks/bitcoin/src/wallet/mod.rs](networks/bitcoin/src/wallet/mod.rs#L120-L134))

### Summary
Analogous to `__OnlyOpenQ_init` storing an unvalidated, attacker-supplied address as the privileged `OpenQProxy`, `ReceivedOutput::read` in `networks/bitcoin/src/wallet/mod.rs` stores three unvalidated attacker-supplied fields — an `offset` scalar, a `TxOut`, and an `OutPoint` — as a "spendable output belonging to us." Just as the missing check let Mallory substitute a key controlling the `onlyOpenQ` gate, the missing check here lets a malicious peer hand the wallet a serialized blob claiming "this outpoint is ours, spendable with `key + offset`," when the offset does not actually map the group key onto `output.script_pubkey` — or when the output pays someone else entirely.

### Finding Description
`ReceivedOutput` is the wallet's record that a given on-chain output is spendable under `group_key + offset * G` (see `ReceivedOutput::offset`, `output`, `outpoint` accessors at lines 99–118). Legitimate `ReceivedOutput`s are produced only by `Scanner::scan_transaction`, which derives them from `self.scripts` — a map built by `Scanner::new`/`register_offset` that binds each registered `script_pubkey` to the correct `offset` (lines 163–214). That derivation is the *only* place the invariant `p2tr_script_buf(key + offset*G) == output.script_pubkey` is established.

`ReceivedOutput::read` (lines 122–134) decodes all three fields straight from the reader:

```rust
pub fn read<R: Read>(r: &mut R) -> io::Result<ReceivedOutput> {
  let offset = Secp256k1::read_F(r)?;
  let output = TxOut::consensus_decode(&mut buf_r)...?;
  let outpoint = OutPoint::consensus_decode(&mut buf_r)...?;
  Ok(ReceivedOutput { offset, output, outpoint })
}
```

No consistency check is performed: the `offset` is never verified against `output.script_pubkey`, and the `outpoint`/`output` pair is never verified against chain data. Any byte source that feeds `ReceivedOutput::read` — an untrusted peer relaying detected outputs, a deserialized cache — can inject arbitrary `(offset, TxOut, OutPoint)` claims. A real fix exists in-scope: the scanner already maintains the authoritative `scripts` map, so deserialization should route through a `Scanner`-aware check that `output.script_pubkey` is a registered script and `scripts[script_pubkey] == offset`.

### Impact Explanation
An attacker who can feed bytes to `ReceivedOutput::read` causes the wallet to report funds as received that are not spendable — the accepted impact class. Concretely:

* **Wrong offset, real output**: attacker flips the `offset` scalar on a legitimately received output. The wallet reports the UTXO, but `keys.offset(offset)` in `send.rs` produces a key that does not control `script_pubkey`, so the FROST signature is invalid and the spend fails permanently without re-scanning.
* **Foreign output**: attacker encodes a `TxOut` paying to an unrelated `script_pubkey` plus any `offset`. The wallet reports it as a received, spendable output it cannot ever sign for — phantom balance, and any attempted spend produces an invalid transaction.

This is exactly the OpenQ failure mode — a security-meaningful binding ("this address is the privileged OpenQ" / "this offset spends this output") accepted without validation — realized on Serai's own untrusted-input surface.

### Likelihood Explanation
Reachability is limited to paths where serialized `ReceivedOutput`s cross a trust boundary (peer-relayed outputs, restored/rewritten state files). Since the scanner itself only emits correct entries, exploitation requires the integrator to deserialize outputs from a source other than its own honest scanner — plausible for any deployment sharing scan results between components or loading persisted state. The attack needs no keys, no collusion, and only `Secp256k1::read_F`-decodable bytes; a single crafted blob per poisoned output.

### Recommendation
Validate the binding at deserialization time, mirroring the `require(_initalOpenQ != address(0))` fix:

* Change `ReceivedOutput::read` to take a `&Scanner` (or the group's `key`), and after decoding `offset`/`output`, recompute `p2tr_script_buf(key + GENERATOR * offset)` and require it equals `output.script_pubkey`, rejecting otherwise. Also verify `scripts.get(&output.script_pubkey) == Some(offset)`.
* Alternatively, keep `read` raw but add a documented `verify(&Scanner)` and audit all callers to invoke it before the output enters spend/queue logic.

### Proof of Concept
```rust
// Attacker crafts bytes claiming an output is spendable by the group.
let mut buf = vec![];
buf.extend(Scalar::from(0xdeadbeefu64).to_bytes()); // wrong offset
buf.extend(serialize(&TxOut { value: Amount::from_sat(1_000_000),
  script_pubkey: victim_script }));                 // pays to someone else
buf.extend(serialize(&OutPoint::new(real_txid, 0))); // real outpoint on chain

let forged = ReceivedOutput::read(&mut buf.as_ref()).unwrap();
// `forged` is accepted; wallet reports 1 BTC received.
// Spending via keys.offset(forged.offset()) produces a signature
// that fails BIP-340 verification: funds reported received are not spendable.
```

Confidence caveat: I verified `ReceivedOutput::read` performs no consistency validation and that the `Scanner` holds the authoritative offset↔script binding, but I did not trace `send.rs`'s consumption path end-to-end (iteration limit reached). The exploit requires an integrator path where `ReceivedOutput` bytes cross a trust boundary — if none exists in this snapshot, the impact reduces to a stored-state integrity issue and severity drops accordingly.