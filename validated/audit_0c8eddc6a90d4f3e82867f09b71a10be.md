### Title
`ReceivedOutput::read` accepts fabricated deposits — inflated/unspendable "received" funds are passed through signing without binding the outpoint to on-chain data - (File: networks/bitcoin/src/wallet/mod.rs)

### Summary
Analogous to the Kraken bug (account credited for a deposit that never actually completed on-chain), `ReceivedOutput` decouples the claimed `TxOut` (value + script_pubkey) from the claimed `OutPoint` and never validates that the outpoint exists or carries that value. `ReceivedOutput::read` accepts fully attacker-controlled bytes, and the only check performed before the output is consumed for signing — `SignableTransaction::multisig` — verifies `p2tr_script_buf(offset.group_key()) == prevouts[i].script_pubkey`, i.e. only that the *script* matches our key. An unprivileged party can therefore fabricate a `ReceivedOutput` naming our known Taproot script_pubkey with an arbitrary `value` and arbitrary/nonexistent `OutPoint`, which is accepted as a received deposit.

### Finding Description
- `ReceivedOutput::read` (networks/bitcoin/src/wallet/mod.rs:122-134) deserializes `offset`, `output` (`TxOut` with attacker-chosen `value`), and `outpoint` independently, with no consistency check between them and no binding to chain data.
- `SignableTransaction::new` (send.rs:150-256) sums `input.output.value` directly into `input_sat` (send.rs:175) and uses the attacker-supplied `outpoint` as `previous_output` (send.rs:180).
- `multisig` (send.rs:273-285) checks only `p2tr_script_buf(offset.group_key())? != self.prevouts[i].script_pubkey` — a forged `ReceivedOutput` carrying our real script_pubkey satisfies this.
- `sign` then commits to `Prevouts::All(&self.tx.prevouts)` (send.rs:375), i.e. to the fabricated value, and produces valid FROST/BIP-340 signatures for a transaction referencing an input that does not exist or whose real value differs.

### Impact Explanation
Funds are reported as received that are not spendable: any downstream accounting/balance tracking built on `ReceivedOutput` (the documented serialization path exists precisely so scanned outputs can be transported between components) credits a deposit that never occurred — the exact Kraken "fabricated deposit credited" primitive. Additionally, `input_sat` inflation suppresses `NotEnoughFunds`, causes real co-spent inputs' value to be burned as fee (the fabricated input "covers" payments while actual inputs fund them and the difference becomes fee via `fee()` at send.rs:138-141), and yields a signed transaction that is invalid on-chain.

### Likelihood Explanation
Reachable by any unprivileged party able to supply bytes to `ReceivedOutput::read` (explicitly an untrusted-input surface): they need only the vault's publicly known P2TR script_pubkey, which is derivable from the group key / any observed deposit. No valid signature, key share, or validator collusion is required — the forgery passes every check in `new` and `multisig`.

### Recommendation
Bind `ReceivedOutput` to its provenance: either (a) make the type unforgeable by only constructing it inside `Scanner` (remove/seal the public `read` path or authenticate serialized outputs), or (b) before spending, verify each `outpoint` resolves on-chain to a `TxOut` exactly equal to `output` (value and script_pubkey) via `get_transaction`/`gettxout`, rejecting mismatches in `SignableTransaction::new` or `multisig`.

### Proof of Concept
```rust
// Attacker constructs bytes for ReceivedOutput::read:
//   offset = Scalar::ZERO (or any registered offset whose script is known)
//   output = TxOut { value: 1_000_000_000 /* fabricated */, script_pubkey: vault_p2tr_script }
//   outpoint = OutPoint { txid: arbitrary/nonexistent, vout: 0 }
let forged = ReceivedOutput::read(&mut &attacker_bytes[..]).unwrap();

// SignableTransaction::new accepts it: input_sat includes the fake 10 BTC,
// no NotEnoughFunds error is raised.
let stx = SignableTransaction::new(vec![real_input, forged], &payments, change, None, fee_rate).unwrap();

// multisig() passes: forged.output.script_pubkey == p2tr_script_buf(offset.group_key())
let machine = stx.multisig(&keys).unwrap();
// FROST sign completes, committing to Prevouts::All containing the fabricated value;
// the resulting signed tx is unspendable/invalid on-chain, yet the deposit was "received".
```