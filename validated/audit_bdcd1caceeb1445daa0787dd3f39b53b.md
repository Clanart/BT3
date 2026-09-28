### Title
`ReceivedOutput::read` / `SignableTransaction` validate only the script_pubkey↔offset binding, never that the outpoint's real on-chain output is what is claimed, letting crafted bytes cause signing of a spend of a nonexistent or mis-valued input - (File: networks/bitcoin/src/wallet/mod.rs)

### Summary
The Notional bug was a guard that blocked `ALT_ETH_ADDRESS` while the actually-used identifiers (`ETH`, `WETH`) passed unchecked — a protection bound to the wrong representation of the protected object. The same class exists in bitcoin-serai: `SignableTransaction::multisig` guards that each input is "ours" by checking only that `p2tr_script_buf(offset.group_key()) == prevouts[i].script_pubkey`. Both `prevouts[i]` (the claimed `TxOut`) and `tx.input[i].previous_output` (the claimed `OutPoint`) come from a `ReceivedOutput` deserialized by `ReceivedOutput::read`, which validates only that `offset` is a canonical scalar (`Secp256k1::read_F`) and that `TxOut`/`OutPoint` are well-formed consensus encodings. It never verifies that the outpoint exists, nor that the claimed `TxOut` (value, script) is the real output at that outpoint.

### Finding Description
`ReceivedOutput::read` at `networks/bitcoin/src/wallet/mod.rs:122-134` accepts attacker-controlled bytes: a canonical scalar `offset`, an arbitrary `TxOut`, and an arbitrary `OutPoint`, with no binding between them. Downstream, `SignableTransaction::new` at `networks/bitcoin/src/wallet/send.rs:175-185` uses the claimed `output.value` for `input_sat` (funding/fee/change math at send.rs:215-235) and builds `TxIn`s from the claimed `outpoint`. The only authenticity check in `multisig` (send.rs:275-281) is `p2tr_script_buf(offset.group_key())? != self.prevouts[i].script_pubkey` — it proves "we could sign for this script" but not "this outpoint is real and holds this TxOut". This is reachable by an unprivileged party who can supply serialized output bytes into the pipeline, since `ReceivedOutput::read` is explicitly an untrusted-bytes entry point (consumed by `Output::read` at processor/src/networks/bitcoin.rs:156).

### Impact Explanation
A crafted `ReceivedOutput` combining a real Serai P2TR `script_pubkey` with a fabricated `outpoint` and fabricated `value` passes every check and causes the threshold validators to sign a Bitcoin transaction spending an input that does not exist, or whose real value differs from the committed prevout amount. Because the BIP-341 `Prevouts::All` sighash commits to the *claimed* prevout, the signature is invalid at consensus — meaning funds reported received (the fake output is treated as a spendable input by `SignableTransaction`) are not spendable, and the multisig is induced to sign an unintended message referencing attacker-chosen outpoints/amounts. Conversely a real UTXO paired with a false TxOut produces transactions that can never confirm, burning signing rounds and potentially causing the coordinator to treat real funds as consumed.

### Likelihood Explanation
Requires attacker-controlled bytes to reach `ReceivedOutput::read` in a context that flows into `SignableTransaction`/`multisig`. The deserialization path performs zero semantic validation (unlike `Scanner::scan_transaction`, which at least produces outpoints from real transactions), so any context deserializing untrusted outputs and feeding them to the signer is exposed. Severity is bounded: the signer cannot be tricked into a *valid* spend of someone else's output (the script↔key check prevents that), so the realistic outcomes are invalid signed transactions and misaccounted/unspendable reported funds — consistent with Medium.

### Recommendation
Treat the deserialized `outpoint`/`output` pair as unverified claims. In `SignableTransaction::new` or `multisig`, fetch (or require the caller to have fetched via `Scanner`) the actual `TxOut` at each `previous_output` and assert it equals `inputs[i].output` before signing — analogous to adding `Deployments.ETH`/`WETH` to the blocklist rather than checking only `ALT_ETH_ADDRESS`. At minimum, document that `ReceivedOutput`s fed to `SignableTransaction` must originate from `Scanner::scan_transaction`/`scan_block`, and consider marking `ReceivedOutput` construction as crate-private so only scanner-produced values (with verified outpoints) can reach the signing path.

### Proof of Concept
```rust
// Crafted bytes: canonical offset, Serai-owned script_pubkey, fake outpoint, fake value
let offset = Scalar::ZERO;
let fake_txout = TxOut {
    value: Amount::from_sat(1_000_000_000),            // claims 10 BTC
    script_pubkey: p2tr_script_buf(key).unwrap(),      // passes multisig's script check
};
let fake_outpoint = OutPoint::new(Txid::all_zeros(), 0); // nonexistent UTXO

let mut bytes = offset.to_bytes().to_vec();
bytes.extend(serialize(&fake_txout));
bytes.extend(serialize(&fake_outpoint));

// ReceivedOutput::read accepts it — no binding between outpoint and output
let received = ReceivedOutput::read::<&[u8]>(&mut bytes.as_ref()).unwrap();

// SignableTransaction uses the claimed value for funding math and the fake
// outpoint for the input; multisig() passes because script_pubkey matches
let tx = SignableTransaction::new(vec![received], &payments, change, None, fee).unwrap();
let machine = tx.multisig(&keys[&Participant::new(1).unwrap()]).unwrap(); // Some(...)
// The multisig signs a transaction referencing a nonexistent input /
// miscommitted prevout amount — invalid at consensus, funds "received" are
// unspendable.
```

Note on uncertainty: whether an unprivileged party can in practice inject serialized `ReceivedOutput`/`Output` bytes into a signing flow depends on integrator/processor usage (out of scope); the bug itself — deserialization with no outpoint/output binding and a guard checking only the script↔offset relationship — is concrete in the in-scope code.