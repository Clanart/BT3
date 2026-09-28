### Title
Scanner reports dust and zero-value outputs as received spendable funds - (File: networks/bitcoin/src/wallet/mod.rs)

### Summary
`Scanner::scan_transaction` in `networks/bitcoin/src/wallet/mod.rs` flags any transaction output whose `script_pubkey` matches a registered script as a `ReceivedOutput`, without checking the output's value. An unprivileged third party can send dust-value (or even zero-value) outputs to the scanned P2TR script; the wallet library reports them as received, spendable funds even though they are below Bitcoin's dust threshold and are uneconomical/nonstandard to spend.

### Finding Description
`Scanner::scan_transaction` iterates `tx.output` and pushes a `ReceivedOutput` for every output whose `script_pubkey` is in `self.scripts`, with no minimum-value check (networks/bitcoin/src/wallet/mod.rs:199-214). `scan_block` inherits the same behavior (mod.rs:221-227). `ReceivedOutput` is documented as "A spendable output" (mod.rs:88) and is the input type consumed by `SignableTransaction::new` (networks/bitcoin/src/wallet/send.rs:150-156).

The spend side recognizes a dust limit: `send.rs` defines `pub const DUST: u64 = 546` (send.rs:32) and rejects payments below it (`DustPayment`, send.rs:165-169), and only emits change if `value >= DUST` (send.rs:228-234). The receive side has no symmetric check, so the library reports outputs it itself classifies as non-spendable dust.

Additionally, each `ReceivedOutput` accepted this way will later be pulled in as an input to `SignableTransaction::new`, where every input adds ~57 vbytes to `needed_fee` (send.rs:204-206) while contributing fewer satoshis than the fee it costs, and `fee()` simply returns `sum(prevouts) - sum(outputs)` (send.rs:138-141). A zero-value output contributes nothing yet still increases the required fee.

### Impact Explanation
1. Funds are reported received that are not economically spendable: an attacker sends e.g. a 300-sat output to the victim's scanned P2TR script. `scan_transaction` returns it as a `ReceivedOutput`; downstream accounting treats it as a real deposit, but spending it costs more in fees than it is worth, and sub-dust change-style outputs are ones this same library refuses to create.
2. Fee-drain griefing: each such dust output included in a later `SignableTransaction` burns ~`57 * fee_per_vbyte` satoshis of the wallet's funds as fees in exchange for <546 sats of input value. The attacker spends only dust amounts to inflict disproportionate fee loss on the wallet.
3. `ReceivedOutput::read` (mod.rs:122-134) similarly deserializes arbitrary `TxOut` values with no dust/spendability validation, so the same class reaches integrators through the read API.

### Likelihood Explanation
The trigger is a plain on-chain Bitcoin transaction any unprivileged party can send; no validator collusion, no leaked keys, no malformed encodings are required. Whether it manifests depends on integrator behavior (whether scanned outputs are auto-aggregated as spend inputs and whether an external dust filter exists), but within the in-scope crate the reported `ReceivedOutput` claims spendability unconditionally, and the only dust guard lives on the payment/change path in `send.rs`, not on the receive path. Medium likelihood: deposits of dust to a well-known multisig address are cheap and trivially constructible.

### Recommendation
- Add a minimum-value check in `Scanner::scan_transaction` (e.g. skip outputs with `output.value.to_sat() < DUST`), symmetric with the `DUST` constant already enforced in `send.rs`, so `ReceivedOutput` upholds its documented invariant of being "a spendable output."
- Alternatively, expose `ReceivedOutput::value`-aware filtering or document the requirement and enforce it in `ReceivedOutput::read`, so untrusted serialized outputs cannot inject sub-dust "funds."
- Consider having `SignableTransaction::new` skip or error on inputs whose value is below the marginal fee they add (~`57 * fee_per_vbyte`), preventing forced fee overpayment.

### Proof of Concept
```rust
// Attacker crafts and broadcasts a tx paying a dust amount to the
// victim's registered P2TR script_pubkey.
let victim_script = p2tr_script_buf(victim_key).unwrap(); // even-Y key
let dust_tx = Transaction {
    version: Version(2),
    lock_time: LockTime::ZERO,
    input: vec![/* attacker's input */],
    output: vec![
        TxOut {
            value: Amount::from_sat(300), // < DUST (546), < spend cost
            script_pubkey: victim_script.clone(),
        },
    ],
};

// Once confirmed, the victim's Scanner reports it as received funds:
let outputs = scanner.scan_transaction(&dust_tx);
assert_eq!(outputs.len(), 1);
assert_eq!(outputs[0].value(), 300);            // reported as spendable
assert_eq!(outputs[0].output().script_pubkey, victim_script);

// If the wallet aggregates scanned outputs into SignableTransaction::new,
// this input contributes 300 sats but adds ~57 * fee_per_vbyte to
// needed_fee — a net loss in fees for the wallet, and a deposit the
// wallet itself would refuse to create (DustPayment check, send.rs:165).
```