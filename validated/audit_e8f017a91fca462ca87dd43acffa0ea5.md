### Title
Hardcoded relay-policy constants (`DUST`, `DEFAULT_MIN_RELAY_TX_FEE`) in `SignableTransaction::new` can produce unspendable outputs or reject valid transactions when Bitcoin policy changes - ([File: networks/bitcoin/src/wallet/send.rs](networks/bitcoin/src/wallet/send.rs))

### Summary
`SignableTransaction::new` hardcodes Bitcoin's *current* relay/policy rules — a 546-satoshi dust threshold and the `DEFAULT_MIN_RELAY_TX_FEE` minimum relay feerate — directly into transaction construction and validation. This is structurally identical to Solidity's `.transfer`, which hardcodes the *current* 2300-gas stipend into a payment: a value baked into the protocol's economics at write time that becomes incorrect when the underlying cost model changes. If Bitcoin Core's dust or minimum-relay-fee rules change (they have changed historically and were reduced again recently), the code either produces change outputs that are no longer spendable/relayable, or rejects transactions that would be perfectly valid.

### Finding Description
`DUST` is defined as a fixed `546` satoshi constant copied from a specific Bitcoin Core commit's `policy.cpp`:

```rust
// networks/bitcoin/src/wallet/send.rs:27-32
// https://github.com/bitcoin/bitcoin/blob/306ccd49.../src/policy/policy.cpp#L26-L63
pub const DUST: u64 = 546;
```

This constant gates two distinct decisions inside `SignableTransaction::new` (`send.rs:150-256`):

1. **Payment validity** (`send.rs:165-169`): any payment `amount < DUST` is rejected with `DustPayment`, even though the actual dust limit is a per-output-type relay rule (for P2TR/segwit outputs it is substantially lower than 546).
2. **Change output creation** (`send.rs:223-235`): a change output is only appended when `input_sat - payment_sat - fee_with_change >= DUST`. The threshold used to decide "is this output spendable" is frozen at the 546-sat policy value rather than derived from the prevailing feerate, and nothing re-checks whether the emitted change output would satisfy the *actual* mempool dust rule at broadcast time.

Additionally, the fee floor check at `send.rs:211` hardcodes `bitcoin::policy::DEFAULT_MIN_RELAY_TX_FEE` (1000 sat/kvB):

```rust
if needed_fee < ((u64::from(bitcoin::policy::DEFAULT_MIN_RELAY_TX_FEE) * vbytes) / 1000) {
  Err(TransactionError::TooLowFee)?;
}
```

Both constants are relay-policy parameters — Bitcoin's analogue of the gas-cost schedule that `.transfer` assumed invariant. Bitcoin Core has already changed these values (the dust rule is feerate-derived: `dust = 3 * min_relay_feerate * serialized_size`; the minimum relay feerate itself has been lowered in newer releases).

### Impact Explanation
- If the relay dust threshold or minimum feerate *increases*, `SignableTransaction::new` will happily append a change output (e.g., 600 sat) that is dust under the new policy. Such an output is non-standard to spend and economically unspendable (it costs more in fees to spend than it is worth) — funds are irrevocably committed to a dead output, analogous to a `.transfer` call reverting/stranding funds when gas costs rise.
- If the limits *decrease*, legitimate low-value payments and low-fee transactions that would be relayed and confirmed are rejected client-side (`DustPayment` / `TooLowFee`), a liveness failure driven purely by a stale hardcoded constant — the same failure mode as `.transfer` breaking under a hard fork.

### Likelihood Explanation
These are consensus-external policy values that Bitcoin Core adjusts across releases; the code itself acknowledges this by pinning the reference to a specific commit hash. Any operator paying to a recipient whose payment amount or chosen `fee_per_vbyte` falls near these boundaries hits the stale assumption with no local workaround — `SignableTransaction::new` offers no override for the dust or min-fee constants. Reachability is via public transaction parameters (payment amounts, fee rate, data) supplied to `new`.

### Recommendation
Do not bake relay policy into transaction construction as fixed constants:
- Derive the dust threshold from the actual output script type and the current/feared feerate (e.g., `GetDustThreshold`-style: `3 * feerate * (input_vsize + output_size)`), or at minimum parameterize `DUST` per script type rather than a blanket 546.
- Treat `DEFAULT_MIN_RELAY_TX_FEE` as a default, not a floor — either query the node's `getmempoolinfo`/`getnetworkinfo` `incrementalrelayfee`/`minrelaytxfee` at construction time, or accept an explicit minimum from the caller and only warn/validate against it.
- Re-validate the computed change amount against the effective dust limit at the time the change output is emitted, not a compile-time constant.

### Proof of Concept
Conceptual, driven by a policy change (exactly as the original `.transfer` finding requires a hard fork):

1. `SignableTransaction::new(inputs, &[(addr, 10_000)], Some(change_script), None, fee_per_vbyte)` computes `value = input_sat - payment_sat - fee_with_change = 600` sat.
2. `600 >= DUST (546)` at `send.rs:229`, so a change `TxOut` of 600 sat is pushed into the transaction and it is signed/broadcast.
3. Under a Bitcoin Core release where the dust rule for that change output type is raised above 600 sat (or the prevailing feerate makes spending it cost more than 600 sat), the change output is dust: it cannot be relayed/spent and is effectively burned — Serai "sent" funds it can never recover.
4. Conversely, under a release where `minrelaytxfee` drops below 1000 sat/kvB, a caller passing a valid `fee_per_vbyte` below the stale floor is unconditionally rejected with `TooLowFee` at `send.rs:211-213`, even though the transaction would confirm.

Both failure modes stem from the same root cause as M-01: a protocol-level cost/policy parameter frozen as a constant at write time inside the code path that moves funds.