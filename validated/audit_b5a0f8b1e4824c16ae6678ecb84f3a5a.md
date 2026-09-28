### Title
Unvalidated `ReceivedOutput` deserialization allows spoofed received outputs with fabricated value/outpoint - (File: networks/bitcoin/src/wallet/mod.rs)

### Summary
`ReceivedOutput::read` reconstructs a "spendable output" (`offset`, `TxOut`, `OutPoint`) from raw bytes with no integrity or consistency validation. Unlike outputs produced by `Scanner::scan_transaction` — where the `script_pubkey` is guaranteed to match `key + offset*G` and the `outpoint`/`value` are guaranteed to reflect an actual on-chain output — a deserialized `ReceivedOutput` can claim any `offset`, any `script_pubkey`, any `outpoint`, and any `value`. This mirrors CVE-2025-0440's bug class (a crafted artifact presented to the system as a trusted UI/security object, spoofing what is actually true): a crafted byte stream presents itself as a confirmed wallet output that was never observed on-chain.

### Finding Description
In `ReceivedOutput::read` (networks/bitcoin/src/wallet/mod.rs:122-134), the three fields are parsed independently:

```rust
let offset = Secp256k1::read_F(r)?;
output = TxOut::consensus_decode(&mut buf_r)...;
outpoint = OutPoint::consensus_decode(&mut buf_r)...;
Ok(ReceivedOutput { offset, output, outpoint })
```

There is no check that:
- `output.script_pubkey` is even a P2TR script, let alone `p2tr_script_buf(key + offset*G)` for the wallet's key (compare with the construction invariant in `Scanner::scan_transaction` at mod.rs:199-214, where membership in `self.scripts` enforces this binding),
- `output.value` matches the value of the real UTXO at `outpoint`,
- `outpoint` refers to a real transaction output at all.

Downstream, `SignableTransaction::new` (send.rs:150-256) treats `input.output.value` as authoritative for `input_sat`, `NotEnoughFunds`, change, and `fee()` accounting, and commits the claimed `TxOut` values into the sighash via `Prevouts::All(&self.tx.prevouts)` (send.rs:375-386). The only post-deserialization check is in `multisig` (send.rs:276-279), which verifies `p2tr_script_buf(offset.group_key()) == prevouts[i].script_pubkey` — it validates the offset↔script binding but accepts the attacker-chosen `value` and `outpoint` unconditionally.

### Impact Explanation
An attacker who can feed bytes to `ReceivedOutput::read` (a deserialization sink reachable across processor/coordinator message boundaries) can report a received output that is not spendable as claimed: fabricated `value` inflates the wallet's apparent balance and satisfies `NotEnoughFunds`/`fee` accounting against funds that don't exist; a fabricated or mismatched `outpoint` causes the threshold group to produce a signature over a sighash committing to a false prevout — the resulting transaction is unbroadcastable, while the real funds were reported consumed/received. This satisfies the "funds reported received that are not spendable" impact criterion and is a direct analog of UI spoofing: the deserialized object asserts on-chain facts the code never verified.

### Likelihood Explanation
Exploitation requires an attacker to reach a `ReceivedOutput::read` call on data they control rather than data produced locally by `Scanner::scan_block`/`scan_transaction`. Where `ReceivedOutput`s are round-tripped through serialized messages/plans shared between parties (e.g., `Output::read`/`Plan::read` consumers), an unauthenticated or semi-trusted sender can substitute forged bytes. The cryptographic check in `multisig` prevents stealing via a wrong offset, so impact is bounded to spoofed balances and invalid spends rather than key compromise — consistent with Medium severity.

### Recommendation
- At construction/deserialization time, bind the fields: require `output.script_pubkey` to be a P2TR script, and add an API such as `ReceivedOutput::read_for(key, ...)` that verifies `output.script_pubkey == p2tr_script_buf(key + offset*G)`, rejecting otherwise.
- Consumers should treat the `value`/`outpoint` as claims and validate them against chain data before use in balance or sighash contexts.
- Document explicitly that `ReceivedOutput::read` performs no validation and that deserialized outputs are untrusted until re-verified against the scanned chain state.

### Proof of Concept
```rust
// In-scope crates: bitcoin-serai wallet, frost Secp256k1
use bitcoin::{Amount, TxOut, OutPoint, Txid, hashes::Hash};
use k256::Scalar;
use bitcoin_serai::wallet::{ReceivedOutput, SignableTransaction, p2tr_script_buf};

// Wallet key (even-Y) and a real registered offset
let key = /* group key */;
let offset = Scalar::ZERO;
let real_script = p2tr_script_buf(key).unwrap();

// Attacker crafts bytes: real script (so multisig() passes the offset check),
// but a fabricated value and a fabricated outpoint
let fake = ReceivedOutput::read(&mut {
    let mut buf = vec![];
    buf.extend(offset.to_bytes());                       // valid offset
    buf.extend(bitcoin::consensus::serialize(&TxOut {    // fabricated TxOut
        value: Amount::from_sat(1_000_000_000),          // claims 10 BTC
        script_pubkey: real_script.clone(),
    }));
    buf.extend(bitcoin::consensus::serialize(&OutPoint {
        txid: Txid::all_zeros(),                          // non-existent prevout
        vout: 0,
    }));
    buf.as_slice()
}).unwrap();

// The spoofed output is accepted; its value drives fee/funds accounting and the
// false prevout is committed into the Taproot sighash, producing a signature for
// a transaction spending 10 BTC that was never received.
let tx = SignableTransaction::new(
    vec![fake], &[(real_script, 100_000)], None, None, 20,
).unwrap(); // NotEnoughFunds check is bypassed by the fabricated value
```