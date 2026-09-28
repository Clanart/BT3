### Title
Scanner accepts dust-valued outputs matching a known script_pubkey, registering unspendable funds as received - (File: networks/bitcoin/src/wallet/mod.rs)

### Summary
CVE-2019-13755 is an "insufficient policy enforcement" flaw: Chrome failed to enforce a policy on attacker-supplied page content, letting an unprivileged remote attacker cause a state (disabled extensions) the policy was meant to forbid. The direct analog in Serai is `Scanner::scan_transaction` in `networks/bitcoin/src/wallet/mod.rs`: it enforces only a `script_pubkey` membership check and no value/amount policy, so any third party can craft a Bitcoin transaction paying a dust-valued (even zero-value, below-relay-minimum) P2TR output to a Serai-controlled script and have it registered as a spendable `ReceivedOutput`. This violates the codebase's own stated policy invariant that every received output's value "MUST exceed the cost to spend said output ... by an order of magnitude."

### Finding Description
`Scanner` stores a `scripts: HashMap<ScriptBuf, Scalar>` mapping the group's P2TR script (and every registered offset script) to a scalar offset [1](#0-0) . `scan_transaction` iterates a transaction's outputs and pushes a `ReceivedOutput` for every output whose `script_pubkey` is in the map, with no check on `output.value` [2](#0-1) .

The Serai protocol defines a policy constant `Network::DUST` precisely to bound which received outputs are acceptable: "For any received output, there's the cost to spend the output. This value MUST exceed the cost to spend said output, and should by a notable margin (not just 2x, yet an order of magnitude)" [3](#0-2) . For Bitcoin this is set to 10,000 sats [4](#0-3) . However, nothing enforces this on the receive path: `scan_transaction` accepts any value, and the consuming `get_outputs` maps the result directly into an `Output` with no value filter [5](#0-4) . Meanwhile the send path does enforce value policy — payments below `wallet::DUST` (546 sats) are rejected with `DustPayment` [6](#0-5)  and change outputs below `DUST` are dropped [7](#0-6) , showing the check was intended but omitted for inbound scanning.

### Impact Explanation
An unprivileged attacker sends a transaction (e.g., paying ~500–9,999 sats, or even below Bitcoin Core's relay dust minimum if they are/cooperate with a miner) to the multisig's external/branch/change/forward address script. The output is reported by every validator's scanner as a received `ReceivedOutput`/`Output` credited to Serai [8](#0-7) . Two concrete harms:

1. **Funds reported received that are not economically spendable**: spending a Taproot input costs ~57 vbytes; at a 5+ sat/vbyte environment the spend can cost more than the output's value, violating the `DUST` invariant and either permanently stranding the input or forcing Serai to subsidize consolidation.
2. **Fee/value accounting corruption in plans**: `prepare_send` and `Scheduler` treat scanned outputs as usable inputs, computing `theoretical_change_amount` and `input_sat` from their values [9](#0-8) ; dust inputs increase the transaction's required fee (`needed_fee = fee_per_vbyte * vbytes`) more than they contribute, inflating operating costs and potentially pushing valid plans to `NotEnoughFunds`.

### Likelihood Explanation
Reachability is trivial: the attacker only needs to broadcast a standard (or miner-relayed) Bitcoin transaction paying to a publicly known Serai address — exactly the "Bitcoin transactions they send" surface allowed by scope. `scan_block`/`scan_transaction` run on every confirmed block [10](#0-9) . The cost to the attacker is at most a few thousand sats per fake input, repeatable to fill input selection with up to `MAX_INPUTS` (520) dust inputs [11](#0-10) . No collusion, key material, or validator status is needed.

### Recommendation
Enforce the receive-side value policy in `Scanner::scan_transaction` (or immediately in `get_outputs`): drop outputs whose `output.value.to_sat()` is below the network `DUST` bound before constructing a `ReceivedOutput`. Because `Scanner` is network-generic over `bitcoin_serai` only, the cleanest fix is a `min_value` field on `Scanner` (set to `Bitcoin::DUST` when the processor constructs it) checked inside the `self.scripts.get(&output.script_pubkey)` branch [8](#0-7) . A defense-in-depth assertion in `SignableTransaction::new` rejecting `ReceivedOutput` inputs below `DUST` would prevent the wallet layer from ever signing spends of uneconomical inputs.

### Proof of Concept
```rust
// networks/bitcoin/src/wallet/mod.rs context
// Given a group key `key` with `Scanner::new(key)`:
let mut scanner = Scanner::new(group_key).unwrap();
let target_script = p2tr_script_buf(group_key).unwrap();

// Attacker broadcasts a TX paying 500 sats (<< Bitcoin::DUST = 10_000,
// and even below wallet::DUST = 546 used for outgoing payments)
let attacker_tx = Transaction {
    version: Version(2),
    lock_time: LockTime::ZERO,
    input: vec![/* attacker-funded input */],
    output: vec![TxOut {
        value: Amount::from_sat(500),
        script_pubkey: target_script, // exact match, no value policy applied
    }],
};

// scan_transaction returns a ReceivedOutput crediting 500 sats,
// despite the output violating Network::DUST's MUST and being
// uneconomical/non-standard to spend.
let received = scanner.scan_transaction(&attacker_tx);
assert_eq!(received.len(), 1);        // accepted
assert_eq!(received[0].value(), 500); // below spendable threshold
```

The send path would refuse to *create* such an output (`TransactionError::DustPayment`) [6](#0-5) , proving the policy exists but is unenforced on the attacker-controlled receive path — the same missing-enforcement shape as CVE-2019-13755. Note: the exact call site wiring (whether the processor layer or `Scanner` itself should filter) could not be fully verified within this analysis, but the missing check in `scan_transaction`/`get_outputs` is confirmed from the cited code.

### Citations

**File:** networks/bitcoin/src/wallet/mod.rs (L153-165)
```rust
pub struct Scanner {
  key: ProjectivePoint,
  scripts: HashMap<ScriptBuf, Scalar>,
}

impl Scanner {
  /// Construct a Scanner for a key.
  ///
  /// Returns None if this key can't be scanned for.
  pub fn new(key: ProjectivePoint) -> Option<Scanner> {
    let mut scripts = HashMap::new();
    scripts.insert(p2tr_script_buf(key)?, Scalar::ZERO);
    Some(Scanner { key, scripts })
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

**File:** networks/bitcoin/src/wallet/mod.rs (L221-227)
```rust
  pub fn scan_block(&self, block: &Block) -> Vec<ReceivedOutput> {
    let mut res = Vec::new();
    for tx in &block.txdata {
      res.extend(self.scan_transaction(tx));
    }
    res
  }
```

**File:** processor/src/networks/mod.rs (L295-301)
```rust
  /// Minimum output value which will be handled.
  ///
  /// For any received output, there's the cost to spend the output. This value MUST exceed the
  /// cost to spend said output, and should by a notable margin (not just 2x, yet an order of
  /// magnitude).
  // TODO: Dust needs to be diversified per ExternalCoin
  const DUST: u64;
```

**File:** processor/src/networks/mod.rs (L434-455)
```rust
    let Plan { key, inputs, mut payments, change, scheduler_addendum } = plan;
    let theoretical_change_amount = if change.is_some() {
      inputs.iter().map(|input| input.balance().amount.0).sum::<u64>() -
        payments.iter().map(|payment| payment.balance.amount.0).sum::<u64>()
    } else {
      0
    };

    let Some(tx_fee) = self.needed_fee(block_number, &inputs, &payments, &change).await? else {
      // This Plan is not fulfillable
      // TODO: Have Plan explicitly distinguish payments and branches in two separate Vecs?
      return Ok(PreparedSend {
        tx: None,
        // Have all of its branches dropped
        post_fee_branches: drop_branches(key, &payments),
        // This plan expects a change output valued at sum(inputs) - sum(outputs)
        // Since we can no longer create this change output, it becomes an operating cost
        // TODO: Look at input restoration to reduce this operating cost
        operating_costs: operating_costs +
          if change.is_some() { theoretical_change_amount } else { 0 },
      });
    };
```

**File:** processor/src/networks/bitcoin.rs (L575-576)
```rust
const MAX_INPUTS: usize = 520;
const MAX_OUTPUTS: usize = 520;
```

**File:** processor/src/networks/bitcoin.rs (L626-638)
```rust
    Since these are solely relay rules, and may be raised, we require all outputs be spendable
    under a 5000 sat/kilo-vbyte fee rate.

    5000 sat/kilo-vbyte = 5 sat/vbyte
    5 * 57 = 285 sats/spent-output

    Even if an output took 100 bytes (it should be just ~29-43), taking 400 weight units, adding
    100 vbytes, tripling the transaction size, then the sats/tx would be < 1000.

    Increase by an order of magnitude, in order to ensure this is actually worth our time, and we
    get 10,000 satoshis.
  */
  const DUST: u64 = 10_000;
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

**File:** networks/bitcoin/src/wallet/send.rs (L165-169)
```rust
    for (_, amount) in payments {
      if *amount < DUST {
        Err(TransactionError::DustPayment)?;
      }
    }
```

**File:** networks/bitcoin/src/wallet/send.rs (L223-235)
```rust
    // If there's a change address, check if there's change to give it
    if let Some(change) = change {
      let (weight_with_change, vbytes_with_change) =
        Self::calculate_weight_vbytes(tx_ins.len(), payments, Some(&change));
      let fee_with_change = fee_per_vbyte * vbytes_with_change;
      if let Some(value) = input_sat.checked_sub(payment_sat + fee_with_change) {
        if value >= DUST {
          tx_outs.push(TxOut { value: Amount::from_sat(value), script_pubkey: change });
          weight = weight_with_change;
          needed_fee = fee_with_change;
        }
      }
    }
```
