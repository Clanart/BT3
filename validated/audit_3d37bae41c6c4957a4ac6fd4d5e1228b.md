### Title
`ReceivedOutput::read` accepts a fully attacker-controlled `offset`/`TxOut`/`OutPoint` triple with no consistency check, so an output can be "received" (credited) that is not spendable under the recorded offset - (File: networks/bitcoin/src/wallet/mod.rs)

### Summary
The Warp Finance exploit let an attacker inflate a valuation input (the manipulated Uniswap LP token price) so the protocol credited more collateral than was real, then borrowed against it. The structural analog in Serai is `ReceivedOutput`, the wallet's record of a spendable coin: its deserializer `ReceivedOutput::read` reads three independent, attacker-controlled fields — a scalar `offset`, a `TxOut`, and an `OutPoint` — and performs zero validation that the `offset` actually derives the output's `script_pubkey` from the scanner's base key, or that the output corresponds to any registered scan script at all. The only place the `offset ↔ script_pubkey` binding is established is `Scanner::scan_transaction`, which looks the offset up in the `scripts` map; `read` bypasses that binding entirely.

### Finding Description
`Scanner` maintains `scripts: HashMap<ScriptBuf, Scalar>` mapping a P2TR script to the scalar offset that makes the output spendable (`networks/bitcoin/src/wallet/mod.rs:152-196`). `scan_transaction` only emits a `ReceivedOutput` when `output.script_pubkey` is present in that map, so the recorded offset is guaranteed to satisfy `key + offset·G == output_key` (mod.rs:199-214).

`ReceivedOutput::read` (mod.rs:120-134), however, deserializes:
- `offset` via `Secp256k1::read_F` — any canonical scalar,
- `output` via `TxOut::consensus_decode` — any value and any `script_pubkey`, including scripts never registered with the scanner, scripts belonging to a different registered offset, or arbitrary non-P2TR scripts,
- `outpoint` via `OutPoint::consensus_decode` — any txid/vout,

and returns the tuple directly. There is no check that `p2tr_script_buf(scanner_key + GENERATOR * offset) == output.script_pubkey`, no membership check against `scripts`, and no confirmation that `outpoint` references a real UTXO. Just as Warp's oracle read a manipulable price field and treated it as ground truth, a consumer of `ReceivedOutput::read` treats an attacker-supplied (offset, script, value) claim as ground truth about spendable funds.

### Impact Explanation
An unprivileged party who can cause `ReceivedOutput::read` to run on bytes they control can inject phantom or mis-attributed outputs into the wallet's view of its funds:

- An output recorded with a `value` (analogous to Warp's inflated collateral value) that does not correspond to any real UTXO, or that pays to a script the key cannot spend, inflates the balance the wallet believes it holds — "funds reported received that are not spendable".
- A real output recorded with a wrong `offset` (e.g., `Scalar::ZERO` on a branch/change/forward output, or an offset registered for a different purpose) will later be spent via `SignableTransaction` using an incorrect re-keyed private key, producing a signature invalid for that input — corrupting otherwise valid planned transactions and misclassifying output kinds (the processor-side `kinds` map keys output classification off `output.offset()`, and `Output::key()` subtracts `offset·G` to recover the signing key, so a forged offset directly corrupts key attribution).
- Combined with a chosen `script_pubkey`, an attacker can attribute an output to the external/base offset while the actual spend authority lies elsewhere, enabling accounting manipulation of the kind Warp suffered (credit exceeding real collateral).

### Likelihood Explanation
Reachability is conditional on an integration feeding untrusted bytes into `ReceivedOutput::read` — the rules explicitly list `ReceivedOutput::read` as an untrusted-input sink. The scan path (`scan_transaction`/`scan_block`) is safe; the bug is confined to the deserialization path where the offset–script binding established by the `Scanner` is never re-verified. The exploit requires no collusion, no malicious validator, and no broken assumptions — only attacker-controlled serialized bytes. Because a mis-attributed offset produces signatures that verify against the wrong key, detection may only surface at spend time, after the phantom credit has already been booked.

### Recommendation
- Re-derive the expected script in the reader's consumers: require `p2tr_script_buf(scanner.key + GENERATOR * offset) == Some(output.script_pubkey)`, or make `ReceivedOutput::read` a method on `Scanner` so it can check `scripts` membership exactly as `scan_transaction` does.
- Reject deserialized `TxOut`s whose `script_pubkey` is not a v1 P2TR output with an even-Y key.
- Document that `ReceivedOutput::read` output must be cross-checked against on-chain data before the `value`/`outpoint` are trusted for balance or input selection.

### Proof of Concept
Conceptually, for a scanner key `K` (even Y) with registered offsets `{ZERO, o_change}`:

```rust
// Attacker-controlled bytes: claim a 21 BTC output to an arbitrary P2TR
// script while asserting it is spendable with offset ZERO (the base key).
let fake = ReceivedOutput::read(&mut &serialize(&(
    Scalar::ZERO,                      // offset: claims base-key spendability
    TxOut {
        value: Amount::from_sat(2_100_000_000_000),
        script_pubkey: arbitrary_p2tr_script, // or another offset's script
    },
    OutPoint { txid: arbitrary_txid, vout: 0 },
))[..]).unwrap(); // succeeds — no binding check
```

`fake` is now indistinguishable from a legitimately scanned output: `fake.value()` reports the forged amount, `fake.offset()` claims it is spendable by `K`, yet either no such UTXO exists or the real spend authority is `K + o'·G` for `o' != ZERO`. Feeding `fake` into `SignableTransaction::new` produces a plan the threshold group will sign with the wrong key (invalid spend) while the wallet's recorded balance was inflated — the same "credit exceeding collateral" failure mode as the Warp LP-token price manipulation.