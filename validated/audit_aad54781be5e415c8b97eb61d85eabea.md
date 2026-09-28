### Title
Attacker-controlled output classification via free choice of registered offset address (Scanner trusts which offset an arbitrary payer selects) - (File: networks/bitcoin/src/wallet/mod.rs)

### Summary
Like Lemmy trusting the first attacker-supplied `X-Forwarded-For` value to select a rate-limit bucket, `Scanner::scan_transaction` trusts an unauthenticated payer's choice of destination address to select the *security classification* (`OutputType`) of a received output. The scanner registers several semantic identities — `External`, `Branch`, `Change`, `Forwarded` — behind distinct `offset`s of the same group key. An arbitrary payer can compute every registered address and deliberately pay to the one whose `offset` yields the classification they want, bypassing the intent of the classification scheme.

### Finding Description
`Scanner::register_offset` inserts `p2tr_script_buf(key + offset*G) → offset` into a public lookup table, and `scan_transaction` classifies a matched output purely by `self.scripts.get(&output.script_pubkey)` [1](#0-0) . In `get_outputs`, the returned `offset` is mapped to a semantic `OutputType` via `kinds[offset_repr_ref]`, where the kinds were registered as `Branch` = `hash_to_F(KEY_DST, b"branch")`, `Change` = `hash_to_F(KEY_DST, b"change")`, `Forwarded` = `hash_to_F(KEY_DST, b"forward")`, and `External` = `Scalar::ZERO` [2](#0-1) . All of these derived addresses are computable by anyone (the group key and the hash-to-scalar DST are public), so the payer — not the multisig — decides the `kind` field of any output they create [3](#0-2) .

Downstream, `kind` drives handling: `External` outputs receive `data`/origin processing and become deposit/refund `Plan`s, while `Forwarded`/`Change`/`Branch` outputs are treated as internally generated (dropped from instruction reporting, used as rotation/forward artifacts) [4](#0-3) . `presumed_origin` is likewise attacker-shaped, taken from `tx.input[0]`'s spent output [5](#0-4) .

### Impact Explanation
An unprivileged external user can have funds *misreported* to the processor:

- Paying the `Change`/`Branch` offset address makes an external deposit classify as internal change/rotation output, which is not converted into a deposit `Plan` and may instead be consumed as an input to a future `SignableTransaction` without crediting any depositor — funds received that are never credited.
- Paying the `Forwarded` offset makes an ordinary external payment look like forwarded funds, changing how `multisigs/mod.rs` schedules/drops it during rotation steps (`ForwardFromExisting`, `ClosingExisting`) [6](#0-5) .
- Combined with the unauthenticated `presumed_origin`, a crafted refund-path output can attribute a refund to an arbitrary address script.

### Likelihood Explanation
Any Bitcoin user paying the multisig chooses the output address. The offset addresses are deterministic functions of the public group key (`hash_to_F` with fixed DSTs) and are even published implicitly via `forward_address` [7](#0-6) . No validator collusion, key access, or malformed encoding is needed — just a standard P2TR payment to a derivable address. Exploitation requires the payer to opt in, so it mainly enables griefing/miscrediting of one's own or induced third-party deposits rather than direct theft; hence Medium.

### Recommendation
Treat `kind` as a claim, not a fact: distinguish offsets by an authenticated signal (e.g., only accept `Change`/`Branch`/`Forwarded` classifications for outputs in transactions the multisig itself authored or is expecting via tracked outpoints), or at minimum process non-`External` classifications conservatively — still emit a deposit instruction (with `data` absent) rather than silently dropping/redirecting the output.

### Proof of Concept
1. Obtain the multisig's public group key `K` (public from the chain or `external_address`).
2. Compute `change_addr = p2tr(K + hash_to_F("Serai Bitcoin Output Offset", "change")*G)` — the same computation `scanner()` performs at `processor/src/networks/bitcoin.rs:333-344`.
3. Broadcast a normal Bitcoin transaction paying `change_addr`.
4. `get_outputs` scans it, `scan_transaction` matches `script_pubkey`, and `kinds[offset]` returns `OutputType::Change` rather than `External` — the deposit is classified as internally generated change, skipping the `data`/origin/instruction path at `processor/src/networks/bitcoin.rs:730-736` and the deposit-`Plan` path in `multisigs/mod.rs`, even though it is an external payment.

### Citations

**File:** networks/bitcoin/src/wallet/mod.rs (L180-213)
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

  /// Scan a transaction.
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
```

**File:** processor/src/networks/bitcoin.rs (L313-347)
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
}
```

**File:** processor/src/networks/bitcoin.rs (L671-674)
```rust
  fn forward_address(key: ProjectivePoint) -> Option<Address> {
    let (_, offsets, _) = scanner(key);
    Some(address_from_key(key + (ProjectivePoint::GENERATOR * offsets[&OutputType::Forwarded])))
  }
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

**File:** processor/src/networks/bitcoin.rs (L714-729)
```rust
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

**File:** processor/src/multisigs/mod.rs (L922-957)
```rust
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
          };

          // Delay External outputs received to new multisig earlier than expected
          if Some(output.key()) == self.new.as_ref().map(|new| new.key) {
            match step {
              RotationStep::UseExisting => {
                DelayedOutputDb::save_delayed_output(txn, &instruction);
                continue;
              }
              RotationStep::NewAsChange |
              RotationStep::ForwardFromExisting |
              RotationStep::ClosingExisting => {}
            }
          }

          instructions.push(instruction);
```
