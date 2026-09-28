### Title
Attacker-flooded UTXOs make `SignableTransaction::new` fail, DoSing multisig spends - (File: networks/bitcoin/src/wallet/send.rs)

### Summary
The Surge report's bug class is an unprivileged party front-running/griefing a victim so the victim's legitimate state-changing operation deterministically reverts. In Serai's Bitcoin wallet, the analogous reachable surface is deposit-driven: anyone can send outputs to the multisig's publicly derivable script_pubkeys, those outputs become `ReceivedOutput`s via `Scanner::scan_transaction`, and when they are fed as `inputs` to `SignableTransaction::new`, the constructor deterministically fails (`TooLargeTransaction`), blocking construction of the spend transaction.

### Finding Description
`Scanner::scan_transaction` accepts any transaction output whose `script_pubkey` matches a registered script and returns a `ReceivedOutput` for it, with no authentication of the sender and no bound on how many matching outputs exist [1](#0-0) . The multisig's scripts are publicly computable: `p2tr_script_buf(key)` for the group key plus the deterministic `hash_to_F(KEY_DST, "branch"/"change"/"forward")` offsets [2](#0-1) . Each returned output becomes one `TxIn` in `SignableTransaction::new` [3](#0-2) , and the constructor hard-fails if the resulting weight exceeds `MAX_STANDARD_TX_WEIGHT` [4](#0-3) . It also unconditionally sums all provided inputs toward `input_sat`, so inputs cannot be partially excluded inside the function [5](#0-4) .

Like the Surge repay underflow, the griefing input is tiny relative to the victim's position (each attacker UTXO need only exceed the 546-sat dust floor filtered at scan time [6](#0-5) ) yet it corrupts the whole operation rather than just itself: one oversized input set makes every attempt to build the spend return `Err`, mirroring the revert-on-underflow DoS.

### Impact Explanation
A withdrawal/spend that must aggregate the received outputs cannot be constructed: `SignableTransaction::new` returns `TransactionError::TooLargeTransaction` (or `NotEnoughFunds` if many inputs force fee growth), permanently blocking the transaction build path until the input set is manually pruned. The attacker's cost is only the dust-threshold value per blocking UTXO, and the sent funds remain spendable by the multisig, so the attacker does not burn meaningful capital — same asymmetric-cost profile as the 1-token grief in the original report.

### Likelihood Explanation
Requires no privileged position: any Bitcoin user can pay to the multisig's public P2TR scripts. It only succeeds if the consumer passes the full scanned output set into `SignableTransaction::new` without chunking; the crate offers no built-in input-limit guard, so a caller that naively spends everything it scanned is vulnerable.

### Recommendation
Enforce an input-count/weight budget when selecting `inputs` for `SignableTransaction::new` (cap inputs so `weight <= MAX_STANDARD_TX_WEIGHT`, and prefer consolidating small outputs in dedicated transactions), and/or make `Scanner`/the consumer ignore outputs that cannot be productively included.

### Proof of Concept
1. Observe the multisig group key; compute `p2tr_script_buf(key)` (and the `branch`/`change`/`forward` offset scripts) — all public.
2. In one transaction (or several), create hundreds of ≥546-sat outputs to those script_pubkeys.
3. `Scanner::scan_transaction` returns one `ReceivedOutput` per matching vout [7](#0-6) .
4. The spender calls `SignableTransaction::new(all_outputs, payments, change, None, fee)`; the `tx_ins` vector grows linearly with attacker outputs [3](#0-2) , `calculate_weight_vbytes` scales accordingly, and construction fails at the weight check [4](#0-3)  — the spend is DoSed exactly as the underflow DoSed `repay`.

### Citations

**File:** networks/bitcoin/src/wallet/mod.rs (L199-214)
```rust
  pub fn scan_transaction(&self, tx: &Transaction) -> Vec<ReceivedOutput> {
    let mut res = Vec::new();
    for (vout, output) in tx.output.iter().enumerate() {
      // If the vout index exceeds 2**32, stop scanning outputs
      let Ok(vout) = u32::try_from(vout) else { break };

      if let Some(offset) = self.scripts.get(&output.script_pubkey) {
        res.push(ReceivedOutput {
          offset: *offset,
          output: output.clone(),
          outpoint: OutPoint::new(tx.compute_txid(), vout),
        });
      }
    }
    res
  }
```

**File:** processor/src/networks/bitcoin.rs (L333-344)
```rust
  register(
    OutputType::Branch,
    *BRANCH_OFFSET.get_or_init(|| Secp256k1::hash_to_F(KEY_DST, b"branch")),
  );
  register(
    OutputType::Change,
    *CHANGE_OFFSET.get_or_init(|| Secp256k1::hash_to_F(KEY_DST, b"change")),
  );
  register(
    OutputType::Forwarded,
    *FORWARD_OFFSET.get_or_init(|| Secp256k1::hash_to_F(KEY_DST, b"forward")),
  );
```

**File:** networks/bitcoin/src/wallet/send.rs (L175-176)
```rust
    let input_sat = inputs.iter().map(|input| input.output.value.to_sat()).sum::<u64>();
    let offsets = inputs.iter().map(|input| input.offset).collect();
```

**File:** networks/bitcoin/src/wallet/send.rs (L177-185)
```rust
    let tx_ins = inputs
      .iter()
      .map(|input| TxIn {
        previous_output: input.outpoint,
        script_sig: ScriptBuf::new(),
        sequence: Sequence::MAX,
        witness: Witness::new(),
      })
      .collect::<Vec<_>>();
```

**File:** networks/bitcoin/src/wallet/send.rs (L241-243)
```rust
    if weight > u64::from(bitcoin::policy::MAX_STANDARD_TX_WEIGHT) {
      Err(TransactionError::TooLargeTransaction)?;
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
