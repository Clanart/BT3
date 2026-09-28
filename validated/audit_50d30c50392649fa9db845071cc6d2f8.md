### Title
Attacker can forge scanned-output classification and presumed origin by paying directly to internal offset addresses - (File: processor/src/networks/bitcoin.rs, networks/bitcoin/src/wallet/mod.rs)

### Summary
The Bitcoin scanner classifies every matched output purely by `script_pubkey` lookup, and `get_outputs` labels the output's `kind` (`External`, `Branch`, `Change`, `Forwarded`) solely from the registered offset table, while `presumed_origin` is taken verbatim from the script_pubkey of whatever output the attacker chose as `tx.input[0]`. An unprivileged sender can therefore craft a transaction whose outputs Serai reports with a forged internal `kind` and an arbitrary `presumed_origin` — the on-chain analogue of Chrome's Autofill UI obfuscation: the metadata the processor relies on to interpret a deposit no longer reflects reality.

### Finding Description
`Scanner::scan_transaction` returns a `ReceivedOutput` for any `tx.output` whose `script_pubkey` is in `self.scripts`, with no other check [1](#0-0) . The script set includes not only the deposit key but every offset registered via `register_offset`, which increments the offset until `p2tr_script_buf` yields an even-Y tweaked key [2](#0-1) . Those offset addresses are deterministic functions of the public group key (`address_from_key(key + GENERATOR * offset)` for `OutputType::Branch`, `Change`, `Forwarded`), so any observer can compute and pay them directly [3](#0-2) .

In `get_outputs`, the scanned output's `kind` is assigned from the offset table (`kinds[offset_repr_ref]`), and `presumed_origin` is set to the script_pubkey of `tx.input[0].previous_output` — a value fully chosen by the sender [4](#0-3) . Only `OutputType::External` outputs receive the transaction's Serai data payload; Branch/Change/Forwarded outputs get no data and a spoofed origin [5](#0-4) . The result is then pushed downstream as a confirmed `Output` once `balance >= DUST` [6](#0-5) .

### Impact Explanation
An attacker can make an externally-sent payment be reported to the Serai processor as an internal `Branch`, `Change`, or `Forwarded` output, or as an `External` deposit carrying a fabricated `presumed_origin` (e.g., another Serai vault address). Protocol logic that treats change/branch outputs as protocol-owned liquidity, or that uses `presumed_origin` for refund/attribution, will act on attacker-forged metadata — enabling misattribution of funds, deposits processed under the wrong classification, or refunds directed to an attacker-chosen address. The output is genuinely spendable by the multisig, so this is not a theft of the output itself; it is a misclassification/forged-provenance issue matching the CVE's "obfuscated security UI" class.

### Likelihood Explanation
Exploitation requires only sending a standard Bitcoin transaction to a publicly derivable address and controlling its first input — no validator status, no threshold collusion, no malformed encodings. The group key is public and the offset derivation is deterministic, so computing the Branch/Change/Forwarded scripts is trivial.

### Recommendation
Distinguish internally generated outputs from externally received ones: tag change/branch/forwarded outpoints at construction time (the processor knows which transactions it signed) rather than inferring `kind` from the script alone, or restrict `kind != External` classification to outpoints produced by known Serai transactions. Treat `presumed_origin` as untrusted and document that it is attacker-controlled, or verify it against an expected origin set.

### Proof of Concept
1. Observe the multisig group key `K` (public on-chain).
2. Compute `change_addr = address_from_key(K + G * offset_change)` where `offset_change` is the scalar found by `register_offset` for `OutputType::Change`.
3. Broadcast a transaction spending an input whose prevout script_pubkey is any attacker-chosen script (e.g., a Serai deposit address), paying `> DUST` sats to `change_addr`, with `tx.input[0]` pointing at the forged prevout.
4. After `CONFIRMATIONS` blocks, `get_outputs` returns an `Output` with `kind == OutputType::Change` and `presumed_origin` equal to the attacker-chosen address — Serai reports the payment as internal change with forged provenance despite it being an arbitrary external send.

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

**File:** processor/src/networks/bitcoin.rs (L686-736)
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
