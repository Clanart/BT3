### Title
Refund `origin` inferred from `tx.input[0]` lets an attacker steal refunds of other parties' deposits in multi-party transactions - ([File: processor/src/networks/bitcoin.rs])

### Summary

The external report describes a payer losing funds because a payment they made was credited to a different receiver. The Bitcoin processor analog is in `Bitcoin::get_outputs`: for any transaction paying a Serai multisig address, the `presumed_origin` (the refund address used when a `RefundableInInstruction` fails or omits `origin`) is unconditionally taken from the spent output of `tx.input[0]` — the *first input*, not the party that actually funded the Serai-bound output or authored the embedded `InInstruction`. In a multi-party transaction (PayJoin, CoinJoin, batched withdrawal) where an unprivileged attacker controls input ordering, deposits made by other input contributors are attributed to the attacker, and failure-triggered refunds are routed to the attacker's Bitcoin address.

### Finding Description

In `processor/src/networks/bitcoin.rs`, `get_outputs` scans each non-coinbase transaction for outputs matching the multisig's scripts and, if any exist, computes `presumed_origin` solely from `tx.input[0]`'s previous output's `script_pubkey`:

```rust
let spent_output = {
  let input = &tx.input[0];
  ...rpc.get_transaction(&spent_tx)...output.swap_remove(vout)
};
Address::new(spent_output.script_pubkey)
```

This `presumed_origin` is then assigned to every scanned output of the transaction (`processor/src/networks/bitcoin.rs:705-736`).

Downstream, `instruction_from_output::<N>(&output)` in `processor/src/multisigs/mod.rs` derives `refund_to` for each `OutputType::External` output, and when the instruction cannot produce a valid `InInstruction` (or fails), the scheduler emits `PlanFromScanning::Refund(output.clone(), refund_to)` (`mod.rs:903-907`, `936-940`), which signs a Bitcoin transaction sending the coins back to that origin address.

Per `spec/integrations/Instructions.md`, an embedded `RefundableInInstruction.origin` overrides the automatically provided origin — but the *fallback* origin when the field is `None` (or the data fails to parse entirely) is the attacker-controlled `presumed_origin`. The inline comment even acknowledges the heuristic is fragile ("This may identify the P2WSH output embedding the InInstruction as the origin") yet still applies it unconditionally to all outputs of the transaction.

### Impact Explanation

The victim's BTC reaches the multisig and is correctly credited internally, but when the deposit triggers a refund path — e.g., the instruction fails execution on Serai, or parsing yields no instruction and only a `refund_to` — the multisig signs a refund transaction paying the *attacker's* address, not the victim's. The victim loses their coins exactly as in the reference bug: the party who funded the position is not the receiver of the resulting payment. The attacker contributes dust or any input as `tx.input[0]` and receives the full refund of the Serai-bound output.

### Likelihood Explanation

Reachability requires a transaction that (a) pays a scanned Serai multisig address, (b) has the attacker's input at index 0, and (c) carries an `InInstruction` that fails or omits `origin`. PayJoin (BIP-78) is the canonical scenario: the receiver controls final input ordering and can place their own input first while the sender's wallet constructs the deposit output and embedded instruction. Batched exchange/custodian withdrawals are another: the entity ordering inputs may differ from the depositor the instruction concerns. The attacker can also deliberately craft a malformed/oversized instruction to force the refund path. No validator compromise or key material is needed — only a Bitcoin transaction the attacker causes to be included. Impact is bounded to refund flows, hence Medium.

### Recommendation

- Do not derive `presumed_origin` from `tx.input[0]` alone. Either require an explicit `origin` in the `RefundableInInstruction` before issuing any refund (dropping `presumed_origin` for multi-input transactions), or attribute origin per-output via a binding embedded in the instruction/commitment data rather than per-transaction.
- If a heuristic is kept, restrict refunds to transactions where *all* inputs share the same `script_pubkey`, so no third party can poison the inferred origin.
- Document clearly that the automatic `origin` is inferred from input ordering and is therefore only trustworthy for single-party transactions.

### Proof of Concept

1. Attacker establishes a PayJoin/session with a victim whose wallet deposits BTC to the Serai external address, embedding a `RefundableInInstruction { origin: None, instruction: <failing InInstruction> }` (or an instruction that will fail on-chain, e.g., a Dex swap with unsatisfiable `minimum`).
2. The attacker contributes an input and orders it as `tx.input[0]`; the victim's inputs fund the Serai output.
3. `Bitcoin::get_outputs` (`processor/src/networks/bitcoin.rs:714-729`) fetches `input[0].previous_output`'s `script_pubkey` — the attacker's address — and stores it as `presumed_origin` on the victim's output.
4. `instruction_from_output` produces a `refund_to` resolving to the attacker's address; the instruction fails, so `scanner_event_to_multisig_event` pushes `PlanFromScanning::Refund(output, refund_to)` (`processor/src/multisigs/mod.rs:936-939`).
5. The multisig signs and broadcasts a refund transaction paying the victim's deposit amount (minus fees) to the attacker's Bitcoin address. The victim, who funded the deposit, receives nothing.

Note on uncertainty: I could not fully read `instruction_from_output`'s internals this session, so the exact precedence between instruction-provided `origin` and `presumed_origin` is inferred from `spec/integrations/Instructions.md` ("Networks may automatically provide `origin`. If they do, the instruction may still provide `origin`, overriding the automatically provided value") combined with the `refund_to`/`presumed_origin` plumbing in `mod.rs` and `bitcoin.rs`.