### Title
Cross-transaction contamination of `presumed_origin`/`data` for all scanned outputs in a block - (File: processor/src/networks/bitcoin.rs)

### Summary
`Bitcoin::get_outputs` accumulates scanned outputs into a single `outputs` vector spanning every transaction in a block, but the code that assigns `presumed_origin` and `data` iterates over the *entire accumulated vector* for each matching transaction. An attacker who gets a Serai-bound output into a later transaction of the same block overwrites the origin and InInstruction data of every earlier Serai output in that block, misattributing deposits and redirecting origin-derived accounting (e.g. refunds).

### Finding Description
In `get_outputs`, `outputs` is declared once before the `for tx in &block.txdata[1 ..]` loop and never cleared between transactions [1](#0-0) . For each transaction, matched outputs are pushed, then `presumed_origin` (derived from `tx.input[0]`'s spent output address) and `data` (from `extract_serai_data(tx)`) are written to `&mut outputs` — the whole vector, not just this tx's new outputs [2](#0-1) .

The analog to the Ambient "surplus collateral accounting" flaw: the accounting metadata of an earlier deposit is rewritten by a later, unrelated operation, reachable purely by an unprivileged party broadcasting a Bitcoin transaction. An attacker mines/relays a transaction paying ≥ `N::DUST` (10,000 sats) to any registered Serai script (external, branch, change, or forward — all computable from the public group key) in the same block as a victim deposit. That attacker's `tx.input[0]` origin and attacker-controlled `data` then overwrite the victim output's `presumed_origin` and, if the victim output is `External`, its `data`.

`presumed_origin` and `data` are populated for outputs of all kinds in the same loop [3](#0-2) , and `data` is extracted per-tx via `Self::extract_serai_data(tx)` [4](#0-3) . These fields feed the processor's output accounting (deposit attribution / refund routing).

### Impact Explanation
Funds accounting corruption: a victim's deposit output is recorded with the attacker's address as `presumed_origin` and attacker-controlled `data`. If the protocol later uses `presumed_origin` to route refunds or `data` (the InInstruction payload) to determine crediting, the attacker can divert refund flows or misroute the credited instruction — a concrete "funds reported/misrouted" impact, not merely cosmetic. Cost to the attacker is one ≥10,000 sat output plus fees.

### Likelihood Explanation
Requires the attacker's tx to land in the same confirmed block as a Serai-bound transaction and pay above the dust threshold — fully under attacker control (they can monitor the mempool and cpfp/bundle). The overwrite is deterministic whenever both conditions hold.

### Recommendation
Scope the origin/data assignment to only the outputs pushed by the current transaction (e.g., track `outputs.len()` before scanning `tx` and iterate `outputs[prev_len..]`, or build a per-tx vector and extend). Additionally consider skipping `presumed_origin`/`data` for non-`External` kinds entirely.

### Proof of Concept
1. Victim broadcasts tx A paying ≥ dust to Serai's external script, with input[0] = victim address and InInstruction data D_v. Attacker observes it in the mempool.
2. Attacker broadcasts tx B in the same block paying ≥ 10,000 sats to Serai's change/forward script, input[0] = attacker address, embedding attacker-chosen data D_a.
3. `get_outputs(block, key)` pushes A's output, assigns origin=victim/data=D_v; then pushes B's output and loops `for output in &mut outputs`, overwriting A's output origin to attacker and (if External) data to D_a.
4. Recorded output: victim's funds with attacker's `presumed_origin`/data.

### Citations

**File:** processor/src/networks/bitcoin.rs (L689-700)
```rust
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

**File:** processor/src/networks/bitcoin.rs (L702-736)
```rust
      if outputs.is_empty() {
        continue;
      }

      // populate the outputs with the origin and data
      let presumed_origin = {
        // This may identify the P2WSH output *embedding the InInstruction* as the origin, which
        // would be a bit trickier to spend that a traditional output...
        // There's no risk of the InInstruction going missing as it'd already be on-chain though
        // We *could* parse out the script *without the InInstruction prefix* and declare that the
        // origin
        // TODO
        let spent_output = {
          let input = &tx.input[0];
          let mut spent_tx = input.previous_output.txid.as_raw_hash().to_byte_array();
          spent_tx.reverse();
          let mut tx;
          while {
            tx = self.rpc.get_transaction(&spent_tx).await;
            tx.is_err()
          } {
            log::error!("couldn't get transaction from bitcoin node: {tx:?}");
            sleep(Duration::from_secs(5)).await;
          }
          tx.unwrap().output.swap_remove(usize::try_from(input.previous_output.vout).unwrap())
        };
        Address::new(spent_output.script_pubkey)
      };
      let data = Self::extract_serai_data(tx);
      for output in &mut outputs {
        if output.kind == OutputType::External {
          output.data.clone_from(&data);
        }
        output.presumed_origin.clone_from(&presumed_origin);
      }
```
