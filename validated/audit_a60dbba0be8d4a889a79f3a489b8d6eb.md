### Title
Unbounded retry on unattainable `getrawtransaction` for an attacker-chosen deposit permanently stalls the Bitcoin scanner — (File: processor/src/networks/bitcoin.rs)

### Summary
The reported bug class is: an unprivileged party can send a small, unsolicited transfer that corrupts internal accounting/iteration and prevents users from withdrawing. In Serai, `Bitcoin::get_outputs` processes every confirmed transaction paying a scanned multisig address. For each such transaction it resolves `presumed_origin` by fetching the transaction spent by `tx.input[0]` via `getrawtransaction`, inside a `while tx.is_err()` loop with no error bound and no escape. An attacker who deposits dust to the external address in a transaction whose first input spends a transaction the node cannot serve permanently blocks `get_outputs` from returning, stalling the scanner and halting all further deposit crediting and withdrawal fulfillment. [1](#0-0) 

### Finding Description
`get_outputs` scans each non-coinbase transaction in a confirmed block; when it finds outputs matching a registered Serai script, it enters code that derives `presumed_origin` from `tx.input[0].previous_output`: [2](#0-1) 

```rust
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
```

The attacker fully controls `tx.input[0]` because they author the deposit transaction. `Rpc::get_transaction` is a thin `getrawtransaction` wrapper. [3](#0-2)  Bitcoin Core's `txindex` is off by default, and without it `getrawtransaction` only serves mempool transactions — any confirmed, non-mempool previous transaction returns an error forever. Even with `txindex`, a pruned/limited-index node, or a node that has dropped the transaction, produces the same permanent error. Because the retry loop has no exit condition, `get_outputs` never returns for that block. `get_outputs` is called per block by the scanning loop in `processor/src/multisigs/scanner.rs` (lines 492+), so one poisoned deposit halts processing of that block and every subsequent block. [4](#0-3) 

This mirrors the report exactly: a minimal unsolicited transfer (dust to the external address, analogous to 1 USDC to the Omnipool) exploits unconditional accounting of attacker-influenced data (`tx.input[0]`'s spent transaction, analogous to counting the donated idle balance in `totalUnderlying_`) to make the withdrawal pipeline unable to complete.

### Impact Explanation
While the loop spins, no new blocks are delivered as `ScannerEvent::Block`, so no new outputs are acknowledged, no `InInstruction`s are emitted, and no scheduler plans/signing rounds for payouts occur — a permanent DoS of Serai's Bitcoin deposits and withdrawals until manual intervention, triggered by a single cheap transaction from any unprivileged user.

### Likelihood Explanation
Requires only that the attacker broadcast a valid Bitcoin transaction paying a dust amount to Serai's public external address, with `input[0]` referencing a transaction the processor's node cannot retrieve. Whether this triggers in a given deployment depends on node configuration (`txindex`/pruning); the code itself assumes `getrawtransaction` is infallible and provides no fallback, timeout, or `NetworkError` propagation — the loop can only exit on success. I was unable to confirm whether Serai's deployment scripts enable `txindex` (the orchestration `run.sh` files contain a relevant match, but its exact flags were not verified), so the exploitability on the reference deployment is uncertain; the defect itself — an infinite retry on attacker-influenced RPC input inside the synchronous scan path — is unconditional.

### Recommendation
Bound the fetch: cap retries and return `NetworkError`/`Err` so the scanner can skip or defer the output rather than looping forever. Prefer deriving `presumed_origin` without `getrawtransaction` (e.g., `getrawtransaction` with the containing `blockhash`, `getblock` verbosity 2 on the spending block's ancestors, or treating origin resolution as best-effort and leaving `presumed_origin`/`data` empty on failure). At minimum, do not block output processing on a non-critical provenance field.

### Proof of Concept
1. Run a processor against a Bitcoin node without `txindex` (default) or against a pruned node.
2. Attacker creates and confirms TX `A` (any self-spend). Once `A` is confirmed and leaves the mempool, broadcast TX `B` whose `input[0]` spends `A`'s output and which pays ≥ dust to Serai's external P2TR address.
3. When `B` reaches `CONFIRMATIONS` depth, `get_outputs` scans it, enters the `while tx.is_err()` loop on `getrawtransaction(A)` — which permanently errors since `A` is neither in mempool nor indexed — and never returns.
4. The scanner emits no further `ScannerEvent::Block`s; all subsequent Bitcoin deposits and withdrawals stall indefinitely.

### Citations

**File:** processor/src/networks/bitcoin.rs (L686-737)
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
    }
```

**File:** networks/bitcoin/src/rpc.rs (L211-225)
```rust
  pub async fn get_transaction(&self, hash: &[u8; 32]) -> Result<Transaction, RpcError> {
    let hex = self.rpc_call::<String>("getrawtransaction", json!([hex::encode(hash)])).await?;
    let bytes: Vec<u8> = FromHex::from_hex(&hex)
      .map_err(|_| RpcError::InvalidResponse("node didn't use hex to encode the transaction"))?;
    let tx: Transaction = encode::deserialize(&bytes)
      .map_err(|_| RpcError::InvalidResponse("node sent an improperly serialized transaction"))?;

    let mut tx_hash = *tx.compute_txid().as_raw_hash().as_byte_array();
    tx_hash.reverse();
    if hash != &tx_hash {
      Err(RpcError::InvalidResponse("node replied with a different transaction"))?;
    }

    Ok(tx)
  }
```

**File:** processor/src/multisigs/scanner.rs (L492-503)
```rust
      for block_being_scanned in (ram_scanned + 1) ..= latest_block_to_scan {
        // Redo the checks for if we're too far ahead
        {
          let needing_ack = {
            let scanner_lock = scanner_hold.read().await;
            let scanner = scanner_lock.as_ref().unwrap();
            scanner.need_ack.front().copied()
          };

          if let Some(needing_ack) = needing_ack {
            let limit = needing_ack + N::CONFIRMATIONS;
            assert!(block_being_scanned <= limit);
```
