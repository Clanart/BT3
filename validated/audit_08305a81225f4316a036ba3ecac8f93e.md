### Title
`ReceivedOutput::read` trusts attacker-supplied prevout/outpoint/offset, letting `SignableTransaction::multisig` sign spends of arbitrary UTXOs - (File: networks/bitcoin/src/wallet/mod.rs)

### Summary
CVE-2017-10140 let a local user influence a privileged Postfix process through undocumented, attacker-controlled configuration (Berkeley DB `DB_CONFIG` read from the current directory). The Serai analog is `ReceivedOutput::read` in `networks/bitcoin/src/wallet/mod.rs` (lines 122–134): its serialized form is treated as trusted configuration. Every field that later determines *what gets signed* — the scalar `offset`, the full `TxOut` (`value` + `script_pubkey`), and the `OutPoint` — is taken verbatim from untrusted bytes and is never validated against the actual blockchain state anywhere in `SignableTransaction::new` / `multisig` (`networks/bitcoin/src/wallet/send.rs:150-285`).

### Finding Description
`ReceivedOutput::read` deserializes an offset scalar via `Secp256k1::read_F`, then a `TxOut` and `OutPoint` via Bitcoin consensus decoding (`networks/bitcoin/src/wallet/mod.rs:122-133`). The only consistency check before signing is in `SignableTransaction::multisig` (`send.rs:273-285`):

```rust
let offset = keys.clone().offset(self.offsets[i]);
if p2tr_script_buf(offset.group_key())? != self.prevouts[i].script_pubkey {
  None?;
}
```

This checks only that the claimed `script_pubkey` equals `p2tr(group_key + offset·G)`. An attacker who feeds bytes to `ReceivedOutput::read` controls `offset`, so they can make this check pass for *any* outpoint: they read the multisig's public group key (public), pick `offset = 0` (or any registered offset, which are derivable from the on-chain deposit scripts), set `script_pubkey` to that known script, and set `outpoint`/`value` to whatever they want — e.g., a real, unspent deposit UTXO owned by the multisig with its true value (all public chain data).

`TransactionSignMachine::sign` (`send.rs:355-398`) then computes `taproot_key_spend_signature_hash` with `Prevouts::All(&self.tx.prevouts)` — the attacker-supplied `TxOut`s — and signs each input with the offset view of the threshold key. Nothing verifies that the referenced outpoint exists, is unspent, or carries the claimed value.

### Impact Explanation
Because the sighash commits to `Prevouts::All` containing the attacker-forged `TxOut`s and to attacker-chosen `outpoint`s, the attacker obtains a valid threshold signature over an unintended message. Concretely: the attacker crafts a `ReceivedOutput` pointing at a genuine, high-value multisig UTXO (correct `script_pubkey` via chosen offset, correct `value`), supplies payments to their own address, and the multisig produces a fully valid, broadcastable Bitcoin transaction spending the multisig's funds to the attacker. Even where the forged prevout data doesn't match reality, the signer still emits a valid BIP-340/FROST signature over an attacker-composed sighash — concrete signing of an unintended message. This mirrors the CVE: undocumented trust placed in attacker-controlled "settings" (the serialized `ReceivedOutput`) inside a privileged signing path.

### Likelihood Explanation
Any party able to submit serialized `ReceivedOutput` bytes — e.g., an API/relayer input, a stored blob, or an untrusted UTXO descriptor — reaches this path with no key material required. The group key and all candidate `script_pubkey`s (`key + offset·G` for registered offsets) are public on-chain data, so constructing a passing `ReceivedOutput` is trivial. Requires an integrator that accepts serialized outputs from untrusted sources rather than producing them solely via `Scanner::scan_transaction`; the format's existence (`read`/`write`/`serialize` with no authenticity) invites exactly that use.

### Recommendation
Document that `ReceivedOutput::read` must only be applied to trusted bytes, or better, add authenticity: bind a `ReceivedOutput` to chain truth before signing — e.g., in `SignableTransaction::multisig`/`new`, re-derive each prevout from the outpoint via the node's UTXO view (or require the caller to supply a proof), and reject if the claimed `value`/`script_pubkey` doesn't match the confirmed UTXO. At minimum, verify each input's `outpoint` resolves to an unspent output paying the expected script and value.

### Proof of Concept
1. Observe the multisig's deposit script `S = p2tr_script_buf(group_key)` (public) and a confirmed UTXO `O = (txid, vout)` paying `S` with value `V`.
2. Serialize an attacker-crafted blob: `offset = Scalar::ZERO.to_bytes() || consensus_encode(TxOut{value: V, script_pubkey: S}) || consensus_encode(O)`.
3. Feed to `ReceivedOutput::read` → passes `multisig()`'s check at `send.rs:277` since `p2tr_script_buf(key + 0·G) == S`.
4. `SignableTransaction::new(vec![that], &[(attacker_script, V - fee)], None, None, fee_rate)` → `multisig()` → sign → complete yields a valid broadcastable transaction paying the attacker from UTXO `O`.