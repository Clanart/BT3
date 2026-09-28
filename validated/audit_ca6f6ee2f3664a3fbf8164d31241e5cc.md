### Title
Attacker-supplied Bitcoin transaction can claim another plan's eventuality, spoofing its completion - (File: processor/src/networks/bitcoin.rs)

### Summary
Serai tracks out-transaction "eventualities" (expected on-chain plan completions) by a raw 32-byte lookup key and matches them against `extract_serai_data`, which pulls attacker-controlled bytes from *any* transaction in a scanned block — from any `OP_RETURN` output's last push, or from the witness of any input whose redeem script matches the `SHA256 <push> EQUALVERIFY` pattern. Just as the vLLM bug let an attacker "register" a package name in an uncontrolled namespace that the resolver preferred (`unsafe-best-match`), any unprivileged user can broadcast a Bitcoin transaction embedding a pending plan's eventuality ID, causing Serai to resolve that attacker's transaction as the plan's legitimate completion.

### Finding Description
`Eventuality::lookup()` returns a bare 32-byte ID, and `EventualitiesTracker::register` indexes pending eventualities solely by that key [1](#0-0) [2](#0-1) .

`get_eventuality_completions` is invoked for every scanned block and matches tracked lookups against transactions in the block [3](#0-2) . The extraction helper accepts data from:

- the last push of *any* `OP_RETURN` output in the transaction (checked first, output index unconstrained), and
- `witness[len-2]` of *any* input whose final witness element parses as `SHA256 <push> EQUALVERIFY ...` — a pattern satisfiable by a standard `OP_SHA256 <hash> OP_EQUALVERIFY OP_EQUAL`/P2WSH-style script the attacker spends themselves [4](#0-3) .

No check ties the extracted data to outputs/inputs belonging to the Serai multisig, to the eventuality's expected key, or even to the transaction being a valid spend of Serai outputs. Once matched, `ScannerEvent::Completed(key, block_number, id, tx, completion)` is emitted with the attacker's transaction recorded as the completion [5](#0-4) .

The analog to `UV_INDEX_STRATEGY="unsafe-best-match"`: the resolver (block scan) satisfies the registered name (eventuality ID) with whichever transaction presents it — attacker's or Serai's own.

### Impact Explanation
A spurious `ScannerEvent::Completed` marks the pending plan resolved and drops it from `EventualitiesTracker` (via `drop`/completion handling), so the real plan transaction — which may still be pending, failed, or never broadcast — is never retried or re-registered. Depending on downstream handling of the `Completed` event, this can cause:

- Payments/forwards reported as executed that were never sent (funds treated as moved that are not spendable where expected).
- Confusion over which transaction effected the plan, since the attacker's `Transaction` is serialized as the completion via `serialize_completion` [6](#0-5) .
- In the worst case, the attacker embeds the ID in a tx that also pays a dust output to a scanned script, conflating completion spoofing with the receive path.

Additionally, `EventualitiesTracker::register` *panics* on a lookup collision [7](#0-6)  — the same unauthenticated namespace means a 32-byte ID observed publicly could grief registration paths.

### Likelihood Explanation
Medium. Requires: (a) learning a pending eventuality ID — these are plan hashes visible in coordinator/processor messages and mempool transactions carrying the ID via `extract_serai_data`'s own encodings; (b) broadcasting a conforming Bitcoin transaction, which any Bitcoin user can do (an `OP_RETURN` push costs dust; the witness path requires spending a self-owned segwit UTXO whose script matches `segwit_data_pattern`). Race window is the plan's pending lifetime; the attacker needs their tx scanned in a block before the genuine completion. The ID is not a secret, and nothing authenticates the carrier transaction — mirroring how `flashinfer-jit-cache` needed only a name, not credentials, to win resolution.

### Recommendation
- Bind completion matching to transaction provenance: only consider `extract_serai_data` from transactions that spend or create outputs on the registered multisig keys (i.e., require the tx to interact with a scanned `script_pubkey` / consume a tracked `OutPoint`), not from arbitrary block transactions.
- Alternatively, namespace the lookup by (eventuality_id, expected input/output fingerprint) rather than the bare ID.
- Treat lookup collisions in `EventualitiesTracker::register` as a logged rejection instead of `panic!`, since the key space is influenced by unauthenticated on-chain data.

### Proof of Concept
1. Observe a pending plan's eventuality ID `E` (32 bytes) — e.g., from the coordinator's `Signable`/`Plan` messages or from a mempool tx carrying it.
2. Broadcast a Bitcoin tx `T` with output `OP_RETURN <E>` (or spend a P2WSH UTXO whose witness is `[<E>, <script: OP_SHA256 <sha256(E)> OP_EQUALVERIFY OP_TRUE>]`).
3. On the next scanned block, `get_eventuality_completions` yields `(id = E's plan id, tx = T, completion = T)`; scanner emits `ScannerEvent::Completed` naming `T` as the plan's completion and the tracker drops the lookup [3](#0-2) .
4. The genuine plan transaction, if it later confirms, finds no registered eventuality; the plan is considered resolved despite being fulfilled by an unrelated, attacker-crafted transaction.

*Caveat: I confirmed the lookup key derivation, the attacker-controlled extraction surface, and the emission of `Completed` with the matching transaction, but did not fully trace `get_eventuality_completions`' exact match predicate nor the downstream consumers of `ScannerEvent::Completed`; the severity hinges on whether a spoofed completion suppresses retries of the real plan.*

### Citations

**File:** processor/src/networks/bitcoin.rs (L216-222)
```rust
impl EventualityTrait for Eventuality {
  type Claim = EmptyClaim;
  type Completion = Transaction;

  fn lookup(&self) -> Vec<u8> {
    self.0.to_vec()
  }
```

**File:** processor/src/networks/bitcoin.rs (L238-246)
```rust
  fn serialize_completion(completion: &Transaction) -> Vec<u8> {
    let mut buf = vec![];
    completion.consensus_encode(&mut buf).unwrap();
    buf
  }
  fn read_completion<R: io::Read>(reader: &mut R) -> io::Result<Transaction> {
    Transaction::consensus_decode(&mut io::BufReader::with_capacity(0, reader))
      .map_err(|e| io::Error::other(format!("{e}")))
  }
```

**File:** processor/src/networks/bitcoin.rs (L473-524)
```rust
  // Expected script has to start with SHA256 PUSH MSG_HASH OP_EQUALVERIFY ..
  fn segwit_data_pattern(script: &ScriptBuf) -> Option<bool> {
    let mut ins = script.instructions();

    // first item should be SHA256 code
    if ins.next()?.ok()?.opcode()? != OP_SHA256 {
      return Some(false);
    }

    // next should be a data push
    ins.next()?.ok()?.push_bytes()?;

    // next should be a equality check
    if ins.next()?.ok()?.opcode()? != OP_EQUALVERIFY {
      return Some(false);
    }

    Some(true)
  }

  fn extract_serai_data(tx: &Transaction) -> Vec<u8> {
    // check outputs
    let mut data = (|| {
      for output in &tx.output {
        if output.script_pubkey.is_op_return() {
          match output.script_pubkey.instructions_minimal().last() {
            Some(Ok(Instruction::PushBytes(data))) => return data.as_bytes().to_vec(),
            _ => continue,
          }
        }
      }
      vec![]
    })();

    // check inputs
    if data.is_empty() {
      for input in &tx.input {
        let witness = input.witness.to_vec();
        // expected witness at least has to have 2 items, msg and the redeem script.
        if witness.len() >= 2 {
          let redeem_script = ScriptBuf::from_bytes(witness.last().unwrap().clone());
          if Self::segwit_data_pattern(&redeem_script) == Some(true) {
            data.clone_from(&witness[witness.len() - 2]); // len() - 1 is the redeem_script
            break;
          }
        }
      }
    }

    data.truncate(MAX_DATA_LEN.try_into().unwrap());
    data
  }
```

**File:** processor/src/networks/mod.rs (L168-178)
```rust
  pub fn register(&mut self, block_number: usize, id: [u8; 32], eventuality: E) {
    log::info!("registering eventuality for {}", hex::encode(id));

    let lookup = eventuality.lookup();
    if self.map.contains_key(&lookup) {
      panic!("registering an eventuality multiple times or lookup collision");
    }
    self.map.insert(lookup, (id, eventuality));
    // If our self tracker already went past this block number, set it back
    self.block_number = self.block_number.min(block_number);
  }
```

**File:** processor/src/multisigs/scanner.rs (L569-590)
```rust
          for (id, (block_number, tx, completion)) in network
            .get_eventuality_completions(scanner.eventualities.get_mut(&key_vec).unwrap(), &block)
            .await
          {
            info!(
              "eventuality {} resolved by {}, as found on chain",
              hex::encode(id),
              hex::encode(tx.as_ref())
            );

            completion_block_numbers.push(block_number);
            // This must be before the mission of ScannerEvent::Block, per commentary in mod.rs
            if !scanner.emit(ScannerEvent::Completed(
              key_vec.clone(),
              block_number,
              id,
              tx,
              completion,
            )) {
              return;
            }
          }
```
