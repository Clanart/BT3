### Title
Two-phase fee calculation allows median-fee drift to block execution of a deemed-fulfillable spend plan - (networks/bitcoin/src/wallet/send.rs)

### Summary
The external report describes a bug where a value committed to storage (`currentStore.reward`) can exceed a later recomputation of the same quantity (`rewardRatePerDay * passedDays * principal`), because the parameters feeding the recomputation can change between the two evaluations — causing an assertion to fail and permanently blocking claims.

The analogous shape in Serai is the split between the fee-estimation path and the transaction-construction path in `SignableTransaction`. `SignableTransaction::new` computes `needed_fee = fee_per_vbyte * vbytes` and enforces `input_sat >= payment_sat + needed_fee`, returning `NotEnoughFunds` otherwise [1](#0-0) . The caller evaluates this function twice with the same inputs but a freshly recomputed `fee_per_vbyte`: once in `needed_fee` (with payments stubbed to `DUST`) to decide whether a Plan is fulfillable, and again in `signable_transaction` with the real payment amounts [2](#0-1)  <cite repo="bsaldua/serai--019" path="processor/src/networks/bitcoin.rs" start="417" end="number" />.

### Finding Description
`make_signable_transaction` derives the fee rate via `self.median_fee(&block_for_fee)` on the block at `block_number`, i.e., the median of per-transaction fee rates in that block <cite repo="bsaldua/serai--019" path="processor/src/networks/bitcoin.rs" start="429" end="number" />. Mempool/block fee rates are influenced by unprivileged transaction senders — any party can raise the median by getting high-fee transactions mined.

The flow in `prepare_send` is:

1. `needed_fee` is called at `block_number` with `calculating_fee = true`, which substitutes every payment amount with `Self::DUST` <cite repo="bsaldua/serai--019" path="processor/src/networks/bitcoin.rs" start="433" end="number" />. The resulting `tx_fee` is used to decide the Plan is fulfillable and to amortize the fee across payments <cite repo="bsaldua/serai--019" path="processor/src/networks/mod.rs" start="442" end="number" />.
2. `signable_transaction` is then called, again at `block_number`, with the real amounts. If it returns `None` (including `NotEnoughFunds`), `prepare_send` treats the Plan as unfulfillable, drops all branch payments, and books the theoretical change amount as an operating cost <cite repo="bsaldua/serai--019" path="processor/src/networks/mod.rs" start="445" end="number" />.

Two discrepancies mirror the report's "decreasing parameters" root cause:

- **Parameter drift between phases:** the median fee is recomputed per call from block data; a higher median at the second call increases `needed_fee`, so `input_sat < payment_sat + needed_fee` can newly hold and `SignableTransaction::new` returns `NotEnoughFunds` <cite repo="bsaldua/serai--019" path="networks/bitcoin/src/wallet/send.rs" start="215" end="number" /> — even though the identical Plan was just judged fulfillable. This is the same class as the reward rate being lowered between storage and recomputation.
- **Inconsistent inputs between phases:** the estimation phase uses `DUST` payment amounts while the real phase uses full amounts <cite repo="bsaldua/serai--019" path="processor/src/networks/bitcoin.rs" start="441" end="number" />. The vbytes are identical, but the funds check is not: a Plan whose payments exactly cover `fee + payments` at estimation can fail at real amounts after amortization rounding, and `signable_transaction` "MUST NOT return None if the above was done properly" per the trait contract <cite repo="bsaldua/serai--019" path="processor/src/networks/mod.rs" start="402" end="number" /> — a contract the two-phase structure does not actually guarantee.

Additionally, `SignableTransaction::fee()` computes `sum(prevouts) - sum(outputs)` with a bare subtraction on `u64` <cite repo="bsaldua/serai--019" path="networks/bitcoin/src/wallet/send.rs" start="138" end="number" />; the invariant that keeps this from panicking is only established inside `new`, and `prevouts` are attacker-malleable via `ReceivedOutput::read`, which accepts arbitrary `TxOut`/`offset` bytes with no script-vs-offset consistency check <cite repo="bsaldua/serai--019" path="networks/bitcoin/src/wallet/mod.rs" start="122" end="number" />.

### Impact Explanation
When the second-phase construction fails, the Plan's branch payments are dropped and the expected change is recorded as an operating cost even though the inputs remain on-chain and unspent <cite repo="bsaldua/serai--019" path="processor/src/networks/mod.rs" start="445" end="number" />. If the elevated fee regime persists, the Plan can remain unexecutable — the analog of the claim being permanently blocked. Where the discrepancy comes from the DUST-stub-vs-real-amount inconsistency rather than fee drift, a correctly-computed fulfillable Plan still fails at signing time, violating the `signable_transaction` contract and stalling fund movement.

### Likelihood Explanation
Fee drift requires a mined block whose median fee is meaningfully higher than the one used at estimation; under sustained fee spikes (or deliberate fee inflation by a party incentivized to stall a specific withdrawal set) this is reachable. The DUST-stub inconsistency is deterministic and requires only that the Plan sit near the funds boundary after fee amortization. Neither requires privileged access — median fee composition is driven by arbitrary transaction senders. Severity: Medium — it stalls/mis-accounts funds rather than enabling theft or forgery, and retrying at a new block can recover, so it does not rise to a permanent loss on its own.

### Recommendation
- Reuse the `needed_fee` (and the exact `fee_per_vbyte`) computed during the estimation call for the subsequent `signable_transaction` call on the same Plan, or pass the computed fee into `SignableTransaction::new` rather than re-deriving it — i.e., store and reuse the parameter, the inverse of the report's fix, because here the parameter is externally volatile.
- Alternatively, make the funds check monotonic between phases: run the estimation with the *real* payment amounts so `needed_fee` and `signable_transaction` evaluate `input_sat >= payment_sat + needed_fee` identically, or tolerate `NotEnoughFunds` at signing by falling back to re-amortization instead of returning `None`.
- In `ReceivedOutput::read` / `SignableTransaction::multisig`, validate that `p2tr_script_buf(keys.offset(offset).group_key())` equals the embedded `TxOut`'s `script_pubkey` before accepting untrusted bytes, and use `checked_sub` in `fee()`.

### Proof of Concept
```text
1. A Plan is prepared with inputs summing to I and payments summing to P,
   chosen so I = P + F_est + ε for small ε, where F_est is the fee at
   fee rate r1 (the median of the block at needed_fee time).
2. Attacker-influenced (or organic) mempool conditions raise the median
   fee to r2 > r1 before signable_transaction is evaluated.
3. SignableTransaction::new (networks/bitcoin/src/wallet/send.rs:215)
   computes needed_fee = r2 * vbytes > I - P and returns
   Err(NotEnoughFunds), so signable_transaction returns None.
4. prepare_send (processor/src/networks/mod.rs:442-455) treats the Plan
   as unfulfillable: branches are dropped and the change amount is booked
   as an operating cost, while the input outputs remain unspent on-chain —
   the spend is blocked by a parameter change between two evaluations of
   the same quantity, matching the reported bug class.
```

Note on confidence: the decisive divergence logic lives in `processor/` (out of the strict in-scope list); the in-scope defect is that `SignableTransaction::new`'s fee-dependent funds check is not monotonic/stable across calls with a drifting `fee_per_vbyte`, and that `fee()` and `ReceivedOutput::read` lack defensive checks. If the grader requires the entire causal chain to be inside `networks/bitcoin/src`, this should be downgraded, as the reachable trigger path is in the processor's `median_fee`/`make_signable_transaction`.

### Citations

**File:** networks/bitcoin/src/wallet/send.rs (L206-221)
```rust
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

**File:** processor/src/networks/bitcoin.rs (L803-815)
```rust
  async fn needed_fee(
    &self,
    block_number: usize,
    inputs: &[Output],
    payments: &[Payment<Self>],
    change: &Option<Address>,
  ) -> Result<Option<u64>, NetworkError> {
    Ok(
      self
        .make_signable_transaction(block_number, inputs, payments, change, true)
        .await?
        .map(|signable| signable.needed_fee()),
    )
```
