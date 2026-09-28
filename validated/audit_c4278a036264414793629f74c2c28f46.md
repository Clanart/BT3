### Title
Deposit origin/refund recipient inferred from `tx.input[0]` can misattribute the sender - (File: processor/src/networks/bitcoin.rs)

### Summary
The Cooler.sol bug is that refunds/collateral are sent to a fixed contract-level party (`owner()`) rather than the actual requester, so collateral can be returned to the wrong recipient whenever the contract is used outside the assumed Olympus integration. The Serai analog lives in `Bitcoin::get_outputs`, where the origin (the address refunds and sender attribution are based on) of every scanned deposit is heuristically set to the script pubkey funding `tx.input[0]` — an attacker- and context-controllable field that does not necessarily identify the true depositor. [1](#0-0) 

### Finding Description
When the processor scans a block, `get_outputs` scans each non-coinbase transaction for outputs paying to a registered multisig script and then populates `presumed_origin` for all of them by taking `tx.input[0].previous_output`, fetching that spent transaction over RPC, and using `spent_output.script_pubkey` as the origin address for every output of the transaction. [2](#0-1) [3](#0-2) 

Two distinct problems mirror the Cooler issue:

1. **Single representative for a heterogeneous sender set.** A Bitcoin transaction can have many inputs funded by different parties (e.g., batched deposits, CoinJoin-style constructions, or a custodial service funding a deposit on behalf of a user). `presumed_origin` unconditionally picks `input[0]`'s funder, so any refund or sender-dependent logic attributes the deposit to whichever party happened to be serialized first — which may not be the party that intended to deposit, exactly as Cooler sends collateral to `owner()` rather than the actual requester.

2. **Attacker-controlled input ordering.** The input ordering of a transaction is chosen by its builder. A malicious sender can deliberately place an input spending an unrelated/uncontrolled-by-intended-recipient output first, or a third-party transaction assembler can order inputs arbitrarily. The code itself acknowledges this is only a guess: "This may identify the P2WSH output *embedding the InInstruction* as the origin" with a `TODO`. [4](#0-3) 

The value is then trusted: it is written into every `Output` produced from that transaction (`output.presumed_origin.clone_from(&presumed_origin)`) and propagated through `Output::presumed_origin()`, which is consumed by the multisig layer (`processor/src/multisigs/mod.rs`) for sender attribution/refunds of `External` outputs. [5](#0-4) [6](#0-5) 

### Impact Explanation
If Serai refunds a deposit (e.g., an `External` output whose embedded `InInstruction` data is malformed or unprocessable) to `presumed_origin`, funds can be returned to an address controlled by a different input funder than the actual depositor — loss of funds for the depositor or crediting/refunding the wrong party, requiring manual recovery. This is directly reachable by any unprivileged party who can broadcast a Bitcoin transaction paying a Serai external address: they fully control `tx.input[0]` and its spent output, hence fully control the reported origin.

### Likelihood Explanation
Any user or service constructing a deposit transaction controls input ordering. Multi-input funding transactions (the norm in Bitcoin, and mandatory for services batching deposits) make `input[0]` an arbitrary representative rather than the depositor. The heuristic is applied unconditionally to every output-bearing transaction in `get_outputs`, so no special conditions are required beyond a deposit transaction whose first input does not represent the intended refund recipient.

### Recommendation
Do not derive a refund/attribution address from `tx.input[0]`'s spent output as authoritative metadata. Either require the refund/origin address to be explicitly committed inside the `InInstruction` data (`extract_serai_data`), or treat `presumed_origin` as informational-only and never route funds to it without an explicit, user-specified refund address bound to the deposit.

### Proof of Concept
1. Attacker (or a batching intermediary) builds a transaction `tx` paying a Serai external address, placing as `tx.input[0]` an input that spends an output whose `script_pubkey` resolves to an address they control (or that belongs to an unrelated third party), while other inputs fund the deposit.
2. `Bitcoin::get_outputs` scans the block, matches the output via `scanner.scan_transaction`, then sets `presumed_origin = Address::new(spent_output_of_input_0.script_pubkey)` for the deposit. [7](#0-6) 
3. Any downstream refund/attribution based on `output.presumed_origin()` returns funds to the `input[0]` funder rather than the actual depositor — the same wrong-recipient refund pattern as Cooler's `owner()`-based collateral return.

### Citations

**File:** processor/src/networks/bitcoin.rs (L124-126)
```rust
  fn presumed_origin(&self) -> Option<Address> {
    self.presumed_origin.clone()
  }
```

**File:** processor/src/networks/bitcoin.rs (L686-699)
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
```

**File:** processor/src/networks/bitcoin.rs (L707-729)
```rust
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
```

**File:** processor/src/networks/bitcoin.rs (L731-736)
```rust
      for output in &mut outputs {
        if output.kind == OutputType::External {
          output.data.clone_from(&data);
        }
        output.presumed_origin.clone_from(&presumed_origin);
      }
```
