### Title
Forwarded-output instructions keyed only by balance and restored in full cause replay of the first stored instruction and permanent loss of later instructions - ([File: processor/src/multisigs/db.rs](processor/src/multisigs/db.rs))

### Summary
Analogous to the reported liquidation issue — where any unprivileged caller could trigger a code path that reallocates a victim's assets — Serai's processor lets any external Bitcoin sender feed `ForwardedOutputDb::take_forwarded_output`, which is keyed solely by `ExternalBalance` (coin + amount) and, after decoding one `InInstructionWithBalance`, writes back the *entire* original buffer instead of the remainder. The first instruction stored under a balance is emitted repeatedly for every matching forwarded output, while subsequent instructions stored under the same balance are never reached — so a user's deposited funds are received by the multisig but their `InInstruction` is never executed.

### Finding Description
When the old multisig forwards an output during rotation, the processor saves the associated `InInstruction` with `ForwardedOutputDb::save_forwarded_output`, keyed by `instruction.balance` — an `ExternalBalance`, i.e. just `(coin, amount)` (`db.rs:76`, `db.rs:222-227`). When a block is scanned, every output classified `OutputType::Forwarded` calls `take_forwarded_output(txn, output.balance())` to retrieve the instruction to emit (`mod.rs:824-833`).

Two defects compose:

1. **Key is only the balance.** Any distinct forwarded outputs with equal `(coin, amount)` collide. An external party can send any transaction paying the publicly derivable forward address (`processor/src/networks/bitcoin.rs:671-674`, `scanner()` registers `FORWARD_OFFSET = hash_to_F(KEY_DST, b"forward")` at `bitcoin.rs:341-344`), producing a `Forwarded`-kind output (`bitcoin.rs:692-699`) with an attacker-chosen amount, and thereby pop/take whichever instruction happens to be first under that balance — instruction/output pairing is never verified.

2. **Full buffer restored instead of remainder.** In `take_forwarded_output` (`db.rs:229-243`), after `InInstructionWithBalance::decode` consumes the first instruction, the non-empty-remainder branch calls `Self::set(txn, balance, &outputs)` where `outputs` is the original undecoded byte vector, not `outputs_ref`. So the same first instruction is returned again on the next take, and any instruction appended after it under the same balance is never decoded. The intended remainder-preserving behavior (compare `DelayedOutputDb::take_delayed_outputs`, `db.rs:253-263`, which drains the buffer correctly) is not implemented.

### Impact Explanation
- **Dropped instructions:** if two forwarding operations produce equal post-fee balances (plausible since forwarded amounts are computed as `instruction.balance - tx.fee` with a fixed fee rate, `mod.rs:883-885`), only the first instruction is ever emitted. The later depositor's coins are received by the multisig but their `InInstruction` (e.g., a swap/bridge-out) is never executed — funds received but the intended action never occurs.
- **Replayed instruction / misattribution:** the same instruction can be emitted once per matching forwarded output, and an attacker's crafted forwarded output of the right amount can consume an instruction belonging to a different user's output, desynchronizing which deposit performed which action.

### Likelihood Explanation
Reachable by an unprivileged party with public inputs: anyone can compute the forward address for the group key and send a Bitcoin transaction of a chosen amount to it; equal-amount collisions can also arise naturally between ordinary deposits since forwarded balances are deterministic. No validator privilege, key material, or protocol-internal access is required.

### Recommendation
- In `take_forwarded_output`, write back the decoded remainder (`outputs_ref`) rather than `outputs`, so instructions are consumed FIFO.
- Key `ForwardedOutputDb` by a unique identifier (e.g., plan/eventuality ID or outpoint) rather than `ExternalBalance`, or store `(balance, nonce)` pairs, so a forwarded output can only be matched to the instruction generated for it.
- When a `Forwarded` output finds no matching instruction, treat it as `External`/unexpected and log, rather than silently consuming the queue.

### Proof of Concept
1. During `RotationStep::ForwardFromExisting`, user A's external output carries `InInstruction` I₁ with `ExternalBalance { Bitcoin, amount = X }`; after `prepare_send`, `save_forwarded_output` stores `encode(I₁)` under key `X` (`db.rs:222-227`, `mod.rs:883-885`).
2. User B's output forwards with the same post-fee balance `X`, appending `encode(I₂)`; the DB value under `X` is now `encode(I₁) || encode(I₂)`.
3. When the forwarded outputs are scanned, `take_forwarded_output(txn, X)` decodes and returns `I₁`, then — because the buffer is non-empty — stores the full original `encode(I₁) || encode(I₂)` back (`db.rs:236-240`).
4. The second forwarded output again returns `I₁` (re-emitting/duplicating A's instruction); `I₂` is never decoded from the head of the buffer, so B's instruction is never emitted even though B's coins arrived.
5. Independently, an attacker who observes balance `X` in flight can send a transaction paying `X` to `forward_address(key)`; the scanner classifies it `OutputType::Forwarded` (`bitcoin.rs:692-695`) and the take pops `I₁`, executing A's instruction against an output that never carried it. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

### Citations

**File:** processor/src/multisigs/db.rs (L76-78)
```rust
    ForwardedOutputDb: (balance: ExternalBalance) -> Vec<u8>,
    DelayedOutputDb: () -> Vec<u8>
  }
```

**File:** processor/src/multisigs/db.rs (L229-243)
```rust
  pub fn take_forwarded_output(
    txn: &mut impl DbTxn,
    balance: ExternalBalance,
  ) -> Option<InInstructionWithBalance> {
    let outputs = Self::get(txn, balance)?;
    let mut outputs_ref = outputs.as_slice();
    let res = InInstructionWithBalance::decode(&mut outputs_ref).unwrap();
    assert!(outputs_ref.len() < outputs.len());
    if outputs_ref.is_empty() {
      txn.del(Self::key(balance));
    } else {
      Self::set(txn, balance, &outputs);
    }
    Some(res)
  }
```

**File:** processor/src/multisigs/mod.rs (L824-837)
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
```

**File:** processor/src/networks/bitcoin.rs (L341-344)
```rust
  register(
    OutputType::Forwarded,
    *FORWARD_OFFSET.get_or_init(|| Secp256k1::hash_to_F(KEY_DST, b"forward")),
  );
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
