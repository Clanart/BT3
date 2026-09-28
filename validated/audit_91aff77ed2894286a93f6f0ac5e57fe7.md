### Title
Change outputs between 546 and 9,999 sats are created but silently dropped by the processor's scanner — ([File: networks/bitcoin/src/wallet/send.rs, processor/src/networks/bitcoin.rs])

### Summary
`SignableTransaction::new` emits a change output whenever the leftover amount is `>= DUST` where `DUST = 546` [1](#0-0) [2](#0-1) . The processor-side Bitcoin scanner, however, discards every scanned output whose amount is below `N::DUST = 10_000` [3](#0-2) [4](#0-3) . Analogous to the BuildFinance exploit — where an attacker-controlled numeric parameter was executed verbatim against treasury funds — here an attacker-influenced payment amount flows verbatim into a protocol spend whose change lands in the unhandled dead zone `[546, 10_000)`, permanently stranding multisig funds.

### Finding Description
In `SignableTransaction::new`, the change branch pushes a `TxOut` for `input_sat - payment_sat - fee_with_change` whenever that value is `>= 546` sats, the lower `DUST` constant defined locally in `send.rs` [5](#0-4) . The produced transaction is then signed by the FROST multisig via `TransactionMachine`, committing the change output on-chain [6](#0-5) .

On the receive side, `Bitcoin::get_outputs` scans each block with `Scanner::scan_transaction`, which correctly recognizes the change output (the `OutputType::Change` offset is registered in `scanner()`) [7](#0-6) [8](#0-7) . But the processor's `ScannerHandle` then applies `if output.balance().amount.0 >= N::DUST` with `N::DUST = 10_000`, dropping any output below 10,000 sats before it is ever emitted or persisted [4](#0-3) [3](#0-2) .

The two dust constants disagree by ~18x, so every change output in `[546, 10_000)` sats is signed into existence and then permanently untracked — the wallet has no other path that credits it, and it is never aggregated or respent.

### Impact Explanation
The payment amounts feeding `SignableTransaction::new` derive from user-submitted `InInstruction` data attached to external outputs, i.e. attacker-influenced public input. An attacker who can shape a payment amount (e.g., repeatedly requesting transfers sized so `input_sat - payment_sat - fee` falls in `[546, 10_000)`) forces each spend to strand up to ~9,999 sats of the multisig's BTC per transaction. The funds remain controlled by the group key at the change offset, but no code path ever credits or respends them — an unbounded griefing drain on the protocol's Bitcoin holdings, with no cryptographic break required. Severity: Medium.

### Likelihood Explanation
Reachable by any user who can cause the processor to construct a spend: the only requirement is inducing `input_sat - payment_sat - fee_with_change ∈ [546, 10_000)`, a window of ~9,454 sats per spend. Given realistic UTXO sizes and fees, an attacker submitting crafted transfer instructions can hit this window deterministically or near-deterministically. The strand is silent (no error, output simply filtered), so it is repeatable across every spend the attacker can influence.

### Recommendation
Unify the dust thresholds: either raise the change-creation threshold in `SignableTransaction::new` (`networks/bitcoin/src/wallet/send.rs`) to the processor's `N::DUST = 10_000` (e.g., pass the network dust limit into `SignableTransaction::new` and skip change creation below it, letting the remainder become fee), or lower `N::DUST` in `processor/src/networks/bitcoin.rs` to the network-level 546 and handle the aggregation economics separately. Additionally, emit/log dropped sub-dust outputs instead of silently discarding them.

### Proof of Concept
Conceptual, anchored to `networks/bitcoin/src/wallet/send.rs` and `processor/src/multisigs/scanner.rs`:

```rust
// In SignableTransaction::new (send.rs ~L224-234):
// input_sat = 100_000, payment_sat = 88_000, fee_with_change ~ 1_000
// change value = 11_000 - ... craft so change = e.g. 5_000 sats
let change = Some(change_addr); // registered Change offset script
// value = 100_000 - 88_000 - fee_with_change; pick payment so value ∈ [546, 9_999]
// -> tx_outs gains TxOut { value: 5_000, script_pubkey: change }   // created & signed
```

```rust
// In the processor scanner loop (scanner.rs ~L562-566):
for output in network.get_outputs(&block, key).await {
  // Scanner::scan_transaction DOES return the 5_000-sat change output
  // (OutputType::Change offset is registered), but:
  if output.balance().amount.0 >= N::DUST { // 5_000 < 10_000
    outputs.push(output);                    // never reached
  }
}
// The 5_000-sat output is dropped: never emitted in ScannerEvent::Block,
// never persisted, never aggregated -> stranded at the multisig's change key.
```

The mismatch is directly verifiable: `DUST = 546` in `networks/bitcoin/src/wallet/send.rs:32` vs `DUST: u64 = 10_000` in `processor/src/networks/bitcoin.rs:638`, with the change-creation check at `send.rs:228-233` using the former and the crediting filter at `processor/src/multisigs/scanner.rs:564` using the latter.

### Citations

**File:** networks/bitcoin/src/wallet/send.rs (L32-32)
```rust
pub const DUST: u64 = 546;
```

**File:** networks/bitcoin/src/wallet/send.rs (L224-234)
```rust
    if let Some(change) = change {
      let (weight_with_change, vbytes_with_change) =
        Self::calculate_weight_vbytes(tx_ins.len(), payments, Some(&change));
      let fee_with_change = fee_per_vbyte * vbytes_with_change;
      if let Some(value) = input_sat.checked_sub(payment_sat + fee_with_change) {
        if value >= DUST {
          tx_outs.push(TxOut { value: Amount::from_sat(value), script_pubkey: change });
          weight = weight_with_change;
          needed_fee = fee_with_change;
        }
      }
```

**File:** networks/bitcoin/src/wallet/send.rs (L373-397)
```rust
    let mut cache = SighashCache::new(&self.tx.tx);
    // Sign committing to all inputs
    let prevouts = Prevouts::All(&self.tx.prevouts);

    let mut shares = Vec::with_capacity(self.sigs.len());
    let sigs = self
      .sigs
      .drain(..)
      .enumerate()
      .map(|(i, sig)| {
        let (sig, share) = sig.sign(
          commitments[i].clone(),
          cache
            .taproot_key_spend_signature_hash(i, &prevouts, TapSighashType::Default)
            // This should never happen since the inputs align with the TX the cache was
            // constructed with, and because i is always < prevouts.len()
            .expect("taproot_key_spend_signature_hash failed to return a hash")
            .as_ref(),
        )?;
        shares.push(share);
        Ok(sig)
      })
      .collect::<Result<_, _>>()?;

    Ok((TransactionSignatureMachine { tx: self.tx.tx, sigs }, shares))
```

**File:** processor/src/networks/bitcoin.rs (L337-340)
```rust
  register(
    OutputType::Change,
    *CHANGE_OFFSET.get_or_init(|| Secp256k1::hash_to_F(KEY_DST, b"change")),
  );
```

**File:** processor/src/networks/bitcoin.rs (L638-638)
```rust
  const DUST: u64 = 10_000;
```

**File:** processor/src/networks/bitcoin.rs (L686-700)
```rust
  async fn get_outputs(&self, block: &Self::Block, key: ProjectivePoint) -> Vec<Output> {
    let (scanner, _, kinds) = scanner(key);

    let mut outputs = vec![];
    // Skip the coinbase transaction which is burdened by maturity
    for tx in &block.txdata[1 ..] {
      for output in scanner.scan_transaction(tx) {
        let offset_repr = output.offset().to_repr();
        let offset_repr_ref: &[u8] = offset_repr.as_ref();
        let kind = kinds[offset_repr_ref];

        let output = Output { kind, presumed_origin: None, output, data: vec![] };
        assert_eq!(output.tx_id(), tx.id());
        outputs.push(output);
      }
```

**File:** processor/src/multisigs/scanner.rs (L562-566)
```rust
          for output in network.get_outputs(&block, key).await {
            assert_eq!(output.key(), key);
            if output.balance().amount.0 >= N::DUST {
              outputs.push(output);
            }
```
