### Title
Unspendable-transaction DoS: each dust output forces an extra per-input FROST signature, and enough inputs push the transaction past `MAX_STANDARD_TX_WEIGHT` - (File: networks/bitcoin/src/wallet/send.rs)

### Summary
The OptimismPortal bug class is "a cheap attacker action exhausts a shared per-operation budget, causing everyone else's transactions to fail." The analog in Serai's bitcoin wallet: anyone can send arbitrarily many minimum-value outputs to a multisig address; `Scanner` accepts every matching `script_pubkey` unconditionally, and `SignableTransaction::new` builds **one `AlgorithmMachine` (a full FROST Schnorr signing session) per input** while the whole transaction must stay under `MAX_STANDARD_TX_WEIGHT`. Attacker-controlled dust inflates both the signing round count and the weight until `TooLargeTransaction` aborts any spend that includes these inputs — matching the "fill the resource quota so the victim's tx reverts" shape.

### Finding Description
- `Scanner::scan_transaction` returns a `ReceivedOutput` for every output whose `script_pubkey` matches a registered offset, with no minimum-value or count bound (`networks/bitcoin/src/wallet/mod.rs:199-214`).
- `SignableTransaction::new` rejects *payments* below `DUST = 546` but applies **no value/economic check to inputs** (`send.rs:165-169`); it only checks `input_sat >= payment_sat + needed_fee` (`send.rs:215-221`).
- `SignableTransaction::multisig` creates one `AlgorithmMachine::new(Schnorr::new(), ...)` per input (`send.rs:274-282`), and `TransactionSignMachine::sign` produces a separate `taproot_key_spend_signature_hash` + FROST share per input (`send.rs:373-397`). Signing cost — a full threshold round per input — scales linearly with attacker-created outputs.
- The weight check `if weight > MAX_STANDARD_TX_WEIGHT { Err(TooLargeTransaction) }` (`send.rs:241-243`) means once enough inputs are included, *any* transaction assembled from them fails to be constructed — the honest spend "reverts" exactly as the victim's deposit reverted when the 8M gas budget was consumed.

### Impact Explanation
An unprivileged Bitcoin user spends ~546 sats per output to the Serai multisig address. Each output becomes an indistinguishable `ReceivedOutput` that (a) adds a full FROST signature session to every spend that consumes it and (b) adds ~58 vB to every candidate transaction. Beyond ~100kWU/58vB ≈ ~1700 inputs a spend cannot even be constructed (`TooLargeTransaction`), and long before that each spend requires the threshold network to run hundreds of parallel Schnorr sessions — a resource-exhaustion DoS of the wallet's spend path bought at dust cost, directly analogous to paying 43k L1 gas to consume the 8M L2-gas budget.

### Likelihood Explanation
The attacker only needs to send ordinary Bitcoin transactions to a publicly known P2TR address — no privileged position. The per-input signature multiplier (`send.rs:275-284`) and the unconditional weight abort (`send.rs:241-243`) are reachable entirely through public `Scanner`-scanned outputs. Mitigation exists at the processor level (flat per-input aggregation fee per `spec/processor/UTXO Management.md:182-191`), but that logic lives outside the in-scope `networks/bitcoin` wallet, which itself offers no defense — so within the audited crate the DoS is fully reachable.

### Recommendation
Enforce a minimum economical input value in `SignableTransaction::new` (reject or segregate `ReceivedOutput`s whose value is below `fee_per_vbyte * input_vbytes`), and/or cap `inputs.len()` so the constructed transaction stays comfortably under `MAX_STANDARD_TX_WEIGHT`, letting the builder split or skip dust inputs rather than failing outright. Optionally have `Scanner`/`scan_transaction` drop outputs below `DUST` since they can never be spent profitably.

### Proof of Concept
```rust
// attacker sends N dust outputs (546 sats) to the multisig P2TR address;
// Scanner::scan_transaction collects all of them as ReceivedOutputs.
let mut inputs: Vec<ReceivedOutput> = dust_outputs_from_scanner(); // N >= ~1700
// Each input adds a Taproot input (~230 WU). N inputs exceed
// MAX_STANDARD_TX_WEIGHT (400_000 WU), so:
let err = SignableTransaction::new(inputs, &payments, Some(change), None, fee_rate);
assert!(matches!(err, Err(TransactionError::TooLargeTransaction)));
// Even below the cap, SignableTransaction::multisig spawns one
// AlgorithmMachine<Secp256k1, Schnorr> per input, so N attacker inputs
// force N threshold signatures for a single spend.
```