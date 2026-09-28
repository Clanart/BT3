### Title
Deterministically-derivable internal output scripts let anyone bypass the External classification guard, causing deposits to be silently uncredited - (File: networks/bitcoin/src/wallet/mod.rs, processor/src/networks/bitcoin.rs, processor/src/multisigs/mod.rs)

### Summary
Analogous to the NLTK symlink bug — where a name passes the "inside corpus root" guard but resolves outside it — the Bitcoin `Scanner` treats any `script_pubkey` present in its `scripts` map as validly "ours", yet the map contains not only the external deposit address but also internally-reserved Branch, Change, and Forwarded scripts. Because the internal offsets are `hash_to_F("Serai Bitcoin Output Offset", "branch"|"change"|"forward")` over the public group key, any unprivileged party can derive those scripts and pay to them. The output passes the scanner's membership guard, then resolves to a non-`External` `OutputType`, causing the deposit to be dropped from instruction processing entirely — funds are received by the multisig but never credited.

### Finding Description
`Scanner::register_offset` builds a `HashMap<ScriptBuf, Scalar>` keyed purely by script. `scan_transaction` matches any output whose `script_pubkey` is in the map and returns the associated offset, with no distinction of intent. [1](#0-0) 

In `processor/src/networks/bitcoin.rs`, `scanner()` deterministically registers Branch, Change, and Forwarded offsets derived via `Secp256k1::hash_to_F(KEY_DST, ...)` — a public computation over the (public) multisig group key: [2](#0-1) 

`get_outputs` then labels each scanned output via `kinds[offset_repr]`, so an attacker payment to the Change script yields `OutputType::Change`, and to the Forward script yields `OutputType::Forwarded`: [3](#0-2) 

In `scanner_event_to_multisig_event`, Forwarded outputs only survive if `ForwardedOutputDb::take_forwarded_output` returns a stored instruction (which only exists for self-initiated forwards), and the subsequent `outputs.retain(|o| o.kind() == OutputType::External)` drops every non-External output. No `InInstruction` is produced, no refund plan is created (`instruction_from_output` finds no refund origin for an address-derived deposit without embedded data), and the output is never reported as received: [4](#0-3) 

### Impact Explanation
An attacker — or a confused user whose wallet derives the same deterministic scripts — sends BTC to the Branch/Change/Forwarded script of an active multisig. The output is genuinely controlled by the multisig's key (so it is "received" in a UTXO sense), but it is classified as internal traffic and silently excluded from deposit crediting. The depositor's funds sit in an output the processor will only ever consume as internal change/forward liquidity, with no user attribution — effectively funds received that are not creditable/spendable for their intended purpose. Worse, sending to the Forwarded script during a `ForwardFromExisting` rotation can inject an output that enters the forwarding plan machinery without a corresponding instruction, perturbing rotation accounting.

### Likelihood Explanation
The offsets require no secret material: `hash_to_F(b"Serai Bitcoin Output Offset", "branch"/"change"/"forward")` over the group key is computable by anyone observing the multisig address, and `register_offset`'s increment-to-even behavior is deterministic and replicable. Reachability is a single on-chain transaction. The main limitation is that the attacker spends their own funds; the realistic vector is griefing (dust-level payments misclassified, triggering unneeded plans/fees) or causing genuine deposits to vanish when a third party pays to a derived address. Severity: Medium.

### Recommendation
Distinguish scanner registrations by intent: keep the `scripts` map but have `scan_transaction`/`get_outputs` only surface `External` outputs for deposit crediting, or reject/flag payments to internal scripts at classification time rather than silently dropping them post-`retain`. At minimum, emit an explicit event/refund path for outputs matching internal scripts that were not self-generated (e.g., Forwarded outputs with no matching `ForwardedOutputDb` entry should produce a refund plan instead of being dropped).

### Proof of Concept
1. Observe the active multisig group key `K` on chain (its P2TR external address is public).
2. Compute `off = Secp256k1::hash_to_F(b"Serai Bitcoin Output Offset", b"change")`; increment until `K + G*off` is even (mirroring `register_offset`), then derive `script = ScriptBuf::new_p2tr_tweaked(TweakedPublicKey::dangerous_assume_tweaked(x_only(K + G*off)))`.
3. Broadcast a transaction paying ≥ `N::DUST` to `script`.
4. `get_outputs` returns the output with `kind = OutputType::Change`; `scanner_event_to_multisig_event` drops it at `outputs.retain(kind == External)`; no `InInstruction` is emitted and the depositor is never credited, despite the multisig controlling the funds.

Note: the full downstream fate (whether the output later gets swept as generic change) could not be fully traced within `processor/src/multisigs`, but the classification bypass and instruction-drop behavior are directly evidenced above.

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

**File:** processor/src/networks/bitcoin.rs (L313-346)
```rust
// Always construct the full scanner in order to ensure there's no collisions
fn scanner(
  key: ProjectivePoint,
) -> (Scanner, HashMap<OutputType, Scalar>, HashMap<Vec<u8>, OutputType>) {
  let mut scanner = Scanner::new(key).unwrap();
  let mut offsets = HashMap::from([(OutputType::External, Scalar::ZERO)]);

  let zero = Scalar::ZERO.to_repr();
  let zero_ref: &[u8] = zero.as_ref();
  let mut kinds = HashMap::from([(zero_ref.to_vec(), OutputType::External)]);

  let mut register = |kind, offset| {
    let offset = scanner.register_offset(offset).expect("offset collision");
    offsets.insert(kind, offset);

    let offset = offset.to_repr();
    let offset_ref: &[u8] = offset.as_ref();
    kinds.insert(offset_ref.to_vec(), kind);
  };

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

  (scanner, offsets, kinds)
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

**File:** processor/src/multisigs/mod.rs (L824-941)
```rust
        for output in &outputs {
          if output.kind() != OutputType::Forwarded {
            continue;
          }

          if let Some(instruction) = ForwardedOutputDb::take_forwarded_output(txn, output.balance())
          {
            instructions.push(instruction);
          }
        }

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
                let Some(shimmed_plan) = N::Scheduler::shim_forward_plan(
                  output.clone(),
                  self.new.as_ref().expect("forwarding from existing yet no new multisig").key,
                ) else {
                  // If this network doesn't need forwarding, report the output now
                  return true;
                };
                plans.push(PlanFromScanning::<N>::Forward(output.clone()));

                // Set the instruction for this output to be returned
                // We need to set it under the amount it's forwarded with, so prepare its forwarding
                // TX to determine the fees involved
                let PreparedSend { tx, post_fee_branches: _, operating_costs } =
                  prepare_send(network, block_number, shimmed_plan, 0).await;
                // operating_costs should not increase in a forwarding TX
                assert_eq!(operating_costs, 0);

                // If this actually forwarded any coins, save the output as forwarded
                // If this didn't create a TX, we don't bother saving the output as forwarded
                // The fact we already created and pushed a plan still using this output will cause
                // it to not be retained here, and later the plan will be dropped as this did here,
                // letting it die out
                if let Some(tx) = &tx {
                  instruction.balance.amount.0 -= tx.0.fee();

                  /*
                    Sending a Plan, with arbitrary data proxying the InInstruction, would require
                    adding a flow for networks which drop their data to still embed arbitrary data.
                    It'd also have edge cases causing failures (we'd need to manually provide the
                    origin if it was implied, which may exceed the encoding limit).

                    Instead, we save the InInstruction as we scan this output. Then, when the
                    output is successfully forwarded, we simply read it from the local database.
                    This also saves the costs of embedding arbitrary data.

                    Since we can't rely on the Eventuality system to detect if it's a forwarded
                    transaction, due to the asynchonicity of the Eventuality system, we instead
                    interpret an Forwarded output which has an amount associated with an
                    InInstruction which was forwarded as having been forwarded.
                  */
                  ForwardedOutputDb::save_forwarded_output(txn, &instruction);
                }
              } else if let Some(refund_to) = refund_to {
                if let Ok(refund_to) = refund_to.consume().try_into() {
                  // Build a dedicated Plan refunding this
                  plans.push(PlanFromScanning::Refund(output.clone(), refund_to));
                }
              }

              // Only keep if we didn't make a Plan consuming it
              plans_at_start == plans.len()
            })()
            .await;
            if should_retain {
              i += 1;
              continue;
            }
            outputs.remove(i);
          }
        }

        for output in outputs {
          // If this is an External transaction to the existing multisig, and we're either solely
          // forwarding or closing the existing multisig, drop it
          // In the case of the forwarding case, we'll report it once it hits the new multisig
          if (match step {
            RotationStep::UseExisting | RotationStep::NewAsChange => false,
            RotationStep::ForwardFromExisting | RotationStep::ClosingExisting => true,
          }) && (output.key() == self.existing.as_ref().unwrap().key)
          {
            continue;
          }

          let (refund_to, instruction) = instruction_from_output::<N>(&output);
          let Some(instruction) = instruction else {
            if let Some(refund_to) = refund_to {
              if let Ok(refund_to) = refund_to.consume().try_into() {
                plans.push(PlanFromScanning::Refund(output.clone(), refund_to));
              }
            }
            continue;
```
