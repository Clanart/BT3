### Title
Scanner accepts dust/below-cost outputs and `SignableTransaction` imposes no input-value floor, making received funds economically unspendable - ([File: networks/bitcoin/src/wallet/mod.rs](networks/bitcoin/src/wallet/mod.rs))

### Summary
The Rio bug class — a hardcoded resource limit making legitimately-sent funds unreceivable/unspendable — maps onto `bitcoin-serai` as a hardcoded, policy-dependent value floor that is applied asymmetrically: `SignableTransaction::new` enforces `DUST = 546` on *outgoing* payments, while `Scanner::scan_transaction` reports *any* output paying to a registered script as a `ReceivedOutput`, and `SignableTransaction::new` performs no minimum-value or economic-viability check on *inputs*.

### Finding Description
`Scanner::scan_transaction` matches outputs purely on `script_pubkey` membership in `self.scripts` and pushes a `ReceivedOutput` regardless of `output.value` (networks/bitcoin/src/wallet/mod.rs:199-214). An unprivileged third party can send outputs of any value — including 1 sat — to any registered offset script.

`SignableTransaction::new` then consumes `inputs: Vec<ReceivedOutput>` with no check that any input can cover its own spending cost (networks/bitcoin/src/wallet/send.rs:150-256). The fee model charges `fee_per_vbyte * vbytes`, and each Taproot input adds ~57 vbytes (~230 WU) of weight (send.rs:62-127). An input of 546 sats costs ~570 sats to spend at 10 sat/vbyte — more than its value. The hardcoded `DUST = 546` (send.rs:32) is itself a copy of Bitcoin Core's relay-policy constant, which is not consensus and has changed historically; if the relay dust threshold or `DEFAULT_MIN_RELAY_TX_FEE` rises, outputs the wallet legitimately creates at the `DUST` boundary become non-standard and unspendable — the same "fixed limit breaks under fee/policy change" failure as the hardcoded `gas: 10_000` in `Asset.transferETH`.

### Impact Explanation
- **Funds reported received that are not spendable**: the scanner reports dust outputs as `ReceivedOutput`s; when selected as inputs they consume more fee than they contribute, so the wallet either burns its own funds paying for them or the transaction errors `NotEnoughFunds`/`TooLowFee` — the received balance is effectively trapped, mirroring LRT tokens trapped in `RioLRTWithdrawalQueue`.
- **Policy drift**: payments at exactly `DUST` are permitted today but may become unrelayable if Bitcoin Core raises `dustRelayFee` (as has happened across releases, analogous to EIP-1884/EIP-2929 repricing), leaving wallet-created outputs stranded.
- An attacker can perpetually dust-spam a known Serai address (the primary script registered by `Scanner::new` is public), forcing every constructed transaction that includes those inputs to pay net-negative input fees.

### Likelihood Explanation
Dusting well-known addresses is a routine, zero-permission attack on Bitcoin; the primary receive script is derivable from the public group key, so no scanning of offset registrations is even needed. Any downstream selection logic that does not pre-filter `ReceivedOutput`s by value (the in-scope wallet code provides no such filter) will include them. The policy-drift component requires a Bitcoin Core policy change, which is infrequent but has precedent (e.g., dust threshold reductions and the 2024+ `dustRelayFee` discussions), matching the report's opcode-repricing argument.

### Recommendation
- In `Scanner::scan_transaction`, reject (or mark) outputs below a value floor sufficient to cover their input weight at a conservative fee rate (e.g., the `input_vbytes * fee_rate` break-even), rather than reporting every matching script.
- In `SignableTransaction::new`, reject inputs whose value is below the marginal fee they add (`57 vbytes * fee_per_vbyte`), so dust inputs can never make a transaction economically worse.
- Derive `DUST` from `bitcoin::policy` constants or parameterize it rather than hardcoding `546`, so the wallet tracks relay policy instead of pinning it.

### Proof of Concept
```rust
// Attacker sends a 1-sat output to the victim's primary script:
let attacker_tx = Transaction {
    output: vec![TxOut {
        value: Amount::from_sat(1),
        script_pubkey: p2tr_script_buf(victim_key).unwrap(), // public key, no permission needed
    }],
    ..
};

// Scanner reports it as spendable funds:
let outputs = scanner.scan_transaction(&attacker_tx);
assert_eq!(outputs.len(), 1);
assert_eq!(outputs[0].value(), 1); // "received" — but costs ~570 sat to spend at 10 sat/vb

// When included as an input, SignableTransaction charges its weight without
// checking the input can pay for itself (networks/bitcoin/src/wallet/send.rs:150-256):
let tx = SignableTransaction::new(vec![dust_output], &payments, change, None, 10);
// needed_fee increases by ~570 sat while contributing 1 sat — net loss, or
// NotEnoughFunds, trapping the real inputs until the dust is manually excluded.
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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

**File:** networks/bitcoin/src/wallet/send.rs (L28-32)
```rust
// https://github.com/bitcoin/bitcoin/blob/306ccd4927a2efe325c8d84be1bdb79edeb29b04/src/policy/policy.cpp#L26-L63
// As the above notes, a lower amount may not be considered dust if contained in a SegWit output
// This doesn't bother with delineation due to how marginal these values are, and because it isn't
// worth the complexity to implement differentation
pub const DUST: u64 = 546;
```

**File:** networks/bitcoin/src/wallet/send.rs (L150-156)
```rust
  pub fn new(
    mut inputs: Vec<ReceivedOutput>,
    payments: &[(ScriptBuf, u64)],
    change: Option<ScriptBuf>,
    data: Option<Vec<u8>>,
    fee_per_vbyte: u64,
  ) -> Result<SignableTransaction, TransactionError> {
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
