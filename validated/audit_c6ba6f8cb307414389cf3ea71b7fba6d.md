### Title
Unauthenticated output-kind spoofing: any external party can inject internally-classified (`Branch`/`Change`/`Forwarded`) outputs into the Scanner by paying to publicly derivable offset addresses - ([File: networks/bitcoin/src/wallet/mod.rs](networks/bitcoin/src/wallet/mod.rs))

### Summary
The GitLab advisory (CVE-2025-1299) is a missing-authorization flaw: an unprivileged user gains access to a protected resource because the system authenticates only a publicly knowable attribute. The same shape exists in Serai's Bitcoin scanner. `Scanner::scan_transaction` classifies an output purely by whether its `script_pubkey` appears in the `scripts` map, and `Bitcoin::get_outputs` then assigns it a privileged internal `OutputType` (`Branch`, `Change`, `Forwarded`) via the deterministic offset table. Since all offset addresses are public functions of the group key (`branch_address`, `change_address`, `forward_address`), any unprivileged party on the Bitcoin network can craft a transaction that the processor ingests as an internally-produced output, bypassing the checks applied to genuine external deposits.

### Finding Description
`Scanner::scan_transaction` does a bare `self.scripts.get(&output.script_pubkey)` lookup and emits a `ReceivedOutput` carrying the stored offset, with no distinction of *who* created the output or *why*. [1](#0-0) 

`Bitcoin::get_outputs` maps each scanned output's offset to a `kind` via `kinds[offset_repr_ref]`, so an attacker-chosen destination address directly selects the output's trust classification. `presumed_origin` and `data` are attached from attacker-controlled transaction fields (`tx.input[0]`'s spent output and the witness `InInstruction`), and `presumed_origin` is populated for *all* kinds, not just `External`. [2](#0-1) 

The offset addresses are unprivileged information: `register_offset` derives them from `key + GENERATOR * offset`, and the `OutputType` → offset mapping is a fixed deterministic `scanner(key)` table recomputed on every call. [3](#0-2) [4](#0-3) 

The only mitigation applied is the `N::DUST` floor and the coinbase skip (`block.txdata[1 ..]`) — neither authorizes the output. [5](#0-4) [6](#0-5) 

### Impact Explanation
An external attacker can mint an output that the processor records as `OutputType::Change` or `OutputType::Branch` — classifications reserved for outputs the multisig itself produced. Downstream consumers (scheduler/plan pipeline, `Output::key()`, balance accounting) treat `kind` as proof of provenance: `Branch`/`Change` outputs are expected to result from internal `Plan` execution, so spoofed ones let an attacker inject unplanned internal outputs, poison `presumed_origin` attribution (set from the attacker's own `tx.input[0]`), and create records whose origin/metadata were never authorized. This is the Serai analog of "crafted request reads/acts on a protected resource without authorization": a crafted Bitcoin transaction obtains an internal trust label using only public data. At minimum it corrupts the processor's ledger of internally-generated funds; depending on scheduler handling of `Branch`/`Forwarded` kinds it can trigger forwarding/spend paths that were never scheduled.

### Likelihood Explanation
Exploitation requires only broadcasting a valid Bitcoin transaction paying ≥ `DUST` (10,000 sats) to a deterministic, publicly computable P2TR address. No keys, collusion, or validator position is needed — the exact reachability profile of the original advisory. [7](#0-6) 

### Recommendation
Distinguish provenance instead of trusting `script_pubkey` alone: mark scanned outputs as `External` unless the outpoint was produced by a known signed `Plan`/multisig transaction (e.g., match against expected change/branch outpoints for pending plans rather than the offset table), and only assign `Change`/`Branch`/`Forwarded` when the spending transaction is one the multisig created. At minimum, do not populate `presumed_origin`/`data` on non-`External` kinds, and treat unsolicited payments to internal addresses as `External`-class deposits.

### Proof of Concept
1. Obtain the multisig group key (public).
2. Locally compute `scanner(key)` to recover the `OutputType::Change` (or `Branch`) offset — pure public computation, no authorization. [8](#0-7) 
3. Broadcast `tx_evil` paying 10,001 sats to `p2tr_script_buf(key + GENERATOR * offset_change)`, spending from the attacker's own input.
4. `get_outputs` scans `tx_evil`, hits `scripts[script_pubkey]`, assigns `kind = OutputType::Change`, and sets `presumed_origin` from the attacker's input — emitting an `Output` indistinguishable in kind from genuine internal change. [9](#0-8) 
5. The processor's `outputs.push(output)` in `scanner.rs` now contains an attacker-controlled, internally-classified output that was never produced by an authorized multisig plan. [5](#0-4) 

Note: I was unable to fully trace the `Scheduler`'s consumption of `Branch`/`Forwarded` outputs within the iteration budget; the concrete downstream effect (misaccounting vs. triggering unscheduled spends) depends on that code, which is outside the indexed files inspected here.

### Citations

**File:** networks/bitcoin/src/wallet/mod.rs (L180-196)
```rust
  pub fn register_offset(&mut self, mut offset: Scalar) -> Option<Scalar> {
    // This loop will terminate as soon as an even point is found, with any point having a ~50%
    // chance of being even
    // That means this should terminate within a very small amount of iterations
    loop {
      match p2tr_script_buf(self.key + (ProjectivePoint::GENERATOR * offset)) {
        Some(script) => {
          if self.scripts.contains_key(&script) {
            None?;
          }
          self.scripts.insert(script, offset);
          return Some(offset);
        }
        None => offset += Scalar::ONE,
      }
    }
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

**File:** processor/src/networks/bitcoin.rs (L638-638)
```rust
  const DUST: u64 = 10_000;
```

**File:** processor/src/networks/bitcoin.rs (L661-674)
```rust
  fn branch_address(key: ProjectivePoint) -> Option<Address> {
    let (_, offsets, _) = scanner(key);
    Some(address_from_key(key + (ProjectivePoint::GENERATOR * offsets[&OutputType::Branch])))
  }

  fn change_address(key: ProjectivePoint) -> Option<Address> {
    let (_, offsets, _) = scanner(key);
    Some(address_from_key(key + (ProjectivePoint::GENERATOR * offsets[&OutputType::Change])))
  }

  fn forward_address(key: ProjectivePoint) -> Option<Address> {
    let (_, offsets, _) = scanner(key);
    Some(address_from_key(key + (ProjectivePoint::GENERATOR * offsets[&OutputType::Forwarded])))
  }
```

**File:** processor/src/networks/bitcoin.rs (L686-739)
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

    outputs
```

**File:** processor/src/multisigs/scanner.rs (L562-567)
```rust
          for output in network.get_outputs(&block, key).await {
            assert_eq!(output.key(), key);
            if output.balance().amount.0 >= N::DUST {
              outputs.push(output);
            }
          }
```
