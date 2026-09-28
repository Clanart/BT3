### Title
`Scanner::scan_transaction` reports dust-value outputs as spendable `ReceivedOutput`s, causing funds to be reported received that cannot be economically spent - ([File: networks/bitcoin/src/wallet/mod.rs])

### Summary

The external report's bug class is *reporting/view functions that do not reflect the protocol's real constraints*: `maxDeposit`/`preview*` claim availability or pricing that the state-changing functions do not honor, misleading consumers of the API.

The reachable analog in Serai's in-scope code is `Scanner::scan_transaction` / `Scanner::scan_block` in `networks/bitcoin/src/wallet/mod.rs`. The scanner matches **only** `script_pubkey` (line 205) and returns every matching output as a `ReceivedOutput` — an explicitly "spendable output" type (line 88) — with no check on `output.value`. Downstream, `SignableTransaction::new` in `send.rs` enforces the `DUST = 546` minimum only on *payments* (lines 165–169), never on *inputs*, and each input adds ~57 vbytes of weight to the transaction fee (weight model in `calculate_weight_vbytes`, lines 62–127). A matching output with value below the cost of spending it is still reported as received and will still be pulled in as an input, destroying value.

### Finding Description

An unprivileged party who learns a registered deposit script (all deposit addresses are public on-chain) can craft a transaction paying a tiny amount — e.g., 1 satoshi — to `p2tr_script_buf(key + offset*G)`. Such outputs are consensus-valid when mined. `scan_transaction` (mod.rs lines 199–214) pushes any output whose `script_pubkey` is in `self.scripts` into `res` as a fully-formed `ReceivedOutput`, regardless of `output.value`:

```rust
if let Some(offset) = self.scripts.get(&output.script_pubkey) {
  res.push(ReceivedOutput { offset: *offset, output: output.clone(), ... });
}
```

`ReceivedOutput` is documented as "A spendable output," yet nothing verifies that the output's value exceeds either:
- `DUST` (send.rs line 32), the relay-policy minimum the codebase itself adopts for outputs it creates, or
- the marginal fee cost of adding it as an input (~230 WU ≈ 57 vbytes × `fee_per_vbyte`, per the comment at send.rs lines 606–638 of the processor and the witness model at send.rs lines 71–84).

When the `ReceivedOutput` is later fed to `SignableTransaction::new`, its value is summed into `input_sat` (line 175) and it contributes fee weight, but `input_sat < payment_sat + needed_fee` (line 215) is the only guard — a 1-sat input still consumes ~57+ vbytes of fee while contributing 1 sat. If credited as a deposit at face value, the protocol mints/acknowledges funds it cannot recover; if swept anyway, the sweep loses money on every such input.

This mirrors the audit issue exactly: an introspection function (`scan_transaction` answering "what did we receive that we can spend?") reports a value unconstrained by the limits the spending path actually imposes.

### Impact Explanation

- **Funds reported received that are not spendable**: a dust output to a registered script is surfaced as a `ReceivedOutput` — the wallet's own notion of spendable — though spending it nets negative value. Any accounting/crediting layer consuming the scanner's output credits unrecoverable value.
- **Forced fee burn**: if the scheduler aggregates the dust input into a transaction, each such input consumes fee weight worth more than the input's value; an attacker can repeat this cheaply (cost ≈ dust value per output, recoverable to themselves only as the fee advantage is Serai's loss).
- Attacker cost is minimal and bounded by dust-value outputs they can include in mined/relayed transactions.

### Likelihood Explanation

Requires only the ability to broadcast (or have mined) a Bitcoin transaction paying a tiny output to a publicly known Serai deposit script — no validator status, no collusion, no key material. Deposit scripts are inherently public once used. Whether a malicious miner is needed depends on relay policy for sub-dust outputs; outputs ≥ 546 sats but < their spend cost at prevailing fee rates relay normally, making the vector reachable via ordinary relayed transactions, not just miner cooperation. Medium likelihood of occurrence; per-event loss is small but unbounded in repetition.

### Recommendation

Enforce a minimum-value check in `Scanner::scan_transaction` (mod.rs lines 199–214): only emit a `ReceivedOutput` when `output.value.to_sat() >= DUST` (or the higher economic floor used by the consumer). At minimum, document that `ReceivedOutput` makes no spendability guarantee and require callers to filter, and/or add a value threshold parameter to `scan_transaction`/`scan_block`. Symmetrically, `SignableTransaction::new` should reject or skip inputs whose value is below the marginal fee they impose.

### Proof of Concept

```rust
// Attacker crafts a TX paying 1 sat to a registered Serai script.
// (Requires mining or a relay policy permitting it; outputs >= DUST relay freely.)
let dust_output = TxOut {
  value: Amount::from_sat(1), // or 546..spend_cost at current feerate
  script_pubkey: serai_deposit_script.clone(), // publicly known
};
let attacker_tx = Transaction { /* txin: attacker's input, */ output: vec![dust_output], .. };

// Serai side:
let scanner = Scanner::new(group_key).unwrap();
let received = scanner.scan_transaction(&attacker_tx);
assert_eq!(received.len(), 1);              // reported as a spendable ReceivedOutput
assert_eq!(received[0].value(), 1);          // worth 1 sat

// Spending it: input adds ~230 WU (~57 vbytes) to the transaction.
// At even 1 sat/vbyte the fee cost (~57 sats) exceeds the 1-sat input value,
// so the "received" funds are net-negative and unrecoverable.
// With a >=546-sat output, relay policy accepts the deposit TX while the
// scanner still reports an input that costs more to spend than it is worth.
```

Relevant code: [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) 

Caveat: the severity depends on how the out-of-scope processor credits scanned outputs and whether it applies its own `DUST`/`MIN_OUTPUT` floor before treating a `ReceivedOutput` as a deposit; within the in-scope crate itself, the reporting/spending mismatch demonstrated above is concrete.

### Citations

**File:** networks/bitcoin/src/wallet/mod.rs (L88-97)
```rust
/// A spendable output.
#[derive(Clone, PartialEq, Eq, Debug)]
pub struct ReceivedOutput {
  // The scalar offset to obtain the key usable to spend this output.
  offset: Scalar,
  // The output to spend.
  output: TxOut,
  // The TX ID and vout of the output to spend.
  outpoint: OutPoint,
}
```

**File:** networks/bitcoin/src/wallet/mod.rs (L199-213)
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
```

**File:** networks/bitcoin/src/wallet/send.rs (L165-169)
```rust
    for (_, amount) in payments {
      if *amount < DUST {
        Err(TransactionError::DustPayment)?;
      }
    }
```

**File:** networks/bitcoin/src/wallet/send.rs (L204-221)
```rust
    let (mut weight, vbytes) = Self::calculate_weight_vbytes(tx_ins.len(), payments, None);

    let mut needed_fee = fee_per_vbyte * vbytes;
    // Technically, if there isn't change, this TX may still pay enough of a fee to pass the
    // minimum fee. Such edge cases aren't worth programming when they go against intent, as the
    // specified fee rate is too low to be valid
    // bitcoin::policy::DEFAULT_MIN_RELAY_TX_FEE is in sats/kilo-vbyte
    if needed_fee < ((u64::from(bitcoin::policy::DEFAULT_MIN_RELAY_TX_FEE) * vbytes) / 1000) {
      Err(TransactionError::TooLowFee)?;
    }

    if input_sat < (payment_sat + needed_fee) {
      Err(TransactionError::NotEnoughFunds {
        inputs: input_sat,
        payments: payment_sat,
        fee: needed_fee,
      })?;
    }
```
