### Title
Dust outputs sent by anyone to a Serai Bitcoin multisig are registered as spendable UTXOs and unconditionally swept into aggregation/spend transactions, burning fees in excess of their value - (File: networks/bitcoin/src/wallet/mod.rs)

### Summary
The reported bug class is "the function consumes the contract's *entire* token balance while only accounting for the amount attached to the call, letting an attacker pre-position funds and bypass the intended economics." Serai's Bitcoin wallet exhibits the same shape: `Scanner::scan_transaction` accepts **any** on-chain output paying to the multisig's script_pubkey — with no minimum-value check — and the UTXO scheduler then drains the *entire* UTXO set into plans, so attacker-planted dust is consumed as if it were a legitimate deposit, while its fee cost exceeds its value.

### Finding Description
`Scanner` matches outputs purely by `script_pubkey`. Any output whose script is in `self.scripts` is returned as a `ReceivedOutput` with no check on `output.value`: [1](#0-0) 

An unprivileged party can send a transaction creating arbitrarily small outputs (e.g. 1 sat, far below the 546-sat `DUST` relay policy defined in `send.rs`) to the multisig's deposit, branch, or change address — all of which are derived from the publicly known group key. These outputs are registered as normal UTXOs.

In `Scheduler::schedule`, *all* stored UTXOs are drained and chunked into aggregation plans sent to the change address, and the first chunk funds payments — there is no filter on output value and no opt-out: [2](#0-1) 

`SignableTransaction::new` then consumes every input's full value (`input_sat = sum(inputs)`), with leftover going to change or the fee. Each Taproot input adds ~58 vbytes to the transaction, so a 1-sat input costs `fee_per_vbyte * 58` sats while contributing 1 sat — a guaranteed net loss per planted output. This is exactly the report's pattern: assets placed by an external party are swept ("all available balance") under fee economics that assume only protocol-initiated deposits.

### Impact Explanation
- Each dust output planted by an attacker is a forced net loss: the multisig signs transactions spending inputs worth less than the marginal fee they add. Repeating the attack drains the multisig's balance proportionally to the attacker's spend.
- Planted outputs occupy slots in `N::MAX_INPUTS` chunks; sustained spam forces continuous aggregation transactions (`force_spend`/`utxo_chunks` paths at `utxo.rs:376-431`), multiplying fee burn.
- The outputs are "reported received that are not spendable": `ReceivedOutput`s below dust policy (or below their own spend cost) are treated as real balance by `Scheduler` and `SignableTransaction`, yet cannot be spent economically — matching an accepted impact category.

### Likelihood Explanation
The attack requires only the public Taproot address of an active multisig and the cost of broadcasting dust transactions. The addresses are deterministic (derived from the group key via `branch_address`, `change_address`, `forward_address`) and appear on-chain. No validator key, collusion, or internal access is needed — identical to the report's attack path of transferring tokens to the locker directly. However, the loss per output is bounded (~58 vbytes × fee rate), and the attacker's cost scales linearly with damage, keeping this at Medium rather than High.

### Recommendation
Enforce a minimum-value filter at intake: in `Scanner::scan_transaction` (or where `ReceivedOutput`s are handed to the scheduler), drop outputs whose `value` is below `DUST` or below the marginal fee cost of spending them (`fee_per_vbyte * input_vbytes`). Alternatively, have `Scheduler::schedule` partition "economically spendable" UTXOs from dust and never include the latter as inputs, so attacker-planted funds cannot silently consume the multisig's balance.

### Proof of Concept
1. Obtain the multisig's change/deposit script_pubkey (public on-chain, derived from the group key).
2. Broadcast a transaction with N outputs of 1 sat each to that script_pubkey.
3. `Scanner::scan_transaction` (mod.rs:199-214) returns N `ReceivedOutput`s — no value check rejects them.
4. On the next `Scheduler::schedule` call, `self.utxos` is fully drained (`utxo.rs:358`) and chunked into aggregation plans (`utxo.rs:376-385`), plus `force_spend` plans (`utxo.rs:423-431`).
5. `SignableTransaction::new` sums all inputs into `input_sat` and signs the tx; each 1-sat input contributes ~58 vbytes of weight. At any nonzero fee rate, `fee` exceeds `sum(inputs)` for the dust portion — the multisig pays real funds to consolidate worthless outputs. Assert: `SignableTransaction::fee()` increases by more than the dust inputs' total value once dust is included, i.e. the multisig balance strictly decreases despite "receiving" funds.

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

**File:** processor/src/multisigs/scheduler/utxo.rs (L350-385)
```rust
    // Sort UTXOs so the highest valued ones are first
    self.utxos.sort_by(|a, b| a.balance().amount.0.cmp(&b.balance().amount.0).reverse());

    // We always want to aggregate our UTXOs into a single UTXO in the name of simplicity
    // We may have more UTXOs than will fit into a TX though
    // We use the most valuable UTXOs to handle our current payments, and we return aggregation TXs
    // for the rest of the inputs
    // Since we do multiple aggregation TXs at once, this will execute in logarithmic time
    let utxos = self.utxos.drain(..).collect::<Vec<_>>();
    let mut utxo_chunks =
      utxos.chunks(N::MAX_INPUTS).map(<[<N as Network>::Output]>::to_vec).collect::<Vec<_>>();

    // Use the first chunk for any scheduled payments, since it has the most value
    let utxos = utxo_chunks.remove(0);

    // If the last chunk exists and only has one output, don't try aggregating it
    // Set it to be restored to UTXO set
    let mut to_restore = None;
    if let Some(mut chunk) = utxo_chunks.pop() {
      if chunk.len() == 1 {
        to_restore = Some(chunk.pop().unwrap());
      } else {
        utxo_chunks.push(chunk);
      }
    }

    for chunk in utxo_chunks.drain(..) {
      log::debug!("aggregating a chunk of {} inputs", chunk.len());
      plans.push(Plan {
        key: self.key,
        inputs: chunk,
        payments: vec![],
        change: Some(N::change_address(key_for_any_change).unwrap()),
        scheduler_addendum: (),
      })
    }
```
