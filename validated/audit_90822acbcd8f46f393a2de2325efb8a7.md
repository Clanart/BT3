### Title
Unvalidated `ReceivedOutput` bytes reach `SignableTransaction`/`multisig`, letting an untrusted party get the threshold group to sign arbitrary Bitcoin spends - (File: networks/bitcoin/src/wallet/send.rs)

### Summary

`ReceivedOutput::read` deserializes attacker-controlled `offset`, `TxOut`, and `OutPoint` with no validation at any hop (`networks/bitcoin/src/wallet/mod.rs:122-134`). `SignableTransaction::new` consumes these raw values to build the transaction (`send.rs:150-256`), and `SignableTransaction::multisig` only checks that `p2tr_script_buf(offset.group_key())` equals the attacker-supplied `script_pubkey` (`send.rs:273-285`). Since the base (untweaked, offset `Scalar::ZERO`) key's p2tr script is publicly known, an unprivileged party can feed fabricated `ReceivedOutput`s referencing real Serai-owned UTXOs with `offset = 0`, pair them with payments to themselves, and obtain valid threshold signatures spending vault funds. Like the PraisonAI bug, untrusted input crosses every hop to a privileged sink (here: `taproot_key_spend_signature_hash` + FROST `sign_share`) with no authorization check.

### Finding Description

The chain is:

- **Source**: `ReceivedOutput::read` reads `offset` (`Secp256k1::read_F`), `TxOut`, and `OutPoint` from an untrusted reader, returning them verbatim — no check that the offset corresponds to the output, that the outpoint exists, or that the output belongs to this wallet (`mod.rs:122-134`).
- **Hop**: `SignableTransaction::new` copies `input.offset` into `offsets` and `input.output` into `prevouts` and into the transaction's inputs (`send.rs:175-185`, `send.rs:252-253`). `payments` — the destination scripts/amounts — are likewise caller-controlled (`send.rs:187-191`).
- **Gate that fails to help**: `multisig` verifies `p2tr_script_buf(keys.offset(offsets[i]).group_key()) == prevouts[i].script_pubkey` (`send.rs:276-279`). This only binds the *declared* script to the *declared* offset; both come from the same attacker input. For any UTXO paying Serai's base tweaked key, `offset = 0` satisfies it — and `Scanner::new` registers exactly `offset: Scalar::ZERO` for the base script (`mod.rs:164`), confirming `0` is the canonical offset for ordinary deposits.
- **Sink**: `TransactionSignMachine::sign` computes `taproot_key_spend_signature_hash` over `Prevouts::All(&prevouts)` and feeds it into each `AlgorithmSignMachine::sign` → `Schnorr::sign_share` → FROST signature share (`send.rs:373-395`). `TransactionSignatureMachine::complete` then assembles valid BIP-340 witnesses (`send.rs:417-425`).

Nothing in this path authenticates that the spend was authorized — it trusts that whoever constructed the `SignableTransaction` was honest, the same flaw as `parse_mcp_command` trusting the `--mcp` string.

### Impact Explanation

Any component that builds a `SignableTransaction` from `ReceivedOutput`s influenced by untrusted bytes (deserialized via `ReceivedOutput::read`, the explicit reachable-API surface) lets an attacker cause the threshold group to produce a fully valid, broadcastable Bitcoin transaction sending real Serai-controlled UTXOs to attacker-chosen `payments` — theft of funds via a signature on an unintended message. The attacker only needs to know an on-chain outpoint paying to Serai's deposit address (public blockchain data) and supplies `offset = 0`.

### Likelihood Explanation

Reachability is explicitly granted: `ReceivedOutput::read` is listed as an untrusted-input sink. The forgery requires no secret material — offset `0` and the real UTXO's `TxOut`/`OutPoint` are public knowledge. Exploitation requires a pipeline that signs transactions from externally supplied inputs, which is precisely the wallet flow this API is built for. Severity: High-to-Critical (direct fund loss), matching the "concrete signing of an unintended message" acceptance bar.

### Recommendation

Bind inputs to authenticated provenance before signing: only accept `ReceivedOutput`s produced by `Scanner::scan_transaction`/`scan_block` over confirmed blocks, never deserialized untrusted bytes. If deserialization must exist, require the caller to re-verify via `Scanner` that `output.script_pubkey` is a registered script and that `offset` is the scanner-registered value for it, and confirm `outpoint` resolves to that exact `TxOut` on-chain (SPV/full-node check) before constructing `SignableTransaction`. Additionally, the destination `payments`/`change`/`data` should be bound by policy (e.g., signed `OutInstruction`) at the same layer that authorizes signing.

### Proof of Concept

```rust
// Networks/bitcoin crate, feature "std".
// Assumption: a pipeline deserializes attacker bytes via ReceivedOutput::read
// and feeds them to SignableTransaction::new -> multisig -> sign.

// 1. Attacker observes a deposit UTXO on-chain paying to Serai's base tweaked
//    key (the script Scanner::new registers at offset 0). They know:
//      - real_outpoint (txid, vout)   -- public
//      - real_txout    (value, script_pubkey == p2tr(tweaked group key)) -- public
// 2. Attacker serializes a forged ReceivedOutput:
//      offset   = Scalar::ZERO bytes
//      output   = real_txout consensus encoding
//      outpoint = real_outpoint consensus encoding
// 3. Victim deserializes:
let stolen = ReceivedOutput::read(&mut attacker_bytes).unwrap();
// 4. Attacker proposes payments to their own address:
let payments = &[(attacker_script, stolen.value() - fee)];
let signable = SignableTransaction::new(
  vec![stolen], payments, None, None, fee_per_vbyte,
).unwrap();
// 5. multisig's check passes: offset 0 -> tweaked group key -> script equals
//    the copied script_pubkey.
let machine = signable.multisig(&tweaked_keys).unwrap();
// 6. Normal FROST ceremony runs; TransactionSignatureMachine::complete
//    returns a valid transaction paying attacker's script from Serai's UTXO.
```