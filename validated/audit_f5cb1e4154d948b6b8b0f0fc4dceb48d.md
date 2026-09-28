### Title
Anyone can cause the wallet to report fabricated outputs as received funds via unauthenticated `ReceivedOutput::read` deserialization - (File: networks/bitcoin/src/wallet/mod.rs)

### Summary
`ReceivedOutput::read` deserializes a spendable-output claim (`offset`, `TxOut`, `OutPoint`) from raw bytes with no binding to the scanner, no verification that `output.script_pubkey` equals `p2tr(key + offset * G)` for the wallet's key, and no proof that `outpoint` actually exists on-chain. Any party able to feed bytes into this reader can cause arbitrary funds to be reported as received when they are not spendable — mirroring the reported "unprotected entrypoint lets anyone trigger the transfer logic" class, where the missing check is on who/what may populate the input the fund-moving code trusts.

### Finding Description
`ReceivedOutput` is the sole representation of "funds belonging to the wallet" consumed by `SignableTransaction::new` (`networks/bitcoin/src/wallet/send.rs:150-256`), which builds a transaction spending every supplied input, and `SignableTransaction::multisig` (`send.rs:273-285`), which drives the FROST signing machines that produce a valid Bitcoin transaction.

`ReceivedOutput::read` at `mod.rs:122-134` performs:

```rust
let offset = Secp256k1::read_F(r)?;
output = TxOut::consensus_decode(&mut buf_r)?;
outpoint = OutPoint::consensus_decode(&mut buf_r)?;
Ok(ReceivedOutput { offset, output, outpoint })
```

No consistency check exists anywhere:
- `offset` is any scalar — there is no requirement it was produced by `Scanner::register_offset` (whose doc comment at `mod.rs:168-179` itself warns that offsets must be securely generated and surjective).
- `output` is any `TxOut` — attacker-chosen `value` and `script_pubkey`.
- `outpoint` is any `OutPoint` — it need not reference a real UTXO.

Legitimate construction goes through `Scanner::scan_transaction`/`scan_block` (`mod.rs:199-227`), which derives `offset` from its own `scripts` map and takes `output`/`outpoint` from a real chain transaction. The `read` path bypasses all of that. `SignableTransaction::new` then happily counts `input.output.value` toward `input_sat` (`send.rs:175`), and `TransactionSignMachine::sign` (`send.rs:355-398`) produces threshold signature shares committing to `Prevouts::All(&self.tx.prevouts)` — i.e., the attacker-supplied `TxOut`s. The only downstream check, `multisig` comparing `p2tr_script_buf(offset.group_key())` to `prevouts[i].script_pubkey` (`send.rs:277`), can be trivially satisfied because the attacker controls both the offset and the TxOut bytes.

### Impact Explanation
An unprivileged party who can get crafted bytes to a `ReceivedOutput::read` call (a listed untrusted-bytes sink) can fabricate a received output claiming any value at any outpoint. Consequences, all reachable without any key material:

1. **Funds reported received that are not spendable**: a fabricated `ReceivedOutput` with a valid-appearing `script_pubkey` and a large `value` is counted in `input_sat`, letting `SignableTransaction::new` succeed and the coordinator commit a signing session to a transaction that can never be mined (nonexistent/unowned outpoint). Real received funds held alongside are effectively locked behind an unbroadcastable plan.
2. **Fee/value confusion**: since `fee()` and `needed_fee` derive from the attacker-chosen `output.value` sums (`send.rs:138-141`), fabricated inputs skew accounting, and the resulting transaction (if the outpoint happens to exist but pay someone else) wastes a signing attempt and burns nonce/attempt state.

This is a concrete "funds reported received that are not spendable" outcome, matching the validated impact class.

### Likelihood Explanation
The reader exists specifically to reconstruct `ReceivedOutput`s from serialized bytes (used in tests at `networks/bitcoin/tests/wallet.rs:73` and by the processor). Any channel that delivers peer/coordinator-supplied bytes into this deserialization — rather than outputs freshly produced by the local `Scanner` over verified blocks — exposes the flaw. It requires no validator status, no threshold collusion, and no key material; only the ability to supply bytes, which is exactly the unprivileged-reachability model of the source report.

### Recommendation
After deserialization, re-verify the claim before use:

- In `SignableTransaction::new`/`multisig` (or a `ReceivedOutput::verify` helper), assert `output.script_pubkey == p2tr_script_buf(wallet_key + GENERATOR * offset)`, and
- confirm `outpoint` resolves to a confirmed UTXO whose `TxOut` equals `output` (e.g., only accept `ReceivedOutput`s constructed by `Scanner` over verified blocks, or re-check via RPC `gettxout`/`get_transaction`).

This is the analog of the source recommendation ("require msg.sender is the fee recipient"): authenticate the provenance of the trigger rather than trusting supplied data.

### Proof of Concept
```rust
use bitcoin::{TxOut, OutPoint, Amount, ScriptBuf, hashes::Hash, Txid};
use k256::{Scalar, ProjectivePoint};
use frost::curve::Secp256k1;
use bitcoin_serai::wallet::{ReceivedOutput, SignableTransaction, p2tr_script_buf};

// Wallet key known to the attacker (public group key is public knowledge)
let wallet_key: ProjectivePoint = /* group key */;
let offset = Scalar::ZERO; // or any scalar giving an even key

// Fabricate a ReceivedOutput: script matches offset, outpoint is fake, value inflated
let mut buf = Vec::new();
ReceivedOutput {
    // fields are private, so craft via raw bytes:
};
// Equivalent raw bytes:
//   offset || TxOut { value: 1_000_000_000, script: p2tr(wallet_key + offset*G) } || bogus OutPoint
let fake = ReceivedOutput::read(&mut &bytes[..]).unwrap();

// This succeeds: input_sat counts the fake 10 BTC
let tx = SignableTransaction::new(
    vec![real_output, fake],   // real_output alone couldn't afford `payments`
    &payments, change, None, FEE,
).unwrap();

// multisig() passes (script check satisfied), signing machines produce shares,
// but the completed transaction is permanently unbroadcastable:
// the fake outpoint does not exist → every node rejects the tx.
// Funds are "reported received" yet not spendable.
```

The critical lines: `mod.rs:122-134` (`read` with no verification), `send.rs:175` (trusts `input.output.value`), `send.rs:375-389` (signs `Prevouts::All` including fabricated prevouts).