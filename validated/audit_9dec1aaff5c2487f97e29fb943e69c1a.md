### Title
`ReceivedOutput::read` accepts untrusted offset/TxOut/outpoint triples without verifying the output exists or pays to `key + offset·G` — (File: networks/bitcoin/src/wallet/mod.rs)

### Summary
The advisory's bug class is a missing consistency/policy check on attacker-supplied data before it is persisted and trusted. The analogous pattern in Serai is `ReceivedOutput::read` in `networks/bitcoin/src/wallet/mod.rs` (lines 122–134), which deserializes an `offset`, a `TxOut`, and an `OutPoint` from untrusted bytes and returns them as a spendable `ReceivedOutput` with no verification that:

- the claimed `outpoint` exists on-chain,
- the `script_pubkey` actually equals `p2tr_script_buf(key + G·offset)` for the wallet's key, or
- the claimed `value` matches reality. [1](#0-0) 

### Finding Description
`Scanner::scan_transaction` is the only honest producer of `ReceivedOutput`s: it only constructs one after matching `output.script_pubkey` against a registered offset script (lines 205–211). That construction embeds the implicit invariant "`script_pubkey == p2tr(key + offset·G)` and the outpoint is real." `ReceivedOutput::read` re-creates the type from raw bytes but enforces none of that invariant — it reads a scalar via `Secp256k1::read_F`, then consensus-decodes an arbitrary `TxOut` and `OutPoint`, and returns them as a fully-formed spendable output.

The only downstream check is in `SignableTransaction::multisig` (`send.rs:277`), which verifies `p2tr_script_buf(keys.offset(offset).group_key()) == prevout.script_pubkey`. This binds the offset to the script but still does not verify the outpoint exists or that the claimed `value` is real. A crafted blob therefore satisfies every check all the way to signing:

- attacker picks any `offset`, computes `script = p2tr(key + G·offset)` themselves,
- fabricates an `OutPoint` that was never mined and a `TxOut` with an arbitrary inflated `value`,
- `SignableTransaction::new` (send.rs:175–221) sums `input.output.value` into `input_sat`, passes the `NotEnoughFunds` check, signs the transaction via `TransactionSignMachine::sign` (send.rs:383–390) — the signature is cryptographically valid for `key + offset·G`, yet the transaction can never confirm because the prevout doesn't exist.

### Impact Explanation
Funds are reported received that are not spendable. Any component that persists `ReceivedOutput`s and later reloads them through `read` will credit a fake UTXO, include it in balance/fee accounting (`input_sat` at send.rs:175), and generate signatures spending a nonexistent outpoint. Depending on the surrounding flow this causes incorrect balance reporting, fee/change computation against phantom value, or stuck transactions. This mirrors the advisory: attacker-controlled fields bypass a policy/validity bound that the honest constructor enforced, and are persisted/acted on as if authentic.

### Likelihood Explanation
Reachable wherever `ReceivedOutput::read` consumes bytes not produced by the local `Scanner` — e.g., outputs synced between components, restored from an attacker-influenced store, or received over a channel. The attacker needs no key material: the offset and script can be chosen freely since the check at send.rs:277 is self-consistent for any attacker-chosen offset. No collusion or validator compromise is required — only the ability to feed crafted bytes to `read`.

### Recommendation
Make deserialization re-establish the constructor's invariant. Either:
- have `ReceivedOutput::read` take the wallet's base `key`/`Scanner` and reject bytes where `p2tr_script_buf(key + G·offset) != output.script_pubkey` and the outpoint isn't confirmed on-chain, or
- treat deserialized `ReceivedOutput`s as unverified and re-validate (script match + on-chain existence) inside `SignableTransaction::new` before accounting or signing.

### Proof of Concept
```rust
// Attacker crafts bytes: arbitrary offset, fake outpoint, inflated TxOut
let offset = Scalar::from(0x41u64);
let fake_script = p2tr_script_buf(victim_key + (ProjectivePoint::GENERATOR * offset)).unwrap();
let fake = ReceivedOutput {
  offset,
  output: TxOut { value: Amount::from_sat(1_000_000), script_pubkey: fake_script },
  outpoint: OutPoint::new(Txid::all_zeros(), 0), // never mined
};
let bytes = fake.serialize();
let read_back = ReceivedOutput::read(&mut bytes.as_slice()).unwrap(); // accepted, no checks

// Wallet accepts it as input — NotEnoughFunds passes on phantom value
let tx = SignableTransaction::new(vec![read_back], &[(dest_script, 500_000)], None, None, 20);
// multisig() succeeds too: script matches key + offset·G by construction
let machine = tx.unwrap().multisig(&victim_keys).unwrap();
// FROST signs a tx spending an outpoint that does not exist → reported funds unspendable
```

### Citations

**File:** networks/bitcoin/src/wallet/mod.rs (L122-134)
```rust
  pub fn read<R: Read>(r: &mut R) -> io::Result<ReceivedOutput> {
    let offset = Secp256k1::read_F(r)?;
    let output;
    let outpoint;
    {
      let mut buf_r = BufReader::with_capacity(0, r);
      output =
        TxOut::consensus_decode(&mut buf_r).map_err(|_| io::Error::other("invalid TxOut"))?;
      outpoint =
        OutPoint::consensus_decode(&mut buf_r).map_err(|_| io::Error::other("invalid OutPoint"))?;
    }
    Ok(ReceivedOutput { offset, output, outpoint })
  }
```
