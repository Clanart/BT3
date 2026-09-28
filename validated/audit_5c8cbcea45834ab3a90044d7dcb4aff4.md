### Title
`ReceivedOutput::read` accepts an arbitrary (offset, TxOut, outpoint) triple with no binding between the claimed scalar offset and the output's `script_pubkey` or any on-chain reality - ([File: networks/bitcoin/src/wallet/mod.rs](networks/bitcoin/src/wallet/mod.rs))

### Summary
The Tokemak bug class is: a cached/claimed value (the stale debt value, the swap's `minAmountOut = 0`) is trusted for accounting while the actual underlying value differs, letting an attacker pocket the difference. In `bitcoin-serai`, `ReceivedOutput::read` deserializes a `Scalar` offset, a `TxOut`, and an `OutPoint` from raw untrusted bytes and returns them as an authoritative "spendable output" claim — without ever checking that `key + offset*G` actually produces `output.script_pubkey`, that the outpoint exists, or that the claimed `value` matches the real UTXO. The declared fields are then consumed as fact by `SignableTransaction::new` for balance, fee, and change computation, and by callers of `ReceivedOutput::offset()`/`value()` for spendability.

### Finding Description
`ReceivedOutput::read` performs three bare consensus decodes and returns the struct unconditionally:

```rust
// networks/bitcoin/src/wallet/mod.rs
pub fn read<R: Read>(r: &mut R) -> io::Result<ReceivedOutput> {
  let offset = Secp256k1::read_F(r)?;
  output = TxOut::consensus_decode(&mut buf_r)...;
  outpoint = OutPoint::consensus_decode(&mut buf_r)...;
  Ok(ReceivedOutput { offset, output, outpoint })
}
```

Compare with the honest producer `Scanner::scan_transaction`, which only emits a `ReceivedOutput` after looking up `output.script_pubkey` in `self.scripts` — i.e., the offset is *proven* to correspond to the script. The `read` path drops that invariant entirely: any peer/supplied byte stream can claim "this UTXO worth X sats is spendable by group key + offset".

Downstream, `SignableTransaction::new` sums `input.output.value` to decide `NotEnoughFunds`, computes the change amount from the claimed `input_sat`, and stores the claimed `TxOut`s into `prevouts`, which are committed wholesale via `Prevouts::All(&self.tx.prevouts)` in `taproot_key_spend_signature_hash` at `TransactionSignMachine::sign`. The only consistency check is in `SignableTransaction::multisig`: `p2tr_script_buf(offset.group_key())? != self.prevouts[i].script_pubkey → None`. That check binds offset to script, but it (a) runs late, only when signing is attempted, and (b) still never verifies the outpoint exists or that the claimed `value` equals the real UTXO value.

An attacker who can feed crafted bytes to `ReceivedOutput::read` can therefore:

1. Report a deposit that doesn't exist (fake outpoint, real-looking script via registered/known offset): the wallet reports funds received that are not spendable — payments constructed against this phantom balance produce transactions that can never confirm, permanently pinning/DoS-ing the accounting and any queue built on it.
2. Report a real UTXO with an inflated `value` field: `Prevouts::All` commits to the forged amount, so the multisig produces a signature for a sighash that doesn't match the chain — the "received" funds are unspendable, and any change computation based on the inflated sum misallocates real co-inputs' value toward fee/change of an invalid TX.
3. Report a real output paying to the group script but pair it with a different real outpoint of a lower-value UTXO (value-substitution), causing `fee() = sum(prevouts) - sum(outputs)` to silently burn the difference as fees if the signature somehow verifies, or simply brick the spend.

Like the TOKE-28 pool, the system acts on a *claimed* value ("this output is ours and worth N") that was never synced/verified against the actual source of truth (the blockchain), and the excess/shortfall is absorbed by the victim's accounting.

### Impact Explanation
High-to-Medium. Any component that persists, relays, or aggregates `ReceivedOutput`s from untrusted bytes (peer messages, DB rows, API input) and then feeds them to `SignableTransaction::new`/balance reporting accepts attacker-forged claims of funds received. Result: funds reported received that are not spendable, transactions constructed that can never be validly signed/confirmed (since `Prevouts::All` commits to the forged TxOut), and mis-computed change/fee outputs — a direct analog of "withdraw at the stale valuation and skim the difference."

### Likelihood Explanation
Medium. Exploitation requires an attacker to control bytes passed to `ReceivedOutput::read` and for the consumer to act on the decoded object before an on-chain rescan. The format is a trivial concatenation (`offset || TxOut || outpoint`), so crafting is free; no key material, collusion, or validator status is needed — only the ability to supply the byte blob.

### Recommendation
Bind the offset to the output at deserialization time or expose a `ReceivedOutput::verify(key)` that recomputes `p2tr_script_buf(key + G*offset) == output.script_pubkey` and rejects otherwise; additionally require consumers to confirm `outpoint`/`value` against the chain (or a trusted UTXO lookup) before crediting balance, analogous to forcing a debt report before honoring a withdrawal. Do not treat a deserialized `ReceivedOutput` as evidence of spendable funds.

### Proof of Concept
```rust
// Attacker-controlled bytes; no on-chain output needed.
let mut buf = Vec::new();
// 1) Any registered/known offset (even Scalar::ZERO) so `multisig`'s
//    script check would pass against a script we choose:
buf.extend(Scalar::ZERO.to_bytes());                       // offset
// 2) A TxOut paying to the group's own P2TR script but with a fake value:
let fake = TxOut { value: Amount::from_sat(21_000_000_0000_0000u64.min(u64::MAX as u64) as u64.into()),
                   script_pubkey: p2tr_script_buf(group_key).unwrap() };
buf.extend(serialize(&fake));
// 3) A fabricated outpoint that exists nowhere:
buf.extend(serialize(&OutPoint::new(Txid::all_zeros(), 0)));

let forged = ReceivedOutput::read(&mut buf.as_slice()).unwrap();
assert_eq!(forged.value(), u64::MAX);            // wallet "received" u64::MAX sats
// SignableTransaction::new(vec![forged], &payments, change, None, fee)
//   -> passes NotEnoughFunds, emits change/fee math on phantom balance;
//   -> multisig() succeeds since script matches offset;
//   -> signature commits (Prevouts::All) to a nonexistent/inflated prevout,
//      producing a permanently unbroadcastable TX while balance was credited.
```