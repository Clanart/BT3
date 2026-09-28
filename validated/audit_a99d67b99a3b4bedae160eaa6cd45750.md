### Title
Refunds/attribution derived from `tx.input[0]` only allow PayJoin/CoinJoin senders to redirect other participants' deposits - (File: processor/src/networks/bitcoin.rs)

### Summary
`Bitcoin::get_outputs` attributes every Serai-bound output in a transaction to a single origin: the script of the output spent by `tx.input[0]`. This "first input is the sender" assumption is a single-point oracle — analogous to Definer using one pool's balance as the price source — and any output in the transaction carrying an absent/invalid `Shorthand` is refunded to that address, letting an attacker supplying input 0 in a multi-party transaction (PayJoin/CoinJoin) steal another participant's deposit via the refund path.

### Finding Description
In `get_outputs`, after `scan_transaction` collects all outputs paying the multisig scripts, the code computes one `presumed_origin` for the whole transaction: [1](#0-0) 

It reads only `tx.input[0]`, fetches the spent output, and uses `Address::new(spent_output.script_pubkey)` as the origin — then clones it onto **every** scanned output of that tx (`output.presumed_origin.clone_from(&presumed_origin)` at lines 731–736).

Downstream, `instruction_from_output` uses `presumed_origin` as the refund destination: `instruction.origin.or(presumed_origin)` becomes `refund_to`, and when the `Shorthand`/data is missing or unparseable the multisig creates `PlanFromScanning::Refund(output, refund_to)`. [2](#0-1) [3](#0-2) 

On Bitcoin, multi-input transactions routinely have inputs controlled by different parties (PayJoin, CoinJoin, batched exchange withdrawals). The code assumes input 0's spender is the funder of all matched outputs — the exact bug class of trusting one on-chain observation point as authoritative.

### Impact Explanation
An unprivileged attacker who controls `tx.input[0]` of a transaction also containing a third party's payment to the Serai multisig can cause that deposit to be refunded to the attacker's address whenever the deposit lacks a valid explicit `origin` — i.e., funds are paid out by threshold signature to an address that did not send them. The attacker gains the victim's BTC; the victim's deposit is drained from multisig control under a legitimately signed refund Plan. This is fund theft reachable purely by sending/arranging a Bitcoin transaction.

### Likelihood Explanation
- The attacker must arrange ordering such that their input is `input[0]` while the victim's Serai-bound output is in the same tx. This is naturally satisfied in PayJoin flows (the receiver/payjoin participant's input position is attacker-influenced) and coordinated CoinJoins; the attacker cannot inject arbitrary victims' UTXOs without cooperation, but any cooperative batching where a counterparty pays a Serai deposit address suffices.
- The deposit must carry no explicit `origin` and no successfully-decoding `Shorthand` (or a `Shorthand` that fails `RefundableInInstruction::try_from`), triggering the refund path — trivially satisfiable since a bare payment with malformed/absent OP_RETURN data falls into `refund_to = presumed_origin`.
- No validator collusion, malicious RPC, or key compromise is required.

### Recommendation
Attribute the origin per-output rather than per-transaction, or stop treating `tx.input[0]` as authoritative:
- Only assign `presumed_origin` when the transaction has exactly one input, or when all inputs share the same prevout script_pubkey.
- Prefer requiring the depositor to supply an explicit `origin` in the `RefundableInInstruction` and treat `presumed_origin` as a heuristic that must be disabled whenever a transaction has multiple distinct input sources; otherwise mark the output non-refundable rather than refunding to a possibly-unrelated party.
- If first-input heuristics are kept, document the theft vector and skip refunds for multi-party transactions.

### Proof of Concept
1. Victim cooperates in a PayJoin: they contribute an input funding an output paying the Serai multisig's external address (offset 0 script from `p2tr_script_buf(key)`), with no OP_RETURN data.
2. Attacker contributes an input and orders it as `tx.input[0]` (standard PayJoin receivers routinely control input ordering), spending a UTXO whose script_pubkey is the attacker's own address.
3. `get_outputs` scans the victim's output to the multisig, sets `presumed_origin` = attacker's address (`input[0]`'s spent script), and attaches no data.
4. `instruction_from_output` finds no valid `Shorthand`, returns `refund_to = attacker's address`; `scanner_event_to_multisig_event` pushes `PlanFromScanning::Refund`.
5. The threshold multisig signs a transaction returning the victim's deposit to the attacker — `schnorr`/FROST signing proceeds normally since the Plan is internally consistent.

Relevant code:
- `Bitcoin::get_outputs` origin derivation: `processor/src/networks/bitcoin.rs` lines 686–737 (`tx.input[0]` at line 715, `Address::new(spent_output.script_pubkey)` at line 728).
- Refund path: `processor/src/multisigs/mod.rs` `instruction_from_output` (lines 40–93) and the `PlanFromScanning::Refund` push (lines 934–941).
- Output classification that routes attacker-sent scripts into `OutputType` kinds: `processor/src/networks/bitcoin.rs` `scanner()` lines 313–347.

### Citations

**File:** processor/src/networks/bitcoin.rs (L714-728)
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
```

**File:** processor/src/multisigs/mod.rs (L84-92)
```rust
  let mut balance = output.balance();
  // Deduct twice the cost to aggregate to prevent economic attacks by malicious miners against
  // other users
  balance.amount.0 -= 2 * N::COST_TO_AGGREGATE;

  (
    instruction.origin.or(presumed_origin),
    Some(InInstructionWithBalance { instruction: instruction.instruction, balance }),
  )
```

**File:** processor/src/multisigs/mod.rs (L934-941)
```rust
          let (refund_to, instruction) = instruction_from_output::<N>(&output);
          let Some(instruction) = instruction else {
            if let Some(refund_to) = refund_to {
              if let Ok(refund_to) = refund_to.consume().try_into() {
                plans.push(PlanFromScanning::Refund(output.clone(), refund_to));
              }
            }
            continue;
```
