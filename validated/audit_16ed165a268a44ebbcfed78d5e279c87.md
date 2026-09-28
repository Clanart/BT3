### Title
`ReceivedOutput` metadata is trusted blindly: fabricated prevout values/script data produce a `Prevouts::All`-committed signature that consensus rejects - (File: networks/bitcoin/src/wallet/send.rs)

### Summary
CVE-2024-25371 is an interface-mismatch bug: Gramine's software-level signal handling diverged from the hardware-level exception state, so the state the software believed in did not match ground truth. The analog in Serai's Bitcoin wallet is the same shape: the `SignableTransaction` treats the `TxOut` (amount + `script_pubkey`) carried inside each `ReceivedOutput` as ground truth about the actual on-chain prevout, and the Schnorr sighash commits to that claim via `Prevouts::All` — but nothing ever checks the claim against the real prevout. `ReceivedOutput::read` accepts untrusted bytes with no consistency check, so a mismatched software view of the prevout is committed into a BIP-341 signature, producing a transaction that is internally "valid" (`complete` returns `Ok`) yet rejected by Bitcoin consensus.

### Finding Description
`ReceivedOutput` bundles three attacker-influenceable fields: `offset`, `output` (`TxOut` with `value` and `script_pubkey`), and `outpoint` (`networks/bitcoin/src/wallet/mod.rs:90-97`). Its deserializer performs no validation whatsoever — it reads a scalar and two consensus-decoded structures and trusts them (`mod.rs:122-134`).

When a transaction is built, `SignableTransaction::new` uses these `TxOut`s verbatim for two independent purposes (`send.rs`):

1. **Economic accounting**: `input_sat` is summed from the claimed `output.value` (`send.rs:175`), gating `NotEnoughFunds` (`send.rs:215`), the change-amount computation (`send.rs:228-230`), and `fee()` (`send.rs:139-141`).
2. **Signature commitment**: the claimed `TxOut`s become `prevouts` (`send.rs:253`), which are committed into every input's taproot sighash via `Prevouts::All(&self.tx.prevouts)` (`send.rs:375-386`).

The only consistency check anywhere is in `multisig`, which verifies `p2tr_script_buf(offset.group_key()) == self.prevouts[i].script_pubkey` (`send.rs:277`) — i.e., it binds the *script* to the key, but never binds the `value` or the `outpoint` to any on-chain reality. There is no path that compares `prevouts[i]` to the actual UTXO at `outpoint`.

So an attacker who can feed bytes to `ReceivedOutput::read` (explicitly reachable per the threat model — untrusted serialized outputs) supplies a `ReceivedOutput` whose `script_pubkey`/`offset` pair is genuine (so `multisig` accepts) but whose `value` is inflated, or whose `outpoint` points at a different output paying to the same script. Every honest signer then runs `TransactionSignMachine::sign`, each sighash commits to the fabricated `TxOut`, shares verify against the verification shares (share verification checks the Schnorr equation, not chain state), and `complete` assembles and returns a transaction whose witnesses are invalid under consensus — BIP-341 key-path sighash binds the real prevout amount and script, which disagree with the fabricated ones.

### Impact Explanation
The scanner/wallet reports and operates on a prevout view that does not match the hardware-equivalent ground truth (the actual UTXO set). Concretely:

- With an inflated `value`, the signer set produces a fully "signed" transaction that cannot be broadcast (invalid sighash commitment), and the wallet simultaneously over-reports its spendable balance and mis-prices the fee/change (`send.rs:215-235`). A change output may be created sized against phantom input value, so even the change accounting is wrong.
- With a mismatched `outpoint`, the signed transaction spends a different output than the participants believe they authorized.

This is the Serai analogue of "SW signals vs HW exceptions": two representations of the same event (the `ReceivedOutput` claim vs. the real prevout) diverge, and the signature interface commits to the wrong one. It yields unspendable-in-practice output handling / permanently failing spends — an availability/integrity impact on funds the threshold group is responsible for, fitting a Medium rating (no key or nonce leakage; a signature for a fabricated sighash is a signature for a message that cannot exist on-chain, so no forgery is enabled).

### Likelihood Explanation
Reachability is direct: `ReceivedOutput::read` (`mod.rs:122-134`) is a public deserialization entry point for untrusted bytes (it round-trips via `serialize` for storage/transport), and the fabricated value flows unchecked through `SignableTransaction::new` → `TransactionSignMachine::sign` → consensus-invalid signature. Exploitation requires only that an attacker influence the serialized `ReceivedOutput` (or the local view of the UTXO set, e.g., a fabricated feed); no key material, no colluding signer, and no protocol-level trickery is needed. The missing check is structural, not probabilistic — it fails deterministically whenever the claim diverges from the chain.

Caveat on confidence: if every consumer in production always obtains `ReceivedOutput`s exclusively from `Scanner::scan_transaction` on locally validated blocks, the attack surface narrows to whoever can corrupt that local view. The `read` API existing as a public untrusted-bytes entry point (and the prompt's own reachability model) keeps this in scope.

### Recommendation
- In `SignableTransaction::new` or `multisig`, authenticate each `prevout` against the chain: either resolve `input.outpoint` to the confirmed `TxOut` at signing time, or require `ReceivedOutput` to carry/verify a proof that `output` is the real prevout at `outpoint`.
- At minimum, have the signing flow re-derive the sighash from independently fetched prevouts rather than from the claimed `TxOut` inside `ReceivedOutput`.
- Document that `ReceivedOutput::read` must only consume bytes that originated from a trusted scanner over a verified chain.

### Proof of Concept
```rust
// Conceptual; assumes a ThresholdKeys<Secp256k1> set with an even group key,
// and a real on-chain output O paying to p2tr_script_buf(key) with value V.

// 1) Scanner produces a genuine ReceivedOutput for O:
//    { offset: Scalar::ZERO, output: TxOut{ value: V, script_pubkey: p2tr(key) },
//      outpoint: (txid_O, vout_O) }
let real: ReceivedOutput = scanner.scan_transaction(&tx).remove(0);

// 2) Attacker tampers with the serialized form: keep script_pubkey and
//    outpoint identical, inflate value to V * 2.
let mut bytes = real.serialize();
// splice a crafted TxOut (value = 2*V, same script_pubkey) into `bytes`
let forged = ReceivedOutput::read(&mut tampered_bytes.as_ref()).unwrap();
// forged.offset() == real.offset(); forged.output().script_pubkey == real's
// but forged.value() == 2 * real.value()

// 3) Build the transaction. input_sat is computed from the fabricated value;
//    multisig() accepts because the script_pubkey still matches offset.group_key().
let stx = SignableTransaction::new(vec![forged], &payments, change, None, fee_rate).unwrap();
for (i, keys) in &keys_map {
    machines.insert(*i, stx.clone().multisig(keys).unwrap()); // succeeds
}

// 4) All honest signers sign. taproot_key_spend_signature_hash commits to
//    Prevouts::All containing the fabricated TxOut (value = 2*V).
let tx = sign(&keys_map, &stx); // complete() returns Ok(tx)

// 5) Broadcast: Bitcoin consensus rejects every input's signature —
//    BIP-341 hashes the *actual* prevout (value = V), not the claimed 2*V.
//    rpc.send_raw_transaction(&tx) -> "non-mandatory-script-verify-flag
//    (Invalid Schnorr signature)". The funds remain but the spend path fails,
//    and fee()/change were computed against phantom input value.
```

Supporting code locations: `networks/bitcoin/src/wallet/mod.rs:122-134` (`ReceivedOutput::read`, no validation), `networks/bitcoin/src/wallet/send.rs:175-176` (claimed values summed and offsets trusted), `send.rs:253` (claimed `TxOut`s become `prevouts`), `send.rs:273-285` (`multisig` checks only `script_pubkey` vs key), `send.rs:373-390` (sighash commits to the unverified `Prevouts::All`), `send.rs:417-425` (`complete` returns the consensus-invalid transaction as `Ok`).