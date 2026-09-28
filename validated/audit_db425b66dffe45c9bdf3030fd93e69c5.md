### Title
Forwarded-output instructions are matched by amount only, letting an attacker attach their own `origin` (refund destination) to a victim's forwarded funds - (File: processor/src/multisigs/db.rs)

### Summary

The bug class is "value is routed to a caller-/attacker-influenced recipient rather than to its rightful, derived destination." In Serai, when the old multisig forwards an `External` output to the new multisig during rotation step `ForwardFromExisting`, the `InInstruction` recovered when the `Forwarded` output is later scanned is looked up in `ForwardedOutputDb` keyed **solely by `ExternalBalance`** (`coin` + `amount`). Any user can submit a Bitcoin transaction paying the multisig's external address with an arbitrary amount and an arbitrary `Shorthand` carrying an attacker-controlled `origin`. An attacker who matches a victim's forwarded balance causes the victim's forwarded output to be associated with the attacker's instruction — including its `origin`, which is the address refunds are sent to if the instruction fails — redirecting the victim's refunded coins to the attacker.

### Finding Description

During `RotationStep::ForwardFromExisting`, each `External` output belonging to the retiring multisig produces a `PlanFromScanning::Forward`, and its parsed `InInstructionWithBalance` (with the balance reduced by the forwarding fee) is persisted via `ForwardedOutputDb::save_forwarded_output`, which appends the SCALE-encoded instruction under key `instruction.balance` [1](#0-0) . When the forwarded output later lands at the new multisig and is scanned as `OutputType::Forwarded`, `take_forwarded_output(txn, output.balance())` pops the first stored instruction for that balance and uses it as the instruction for the output [2](#0-1) .

Two flaws compound:

1. **Balance-only correlation.** `ForwardedOutputDb: (balance: ExternalBalance) -> Vec<u8>` [3](#0-2)  — there is no binding to the outpoint, txid, or offset of the output being forwarded. Any two outputs with equal `coin` and post-fee `amount` collide in the same queue, and `take_forwarded_output` returns whichever instruction was queued first, regardless of which output it belonged to [4](#0-3) .

2. **Consumed entries are re-stored.** After decoding one instruction, if bytes remain, the code writes back `&outputs` — the original full buffer, not the `outputs_ref` remainder — so the popped instruction is served again for the next forwarded output of the same balance [5](#0-4) .

The diverted instruction matters because `origin` is attacker-controllable: `instruction_from_output` uses `instruction.origin.or(presumed_origin)` [6](#0-5) , and per the spec an instruction-provided `origin` overrides the automatically provided one [7](#0-6) . If the attached instruction later fails execution, the coins are scheduled for return to `origin` — the attacker's address — rather than the victim's.

### Impact Explanation

An unprivileged external user can redirect refund proceeds belonging to another depositor. Refunds of forwarded outputs are issued to the attacker-chosen `origin` embedded in their same-balance instruction, or the attacker's instruction is replayed for the victim's output due to the re-store bug. This is direct misrouting of user funds reachable purely through public transaction data (chosen output amount + crafted `Shorthand` bytes), analogous to the original report's "victim's funds end up with a party who merely triggered/supplied data to the flow" rather than a dedicated, verifiably correct destination.

### Likelihood Explanation

Exploitation requires a multisig rotation window (`ForwardFromExisting`, a 6-hour window per spec) and an amount collision. Both are achievable by an unprivileged party: the attacker observes pending external deposits or simply spams same-value outputs during rotation to populate the balance-keyed queue ahead of or alongside the victim's. The amount is directly chosen by the depositor, and the fee deducted is deterministic, so matching a victim's post-fee `balance` is feasible whenever the victim's amount is known or guessable.

### Recommendation

Key `ForwardedOutputDb` by the forwarded output's outpoint (or output ID) rather than `balance`, so a forwarded output can only retrieve the instruction saved for that exact output. Additionally, in `take_forwarded_output`, write back `outputs_ref` (the remaining undecoded bytes) instead of `&outputs` when the queue is non-empty after a pop.

### Proof of Concept

1. Rotation reaches `RotationStep::ForwardFromExisting` for multisig key `K_old` → `K_new`.
2. Victim's `External` output `V` (amount `a`, valid instruction, origin `Ov`) is scanned; its instruction is saved under balance `a - fee` and a `Forward` plan is created [8](#0-7) .
3. Attacker sends their own `External` output `A` to `K_old` whose amount makes `A.balance - forwarding_fee == a - fee`, with a `Shorthand` encoding `origin = attacker_addr` and a deliberately failing instruction. It is queued under the same `ExternalBalance` key [9](#0-8) .
4. Both outputs are forwarded to `K_new`. When the victim's forwarded output is scanned, `take_forwarded_output` pops whichever instruction sits first in the shared queue — potentially the attacker's — and the popped instruction may be served twice because the full buffer is re-stored [4](#0-3) .
5. The victim's coins are now associated with the attacker's `origin`; when the instruction fails, `plans.push(PlanFromScanning::Refund(output, attacker_addr))` refunds the victim's funds to the attacker [10](#0-9) .

### Citations

**File:** processor/src/multisigs/mod.rs (L89-92)
```rust
  (
    instruction.origin.or(presumed_origin),
    Some(InInstructionWithBalance { instruction: instruction.instruction, balance }),
  )
```

**File:** processor/src/multisigs/mod.rs (L824-833)
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
```

**File:** processor/src/multisigs/mod.rs (L859-901)
```rust
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
```

**File:** processor/src/multisigs/mod.rs (L903-908)
```rust
              } else if let Some(refund_to) = refund_to {
                if let Ok(refund_to) = refund_to.consume().try_into() {
                  // Build a dedicated Plan refunding this
                  plans.push(PlanFromScanning::Refund(output.clone(), refund_to));
                }
              }
```

**File:** processor/src/multisigs/db.rs (L76-76)
```rust
    ForwardedOutputDb: (balance: ExternalBalance) -> Vec<u8>,
```

**File:** processor/src/multisigs/db.rs (L223-227)
```rust
  pub fn save_forwarded_output(txn: &mut impl DbTxn, instruction: &InInstructionWithBalance) {
    let mut existing = Self::get(txn, instruction.balance).unwrap_or_default();
    existing.extend(instruction.encode());
    Self::set(txn, instruction.balance, &existing);
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

**File:** spec/integrations/Instructions.md (L44-49)
```markdown

Networks may automatically provide `origin`. If they do, the instruction may
still provide `origin`, overriding the automatically provided value.

If the instruction fails, coins are scheduled to be returned to `origin`,
if provided.
```
