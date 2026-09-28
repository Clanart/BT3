### Title
`SignableTransaction`/`TransactionSignMachine` trust untrusted `ReceivedOutput` prevout values and outpoints, signing transactions that spend non-existent or mis-valued inputs - ([File: networks/bitcoin/src/wallet/send.rs](networks/bitcoin/src/wallet/send.rs))

### Summary
The reported bug class is "funds moved in/out are assumed rather than measured": `_transferFromERC20` trusted that the amount sent equals the amount received and that a successful call implies a successful transfer, with no balance sanity check. The Serai analog is in the Bitcoin wallet: `ReceivedOutput::read` deserializes an arbitrary `TxOut` (value + script_pubkey) and `OutPoint` from untrusted bytes, and `SignableTransaction::new`/`multisig`/`TransactionSignMachine::sign` consume these claimed prevouts as authoritative. Only the script_pubkey is ever checked against the key (`multisig` verifies `p2tr_script_buf(offset.group_key()) == prevouts[i].script_pubkey`); nothing verifies that the outpoint exists on-chain or that the claimed `value` matches the real UTXO. The BIP-341 sighash then *commits* the signer to the forged values via `Prevouts::All`.

### Finding Description
- `ReceivedOutput::read` reads `offset`, a consensus-decoded `TxOut` (arbitrary `value` and `script_pubkey`), and an arbitrary `OutPoint` with no validation that the referenced output exists or carries that value (`mod.rs` lines 122–134).
- `SignableTransaction::new` sums `input.output.value` directly to compute `input_sat`, decides payments/change/fees (`send.rs` lines 175, 187–235), and stores `prevouts` verbatim (`send.rs` line 253).
- `multisig` checks only that each prevout's `script_pubkey` equals the P2TR script derived from `keys.offset(offset)` (`send.rs` lines 273–282). The `value` and `outpoint` are never checked.
- `TransactionSignMachine::sign` commits to all claimed prevouts with `Prevouts::All(&self.tx.prevouts)` and produces valid Schnorr signatures over sighashes binding the *claimed* amounts (`send.rs` lines 373–390).

Because Taproot sighashes bind prevout *values*, a forged or stale `ReceivedOutput` (inflated value, non-existent outpoint, or an output already spent) does not cause an error — the threshold still produces a fully signed transaction. That transaction is then invalid/unspendable on-chain, while the library reports `fee()` and change amounts computed from the forged inputs — i.e., "funds reported received that are not spendable" and fee/change math derived from amounts never actually received, exactly the insolvency/miscounting shape of the ERC20 finding. There is no equivalent of the recommended "balance actually increased by the expected amount" sanity check.

### Impact Explanation
Any consumer that accepts `ReceivedOutput`s from an untrusted source (network messages, coordinator-supplied data) can be induced to have the threshold sign a transaction spending phantom or mis-valued inputs. Consequences: (a) a signed but invalid transaction that cannot confirm — the apparent received funds are not spendable; (b) fee/change computed on inflated input values, so the constructed outputs can exceed real funds available; (c) wasted on-chain fees / burned real inputs if a subset of forged prevouts is mixed with real ones. Severity is bounded because BIP-341's `Prevouts::All` commitment prevents the forged values from being silently swapped after signing — the signatures attest to the forged values, so the failure is a spendability/accounting failure rather than signature forgery.

### Likelihood Explanation
Reachable by any party able to supply `ReceivedOutput` bytes to `ReceivedOutput::read` and downstream `SignableTransaction::new`/`multisig` — e.g., an untrusted coordinator or peer in the signing pipeline. It requires the consumer to source prevout data from an untrusted party rather than its own verified `Scanner` over confirmed blocks; the crate itself provides no verification path tying `read`-produced outputs to chain state, so this is the default hazard of the API. Medium likelihood, Medium impact.

### Recommendation
Treat `ReceivedOutput` as a claim requiring confirmation: (1) before constructing `SignableTransaction`, verify each `outpoint` resolves to an unspent UTXO whose `value` and `script_pubkey` equal the `ReceivedOutput`'s fields (e.g., via `gettxout`/a verified scan over confirmed blocks); (2) add a sanity check in `SignableTransaction::new` or `multisig` asserting `prevouts` were sourced from a validated scan — at minimum document that `ReceivedOutput::read` output must be reconciled against chain state; (3) have `scan_block`/`scan_transaction` callers drop coinbase/immature and spent outputs before use (the coinbase maturity caveat is documented but not enforced). The analogous fix to the ERC20 report is: measure, don't trust — validate that the input set the transaction commits to equals the inputs actually confirmed on-chain.

### Proof of Concept
Conceptual reproduction:

```rust
// Attacker-controlled bytes handed to a signer/integration.
let mut buf = Vec::new();
// offset such that key + offset*G is the signer's real P2TR key (valid script_pubkey)
Secp256k1::read_F(...); // a legitimately-derived offset, e.g. Scalar::ZERO
let forged = TxOut { value: Amount::from_sat(10_000_000), script_pubkey: real_p2tr_script };
let fake_outpoint = OutPoint { txid: Txid::all_zeros(), vout: 0 };
forged.consensus_encode(&mut buf).unwrap();
fake_outpoint.consensus_encode(&mut buf).unwrap();

let ro = ReceivedOutput::read(&mut &buf[..]).unwrap(); // accepted
let stx = SignableTransaction::new(vec![ro], &[(dest_script, 5_000_000)],
                                 Some(change_script), None, 10).unwrap();
// input_sat = 10M (phantom), change credited ~5M that doesn't exist
let machine = stx.multisig(&keys).unwrap(); // script_pubkey check passes
// sign() commits to Prevouts::All including the 10M-value phantom prevout;
// the threshold produces valid Schnorr signatures over a transaction whose
// input does not exist → broadcast fails / funds "received" are unspendable.
```

Key code: `ReceivedOutput::read` at `mod.rs:122-134`, script-only check in `multisig` at `send.rs:273-282`, and the `Prevouts::All` commitment at `send.rs:375-390`.