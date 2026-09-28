### Title
`ReceivedOutput::read` accepts arbitrary outpoints/values with no proof the output belongs to the wallet, letting untrusted bytes register unspendable funds as received - (File: `networks/bitcoin/src/wallet/mod.rs`)

### Summary
Analogous to MainVault's `withdraw()` performing `transferFunds` to an attacker-chosen `_receiver` with no authorization check, `ReceivedOutput::read` deserializes an `(offset, TxOut, OutPoint)` triple from raw bytes with no check that the output was ever produced by `Scanner::scan_transaction`/`scan_block` — i.e., no check that the output's `script_pubkey` corresponds to `key + offset*G`, that the `outpoint` references a real transaction, or that the claimed `value` exists on-chain. The deserialized object is then trusted by the wallet pipeline as a spendable input.

### Finding Description
`ReceivedOutput` is the type the wallet uses to represent "funds we own". It is only legitimately constructed inside `Scanner::scan_transaction`, which matches `output.script_pubkey` against the registered key-derived scripts (`networks/bitcoin/src/wallet/mod.rs:205-211`). However, `ReceivedOutput::read` (`networks/bitcoin/src/wallet/mod.rs:122-134`) reconstructs the same type from a byte stream by simply decoding a scalar, a `TxOut`, and an `OutPoint` — with zero binding to the `Scanner`'s key or to the blockchain:

- `offset` is read via `Secp256k1::read_F` but never checked against `output.script_pubkey` (contrast with `register_offset`, which maintains the `scripts` map keyed by script).
- `output` is consensus-decoded, so the attacker controls both `value` and `script_pubkey`.
- `outpoint` is consensus-decoded with no existence/maturity check.

Downstream, `SignableTransaction::new` (`networks/bitcoin/src/wallet/send.rs:150-256`) consumes these `ReceivedOutput`s directly: it sums `input.output.value` for funding math (line 175), uses `input.outpoint` as the `TxIn` (line 180), stores `input.offset` for key derivation (line 176), and pushes `input.output` into `prevouts` (line 253) which is committed in the Taproot sighash via `Prevouts::All` (line 375).

The only validation at signing time is in `SignableTransaction::multisig` (line 277): `p2tr_script_buf(offset.group_key()) == prevouts[i].script_pubkey`. An attacker can trivially satisfy this by setting `script_pubkey` to the real vault script (publicly known) while fabricating the `outpoint` and `value` — analogous to how the MainVault attacker supplied a real `vaultCurrency` transfer path but an unauthorized `_receiver`. The FROST `TransactionMachine` will then drive all `t` signers to produce valid Schnorr signatures over `taproot_key_spend_signature_hash` for a transaction spending a nonexistent input, and `complete` returns a fully-signed, consensus-invalid `Transaction`.

### Impact Explanation
Any component ingesting `ReceivedOutput` bytes from a peer/queue (the explicitly allowed untrusted-bytes surface) will report funds as received that are not spendable: `value()`/`output()` return attacker-chosen amounts, balance accounting is inflated, and the coordinator can be induced to have the threshold group sign withdrawal transactions that can never confirm (nonexistent prevout), burning the group's one-shot preprocesses (nonce rotation) and blocking legitimate spends queued behind the same UTXO set. This matches the MainVault impact pattern: the missing check on *who/what authorizes* the input lets an unprivileged party drive the system to move (or attempt to move) assets it does not actually control.

### Likelihood Explanation
Reachable whenever serialized `ReceivedOutput`s transit an unauthenticated or attacker-influenceable channel (scanner output relayed between services, message queue, RPC-ingested bytes). The attacker needs only to know the vault's P2TR script (public once used) to satisfy the `multisig` script check. No key material, no valid signature, and no on-chain footprint is required — just bytes. Probability is bounded by whether deployments deserialize `ReceivedOutput` from untrusted sources rather than only from a locally-run `Scanner`, which the `read`/`write` API explicitly supports.

### Recommendation
Bind deserialized outputs to the wallet's authority, exactly as the MainVault report recommends binding withdrawals to authorized receivers:
1. Change `ReceivedOutput::read` (or add `Scanner::read_received_output`) to take the `Scanner`/group key and verify `p2tr_script_buf(key + offset*G) == output.script_pubkey` at deserialization, rejecting mismatches — moving the check currently in `multisig` to the trust boundary.
2. Require a confirmation proof (or at minimum defer crediting) before a `ReceivedOutput` is spendable: verify `outpoint` resolves to a confirmed UTXO with a matching `script_pubkey`/`value`, and exclude immature coinbase outputs (noted but unenforced at `mod.rs:218-220`).
3. Document that `ReceivedOutput` bytes are authorization-bearing and must only be accepted from the scanning component, or add an authentication tag (MAC/signature) to `write`/`serialize`.

### Proof of Concept
```rust
// Attacker knows the vault's P2TR script_pubkey (public).
let vault_script = scanner_scripts.iter().next().unwrap().clone(); // observed on-chain

// Fabricate a ReceivedOutput claiming 10 BTC at a fake outpoint,
// with a script_pubkey that passes the multisig() check.
let fake = ReceivedOutput {
    offset: Scalar::ZERO,                 // key + 0*G = group key -> vault_script
    output: TxOut { value: Amount::from_sat(1_000_000_000), script_pubkey: vault_script },
    outpoint: OutPoint::new(Txid::from_byte_array([0xAA; 32]), 0), // nonexistent
};
let bytes = fake.serialize();

// Victim side: deserialization performs NO ownership/existence check.
let claimed = ReceivedOutput::read(&mut &bytes[..]).unwrap();
assert_eq!(claimed.value(), 1_000_000_000); // "received" 10 BTC that don't exist

// It passes SignableTransaction::multisig's only check
// (p2tr_script_buf(key + offset) == script_pubkey), so the threshold group
// signs a taproot_key_spend_signature_hash spending a nonexistent input.
let tx = SignableTransaction::new(vec![claimed], &payments, Some(change), None, fee).unwrap();
// tx.multisig(&keys) returns Some(...); FROST signers produce valid Schnorr sigs;
// complete() returns a fully-signed Transaction that no node will accept,
// while balance accounting already credited 10 BTC.
```
Conceptual — depends on attacker-controlled bytes reaching `ReceivedOutput::read` (an allowed untrusted-input path) rather than outputs originating solely from a local `Scanner`.