### Title
`ReceivedOutput::read` accepts untrusted bytes without binding the claimed offset to the output's script or the outpoint to the chain — ([File: networks/bitcoin/src/wallet/mod.rs])

### Summary
The reference bug is a swap performed with `minBuyAmount = 0`: the caller accepts whatever value comes back with no lower bound or consistency check on what was actually received. The analog in Serai is `ReceivedOutput::read` (`networks/bitcoin/src/wallet/mod.rs:122-134`): it deserializes an `offset`, a `TxOut`, and an `OutPoint` from attacker-controlled bytes and returns them as a valid "spendable output" without any check that the pieces are consistent or real. The deserialization path enforces zero constraints — the equivalent of `minBuyAmount = 0`.

### Finding Description
`ReceivedOutput` is the type that represents "funds the vault can spend": it carries the scalar `offset` needed to derive the spending key, the `TxOut` (script and value), and the `outpoint` being claimed.

`ReceivedOutput::read` (`mod.rs:122-134`) performs three raw decodes:

- `Secp256k1::read_F` for `offset` — any scalar accepted.
- `TxOut::consensus_decode` — any script_pubkey and any `value` accepted (including values above 21M BTC supply, dust, or zero).
- `OutPoint::consensus_decode` — any txid/vout accepted; there is no verification the referenced output exists on-chain or is unspent.

Crucially, it never checks that `output.script_pubkey == p2tr_script_buf(key + G*offset)`, i.e. that the claimed `offset` actually makes the output spendable by the group key — the check that *does* exist on the construction side (`SignableTransaction::multisig`, `send.rs:277`, verifies `p2tr_script_buf(offset.group_key()) == prevouts[i].script_pubkey`, and `Scanner::register_offset` at `mod.rs:180-196` binds offset→script at registration). The honest scanning path (`Scanner::scan_transaction`, `mod.rs:199-214`) only ever produces `ReceivedOutput`s whose script was found in the registered `scripts` map, so the invariant "offset matches script" holds by construction — but `read` reconstructs the struct from bytes and never re-establishes it.

The downstream consumer `Output::read` (`processor/src/networks/bitcoin.rs:145-166`) wraps `ReceivedOutput::read` plus a `kind`, `presumed_origin`, and `data`, again with no validation. The consistency check that does exist elsewhere — `Output::key()` at `processor/src/networks/bitcoin.rs:112-122`, which recomputes the group key as `read_G(script_key) - G*offset` — can be satisfied by an attacker: they choose any offset δ, compute the script_pubkey for `key + G·δ` themselves, and claim a fabricated `outpoint` with a fabricated `value`. `key()` then returns the correct group key and the object is indistinguishable from a real deposit.

### Impact Explanation
Funds can be reported as received that are not spendable — explicitly an in-scope impact. A forged `ReceivedOutput`/`Output` claims an on-chain UTXO (outpoint + value) that may not exist, may be already spent, or may be credited at an arbitrary `value`. Consumers use `output.value()`/`balance()` (`mod.rs:116-118`, `bitcoin.rs:128-130`) for accounting and feed `ReceivedOutput`s into `SignableTransaction::new` (`send.rs:150-256`), where `input_sat` is summed from the attacker-supplied `output.value` fields (`send.rs:175`). That means a forged output inflates `input_sat`, causing the multisig to construct and sign a transaction paying real funds against phantom inputs — the exact "receive less than expected / value accepted without a floor" shape of the reference bug. The signed transaction is then either invalid (nonexistent prevout, burning a round of threshold signing and locking real UTXOs into the attempt) or, in an accounting context that credits `balance()` before on-chain confirmation, direct mis-crediting.

### Likelihood Explanation
Reachable by any party that can supply bytes to `ReceivedOutput::read` / `Output::read` — the prompt's threat model explicitly designates these as untrusted-byte entry points. Crafting the bytes requires no secret: the attacker only needs the group key (public), chooses δ freely, builds the matching Taproot script with `p2tr_script_buf(key + G·δ)`, and invents an outpoint. No honest-path check distinguishes this from a scanned output. Severity Medium: the forged object must still pass downstream flow (an invalid prevout prevents a confirming spend), so it enables false accounting and signer-resource/fund-flow attacks rather than direct theft.

### Recommendation
In `ReceivedOutput::read`, re-derive and enforce the offset↔script invariant after decoding: given the group key `key`, check `output.script_pubkey == p2tr_script_buf(key + G*offset)`, rejecting if the point is odd or the script mismatches. Since `read` doesn't currently know the group key, either pass it in (change signature to `read(r, key)`) or store/verify it at the `Output` layer and validate in `Output::read` via `key()` plus an explicit equality check. On-chain existence of the `outpoint` must remain the responsibility of the scanning/confirmation path — consumers should not treat a deserialized `ReceivedOutput` as confirmed funds without a chain lookup.

### Proof of Concept
```rust
// Attacker knows the group key `key` (public).
let offset = Scalar::ONE; // arbitrary
let offset_key = key + (ProjectivePoint::GENERATOR * offset);

// If odd, bump offset until even (same loop as register_offset).
let script = p2tr_script_buf(offset_key).unwrap();

// Fabricate a ReceivedOutput claiming a nonexistent, high-value UTXO.
let fake = ReceivedOutput {
    offset,
    output: TxOut {
        value: Amount::from_sat(1_000_000_000), // arbitrary claimed value
        script_pubkey: script,
    },
    outpoint: OutPoint::null(), // or any invented txid:vout
};

let bytes = fake.serialize();
let decoded = ReceivedOutput::read::<&[u8]>(&mut bytes.as_ref()).unwrap();
// decoded is accepted: key() returns `key`, value() returns 1_000_000_000,
// yet no such UTXO exists on-chain.
```

`read` succeeds because it performs zero consistency checks — the deserialization-time `minBuyAmount = 0`.