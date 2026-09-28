### Title
`ReceivedOutput::read` accepts an offset inconsistent with the output's `script_pubkey`, causing unspendable funds to be reported as spendable - (File: networks/bitcoin/src/wallet/mod.rs)

### Summary

The referenced bug is a privileged path (`arbitraryCall`) gated only by a staleness-prone check (`incentives[who] == 0`), letting residual authority (an ERC-20 `allowance`) be exercised against an arbitrary target that was never re-validated. The structural analog in Serai's in-scope code is `ReceivedOutput::read` in `networks/bitcoin/src/wallet/mod.rs`: it deserializes an attacker-controlled `offset` and an attacker-controlled `TxOut`/`OutPoint` independently, with no check that `key + offset·G` actually maps to `output.script_pubkey` — i.e., no re-validation that the claimed authorization (the offset that will re-key the spend) corresponds to the funds claimed. The scanner that produces `ReceivedOutput`s legitimately establishes this invariant in `scan_transaction` (mod.rs:199-214), but the deserialization boundary drops it, mirroring how `arbitraryCall` dropped the "active incentive" invariant once `incentives[who]` became 0.

### Finding Description

`ReceivedOutput` is the type downstream wallet/send code treats as "an output spendable by `group_key + offset·G`." Its `read` implementation (networks/bitcoin/src/wallet/mod.rs:122-134) parses three fields independently:

```rust
let offset = Secp256k1::read_F(r)?;
output = TxOut::consensus_decode(&mut buf_r)?;
outpoint = OutPoint::consensus_decode(&mut buf_r)?;
```

There is no consistency check that `p2tr_script_buf(scanner.key + GENERATOR * offset) == output.script_pubkey`, nor that `outpoint` references an output whose script matches. The honest producer, `Scanner::scan_transaction` (mod.rs:199-214), derives `offset` from its `scripts` map keyed by `script_pubkey`, so internally-produced values are consistent — but nothing enforces this on the untrusted-bytes path, exactly like `arbitraryCall` trusting that `who` was still a non-incentive address.

Because offsets are surjective, not bijective (documented at mod.rs:174-175, with `register_offset` incrementing past odd points at mod.rs:184-193), the offset→script relation is context-dependent and cannot be assumed by a parser. A crafted `ReceivedOutput` pairs a real, confirmed deposit's `outpoint`/`TxOut` with an unrelated scalar offset.

### Impact Explanation

Downstream, the output is treated as spendable under `key + offset·G` and fed into transaction construction (`send.rs`). Two failure modes:

1. **Funds reported received that are not spendable**: if `key + offset·G` doesn't match the output's taproot key, any spend built on this `ReceivedOutput` produces an invalid/unsigned-able input — the protocol accounts the deposit as spendable liquidity while it is not, stalling the signing pipeline or the batch it was included in.
2. **Misattributed deposits**: with a mismatched offset, the same on-chain output is reported under the wrong offset label, corrupting offset↔deposit accounting.

This is a Medium-severity analog: it requires the consumer to accept serialized `ReceivedOutput`s from an untrusted/transit source rather than only from its own `scan_transaction`, but it is directly reachable via the listed `ReceivedOutput::read` sink with purely public inputs.

### Likelihood Explanation

Exploitation requires only that an attacker supply (or corrupt) serialized `ReceivedOutput` bytes — no validator status, no collusion, no leaked keys. It is bounded by whether integrators feed externally-received `ReceivedOutput`s into signing; the read API exists precisely for that transport, so the precondition is plausible. Severity is capped at Medium because the primary effect is reporting unspendable funds / broken accounting rather than direct theft.

### Recommendation

Either make `ReceivedOutput` non-deserializable from untrusted bytes (construct it only via `Scanner`), or add a `ReceivedOutput::verify(key) -> bool`/`new_checked` that recomputes `p2tr_script_buf(key + GENERATOR * offset)` and requires equality with `output.script_pubkey` — analogous to the report's suggested `isIncentiveToken` mapping that permanently binds the target to its authorized role.

### Proof of Concept

1. Deposit D pays to `P = group_key + o·G` (script registered under offset `o`); its outpoint and `TxOut` are public on-chain.
2. Attacker serializes `ReceivedOutput { offset: o' (o' ≠ o, e.g., o + 1 adjusted to another even point), output: D.txout, outpoint: D.outpoint }`.
3. Consumer calls `ReceivedOutput::read` — succeeds, since `read_F`/`consensus_decode` only check encodings (mod.rs:122-134).
4. Consumer treats D as spendable under `group_key + o'·G`; the key doesn't control D's script, so the spend can never be validly signed — funds are counted as received/available but are unspendable via this record.