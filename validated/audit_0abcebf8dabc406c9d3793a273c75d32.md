### Title
Scanner classifies outputs by script_pubkey alone, letting any external sender mislabel deposits as internal Branch/Change/Forwarded outputs - (File: processor/src/networks/bitcoin.rs)

### Summary
The analog to "fees/accounting applied to the wrong actor" in Serai is output-kind attribution. `Scanner::scan_transaction` matches outputs purely by `script_pubkey`, and `get_outputs` maps the matched offset to `OutputType::{External, Branch, Change, Forwarded}` without checking who created the output. Since the Branch/Change/Forwarded addresses are deterministically derived (`Secp256k1::hash_to_F(KEY_DST, b"branch"|"change"|"forward")` added to the group key), any unprivileged party can craft a Bitcoin transaction paying to, e.g., the Change address. Serai then reports those externally-sent funds as `OutputType::Change` — internal bookkeeping funds — instead of an external deposit, corrupting accounting the same way the report's fee was pulled from the wrong account.

### Finding Description
In `networks/bitcoin/src/wallet/mod.rs:199-214`, `scan_transaction` returns a `ReceivedOutput` whenever `output.script_pubkey` is in `self.scripts`, which contains the group key's P2TR script plus one script per registered offset. In `processor/src/networks/bitcoin.rs:686-736`, `get_outputs` maps `output.offset()` through `kinds` to an `OutputType` (`processor/src/networks/bitcoin.rs:314-346`). The offsets for `Branch`, `Change`, and `Forwarded` are fixed, publicly computable scalars (`hash_to_F(KEY_DST, "branch")` etc., possibly incremented to the next even-Y point per `register_offset`, `wallet/mod.rs:180-196`). Nothing distinguishes a transaction Serai itself constructed (legitimate change/branch flows) from an arbitrary third-party transaction paying to the same script. `presumed_origin` is recorded (`processor/src/networks/bitcoin.rs:707-735`) but the `kind` field — which drives downstream handling — is attacker-controlled.

### Impact Explanation
A remote, unprivileged Bitcoin user can send funds to the Change/Branch/Forwarded addresses and have them internally classified as protocol-internal outputs rather than `OutputType::External` deposits. Depending on how the processor routes each `OutputType` (branch outputs feed the `PostFeeBranch` reconciliation in `processor/src/networks/mod.rs:504-517`), this misattribution can cause funds to be credited under the wrong category — e.g., counted as change the protocol "already owns" rather than a user deposit that must trigger an `InInstruction` — leaving the depositor uncredited while the coins sit at an address the protocol controls. This is the same class of bug as the report: an internal party/step was made to stand in for the true external actor, breaking correct fund attribution.

### Likelihood Explanation
The attack requires only sending a standard Bitcoin transaction to a publicly derivable address — no collusion, no threshold compromise, no malformed encodings. Whether it rises above Medium depends on the processor's `OutputType` handling, which sits outside the in-scope crates; conservatively this is a reachable accounting-integrity issue.

### Recommendation
Treat `OutputType::{Branch, Change, Forwarded}` as authoritative only for transactions whose full shape the scheduler itself planned (match by txid/plan), or require corroborating evidence — e.g., that the output's position and sibling outputs match an expected internal transaction template — before assigning a non-`External` kind. At minimum, outputs at internal offsets arriving in transactions not originated by the protocol should be quarantined for review rather than silently classified.

### Proof of Concept
```rust
// regtest: derive Serai's internal offsets exactly as scanner() does
let key = keys.values().next().unwrap().group_key();
let mut scanner = Scanner::new(key).unwrap();
let change_offset = scanner
    .register_offset(Secp256k1::hash_to_F(b"Serai Bitcoin Output Offset", b"change"))
    .unwrap();
let change_script = p2tr_script_buf(key + ProjectivePoint::GENERATOR * change_offset).unwrap();

// Attacker (any Bitcoin wallet) sends funds to the change script
let attacker_tx = /* tx paying `change_script`, txid A */;

// Serai's get_outputs classifies it as internal change:
//   kinds[offset_repr] == OutputType::Change
// instead of an External deposit, even though the protocol never built tx A.
let outputs = bitcoin.get_outputs(&block_containing(attacker_tx), key).await;
assert_eq!(outputs[0].kind, OutputType::Change); // misattributed attacker funds
```

Relevant code: `networks/bitcoin/src/wallet/mod.rs:180-214` (offset registration, script_pubkey-only matching), `processor/src/networks/bitcoin.rs:308-346` (deterministic offset derivation), `processor/src/networks/bitcoin.rs:686-736` (kind assignment without provenance check).