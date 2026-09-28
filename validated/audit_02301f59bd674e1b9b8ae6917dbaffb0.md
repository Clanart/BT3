### Title
Attacker-controlled payments to publicly derivable internal offset scripts are misclassified as protocol-owned outputs - ([File: networks/bitcoin/src/wallet/mod.rs](networks/bitcoin/src/wallet/mod.rs))

### Summary
The 3913 exploit works because the protocol's accounting (pair reserves) could be manipulated by an outsider directly moving tokens into privileged balances (`transfer` + `skim`/`burnPairs`), so the contract treated attacker-injected balances as its own. The analog in bitcoin-serai: `Scanner` matches outputs purely by `script_pubkey`, and the scripts for the *internal* offset types (`Branch`, `Change`, `Forwarded`) are publicly computable — the group key is public and the offsets are `Secp256k1::hash_to_F(KEY_DST, b"branch"|"change"|"forward")`. Any unprivileged party can send a Bitcoin transaction paying to those internal scripts, and it will be scanned and classified as a protocol-internal output rather than an external deposit, corrupting the accounting boundary between "user deposit" and "protocol-owned change/branch/forward output".

### Finding Description
`Scanner::scan_transaction` (networks/bitcoin/src/wallet/mod.rs:199-214) identifies owned outputs solely via `self.scripts.get(&output.script_pubkey)`, a `HashMap<ScriptBuf, Scalar>` keyed only on the script. `register_offset` (mod.rs:180-196) inserts `p2tr_script_buf(key + G*offset)` for whatever offsets the caller registers. In the consuming code (processor/src/networks/bitcoin.rs:308-347), the three internal offsets are deterministic public values:

```rust
register(OutputType::Branch,  Secp256k1::hash_to_F(KEY_DST, b"branch"));
register(OutputType::Change,  Secp256k1::hash_to_F(KEY_DST, b"change"));
register(OutputType::Forwarded, Secp256k1::hash_to_F(KEY_DST, b"forward"));
```

and classification is a pure lookup — `let kind = kinds[offset_repr_ref]` (bitcoin.rs:693-695) — with no check that the output actually originated from a protocol-constructed transaction. Note `p2tr_script_buf` uses `dangerous_assume_tweaked` on the x-only key (mod.rs:80-86), so the script is a plain BIP-341 key-path address anyone can derive offline given the public group key.

Like the deflationary-token attacker pushing tokens into the pair to distort reserves, an attacker pushes satoshis into a privileged balance bucket: the scanner reports them under `OutputType::Change`/`Branch`/`Forwarded` instead of `External`, so `presumed_origin`/`data` are still attached (bitcoin.rs:706-736 only suppresses `data` for non-External kinds; origin is set for all) and downstream handling treats them as internally generated outputs.

### Impact Explanation
Outputs classified as `Change`/`Branch`/`Forwarded` are assumed to be products of the multisig's own transactions. An attacker can therefore inject outputs into internal accounting flows — e.g., outputs treated as already-forwarded/branch funds — without going through the deposit path, at minimum miscrediting or inflating protocol-visible balances ("funds reported received" under a classification that asserts protocol origin which never occurred). This is the same bug class as the reference exploit: external injection into a balance the system trusts as its own, producing accounting/attribution divergence.

### Likelihood Explanation
Fully reachable by any unprivileged party: requires only sending a standard Bitcoin transaction to a publicly derivable address. No collusion, no leaked keys, no malicious validator. The cost is only the donated amount plus fees. Frequency of harm depends on how the consumer reacts to misclassified kinds, but the injection primitive is unconditional.

### Recommendation
Do not classify scanned outputs by `script_pubkey` alone for internal kinds. Track which outpoints/txids the protocol itself constructed (or bound the offset registration to expected transaction templates) and only accept `Branch`/`Change`/`Forwarded` outputs from protocol-originated transactions; classify any other payment to those scripts as `External` (or reject it). At minimum, document that internal offset addresses must never be paid by external parties and add an explicit provenance check in `get_outputs`.

### Proof of Concept
```rust
// Given the public multisig group key `key: ProjectivePoint`:
let branch  = Secp256k1::hash_to_F(b"Serai Bitcoin Output Offset", b"branch");
let change  = Secp256k1::hash_to_F(b"Serai Bitcoin Output Offset", b"change");
let forward = Secp256k1::hash_to_F(b"Serai Bitcoin Output Offset", b"forward");

// Attacker recomputes the parity adjustments Scanner::register_offset applies
// (increment until p2tr_script_buf returns Some), derives the internal
// script_pubkeys, and broadcasts a normal tx paying to one of them.
let internal_script = p2tr_script_buf(key + (ProjectivePoint::GENERATOR * adjusted_change)).unwrap();
// ... build & broadcast tx with TxOut { script_pubkey: internal_script, value } ...

// Scanner::scan_transaction returns it as a ReceivedOutput with the Change offset;
// get_outputs() then maps it to OutputType::Change — an "internal" output that the
// protocol never created, indistinguishable from its own change.
```

Root cause: `Scanner` (networks/bitcoin/src/wallet/mod.rs:153-214) keys ownership purely on `script_pubkey`, and deterministic public offsets (`processor/src/networks/bitcoin.rs:324-345`) make those scripts attacker-derivable, while `get_outputs` (bitcoin.rs:686-700) trusts the offset→kind mapping without verifying protocol origin.