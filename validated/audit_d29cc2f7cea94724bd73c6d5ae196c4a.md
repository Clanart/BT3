### Title
Self-payment to the multisig's own deposit script is re-scanned as a new External deposit, double-crediting funds - (File: networks/bitcoin/src/wallet/mod.rs)

### Summary
The report's bug class — a self-directed transfer being counted as new credited value — maps onto Serai's Bitcoin wallet scanner. `Scanner::scan_transaction` identifies received funds purely by `script_pubkey` membership in its script→offset map, with no check that the paying transaction wasn't constructed and signed by Serai itself. Any output of a Serai-signed transaction paying to the zero-offset (External) script is re-reported as a fresh `OutputType::External` deposit, which `scanner_event_to_multisig_event` converts into a new `InInstruction` — crediting the same coins twice.

### Finding Description
`Scanner::scan_transaction` iterates every output of every scanned transaction and emits a `ReceivedOutput` whenever `self.scripts.get(&output.script_pubkey)` matches, matching on the script alone [1](#0-0) . The external deposit script is just the P2TR script of the bare group key (`scripts.insert(p2tr_script_buf(key)?, Scalar::ZERO)`) [2](#0-1) . In `get_outputs`, every non-coinbase transaction in the block — including transactions Serai itself created and signed via `SignableTransaction`/`multisig` — is scanned, and every match whose offset maps to `Scalar::ZERO` is classified `OutputType::External` [3](#0-2) . `presumed_origin` is populated (from the first input's spent output) but is informational only; nothing filters outputs created by Serai's own spends [4](#0-3) . Downstream, `scanner_event_to_multisig_event` retains all `External` outputs and derives instructions from them via `instruction_from_output` [5](#0-4) . Since `register_offset` returns the actually-used offset and is caller-supplied, an integrator may also register the zero-equivalent offset, but the plain External path alone suffices: the deposit address is public and computable from the group key.

### Impact Explanation
A withdrawal/batch payment whose destination is the multisig's own External deposit address produces a transaction output that is simultaneously a spend of existing multisig funds and a newly "received" deposit. The scanner emits it as `OutputType::External`, and the processor issues a fresh `InInstruction`, minting Serai-side credit for funds that were already multisig property — a double-credit / funds-reported-received-that-aren't-new-deposits condition. Analogously, any third party who learns the deposit script can craft outputs that look like fresh deposits in contexts where provenance matters (e.g., forwarded-output accounting races the same matching path).

### Likelihood Explanation
The trigger is a payment to the multisig's own public deposit address, which is trivially derivable (P2TR of the tweaked group key). Whether a Serai-signed plan can be induced to pay that address depends on scheduler validation of payment destinations, which is outside the in-scope `networks/bitcoin` code and could not be fully verified here; however, nothing in the scanning or output-classification layer itself prevents the re-crediting, and the "pay your own deposit address" path is a realistic operational edge (batch forwards, refunds, user error, or deliberate withdrawal-destination choice). The code comments acknowledge only uniqueness of output IDs, not self-origination, as a safety property [6](#0-5) .

### Recommendation
Filter scanned outputs by provenance: ignore outputs of transactions authored by the multisig itself (e.g., recognize Serai-created TXIDs/plans, or exclude outputs whose `presumed_origin` resolves to a script controlled by the same key tree). At minimum, `Scanner`/`get_outputs` should not classify an output as `OutputType::External` when the spending transaction spends other outputs of the same multisig key, and the scheduler should reject payments addressed to any script in the scanner's registered set.

### Proof of Concept
1. Obtain the multisig group key `K` and compute its deposit script `p2tr_script_buf(K)` — the same `ScriptBuf` `Scanner::new` registers under `Scalar::ZERO` [2](#0-1) .
2. Cause Serai to sign a transaction containing an output paying to that script (e.g., a withdrawal whose destination address is the multisig's own external address, via `SignableTransaction::new`/`multisig`).
3. When the transaction confirms, `get_outputs` → `Scanner::scan_transaction` matches `output.script_pubkey`, emits a `ReceivedOutput` with offset `ZERO`, classified `External` [3](#0-2) .
4. `scanner_event_to_multisig_event` retains it and `instruction_from_output` produces a new `InInstruction` crediting the amount again — the same BTC was both spent and re-deposited.

Caveat: I could not fully verify whether upstream scheduler/instruction code rejects payment destinations equal to Serai's own deposit scripts; if such a check exists outside the audited crates, the reachable impact reduces to foot-gun rather than exploitable double-credit.

### Citations

**File:** networks/bitcoin/src/wallet/mod.rs (L162-166)
```rust
  pub fn new(key: ProjectivePoint) -> Option<Scanner> {
    let mut scripts = HashMap::new();
    scripts.insert(p2tr_script_buf(key)?, Scalar::ZERO);
    Some(Scanner { key, scripts })
  }
```

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

**File:** processor/src/networks/bitcoin.rs (L686-701)
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

**File:** processor/src/networks/bitcoin.rs (L707-737)
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
      let data = Self::extract_serai_data(tx);
      for output in &mut outputs {
        if output.kind == OutputType::External {
          output.data.clone_from(&data);
        }
        output.presumed_origin.clone_from(&presumed_origin);
      }
    }
```

**File:** processor/src/multisigs/mod.rs (L835-860)
```rust
        // If the remaining outputs aren't externally received funds, don't handle them as
        // instructions
        outputs.retain(|output| output.kind() == OutputType::External);

        // These plans are of limited context. They're only allowed the outputs newly received
        // within this block and are intended to handle forwarding transactions/refunds
        let mut plans = vec![];

        // If the old multisig is explicitly only supposed to forward, create all such plans now
        if step == RotationStep::ForwardFromExisting {
          let mut i = 0;
          while i < outputs.len() {
            let output = &outputs[i];
            let plans = &mut plans;
            let txn = &mut *txn;

            #[allow(clippy::redundant_closure_call)]
            let should_retain = (|| async move {
              // If this output doesn't belong to the existing multisig, it shouldn't be forwarded
              if output.key() != self.existing.as_ref().unwrap().key {
                return true;
              }

              let plans_at_start = plans.len();
              let (refund_to, instruction) = instruction_from_output::<N>(output);
              if let Some(mut instruction) = instruction {
```

**File:** processor/src/multisigs/scanner.rs (L593-638)
```rust
        // Panic if we've already seen these outputs
        for output in &outputs {
          let id = output.id();
          info!(
            "block {} had output {} worth {:?}",
            hex::encode(&block_id),
            hex::encode(&id),
            output.balance(),
          );

          // On Bitcoin, the output ID should be unique for a given chain
          // On Monero, it's trivial to make an output sharing an ID with another
          // We should only scan outputs with valid IDs however, which will be unique

          /*
            The safety of this code must satisfy the following conditions:
            1) seen is not set for the first occurrence
            2) seen is set for any future occurrence

            seen is only written to after this code completes. Accordingly, it cannot be set
            before the first occurrence UNLESSS it's set, yet the last scanned block isn't.
            They are both written in the same database transaction, preventing this.

            As for future occurrences, the RAM entry ensures they're handled properly even if
            the database has yet to be set.

            On reboot, which will clear the RAM, if seen wasn't set, neither was latest scanned
            block. Accordingly, this will scan from some prior block, re-populating the RAM.

            If seen was set, then this will be successfully read.

            There's also no concern ram_outputs was pruned, yet seen wasn't set, as pruning
            from ram_outputs will acquire a write lock (preventing this code from acquiring
            its own write lock and running), and during its holding of the write lock, it
            commits the transaction setting seen and the latest scanned block.

            This last case isn't true. Committing seen/latest_scanned_block happens after
            relinquishing the write lock.

            TODO2: Only update ram_outputs after committing the TXN in question.
          */
          let seen = ScannerDb::<N, D>::seen(&db, &id);
          let id = id.as_ref().to_vec();
          if seen || scanner.ram_outputs.contains(&id) {
            panic!("scanned an output multiple times");
          }
```
