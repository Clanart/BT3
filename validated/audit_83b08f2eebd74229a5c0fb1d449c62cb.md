### Title
Scanner reports dust-value outputs as received which can never be economically spent - ([File: networks/bitcoin/src/wallet/mod.rs](networks/bitcoin/src/wallet/mod.rs))

### Summary
`Scanner::scan_transaction` matches received outputs solely by `script_pubkey` and records them as `ReceivedOutput`s without any minimum-value check. Analogous to the Arrakis report (where a missing assignment causes the "return leftover funds" branch to be skipped), here the missing guard condition causes funds to be accepted as received which the wallet can never spend back — the sats are effectively burned rather than returned.

### Finding Description
`Scanner` indexes spendable scripts in `self.scripts` and reports every matching output unconditionally: [1](#0-0) 

There is no check that `output.value` is at least `DUST`/`bitcoin_serai`'s minimum spendable amount. The only size guard in the module is `DUST` in `send.rs`, used to reject *outgoing* dust payments and dust change: [2](#0-1) , [3](#0-2) 

No equivalent filter exists on the receive side. A `ReceivedOutput` of 1 sat (or any value below the cost of spending it — estimated ~285 sats per input at 5 sat/vbyte per the in-code analysis, and effectively anything under `DUST = 10_000`) is reported as a valid received output, yet any transaction spending it would pay more in fees than it contributes, and no sane `SignableTransaction` flow will ever recover it.

### Impact Explanation
An unprivileged party sends a Bitcoin transaction containing a dust-valued output paying to the multisig's P2TR script (e.g., 1 sat, or 546 sats — the relay dust limit which is still far below the 10,000-sat `DUST` constant defined for the network). The scanner reports the output as received; the protocol treats it as real received funds. Those funds are permanently unspendable: they cannot cover the fee of the input that spends them, and `SignableTransaction::new`/`prepare_send` semantics treat input value as contributing to payments. The result is funds reported as received that are not spendable, and, where refunds are owed, the refunded balance can never actually be returned — the direct analog of "users don't get back their ETH."

### Likelihood Explanation
Requires only that an external party craft a Bitcoin transaction with a dust output to a known multisig script — fully public input, no validator cooperation needed. The multisig script pubkey is publicly observable on-chain once used, so the attack is reachable by anyone.

### Recommendation
In `Scanner::scan_transaction`, skip (or separately flag) outputs whose `output.value.to_sat()` is below the network's spendability threshold (the `DUST` constant, i.e., the value at which the output's value exceeds the fee required to spend it at a conservative fee rate). Alternatively, have `ReceivedOutput` carry/verify spendability so downstream consumers never treat sub-dust outputs as received funds.

### Proof of Concept
```rust
// networks/bitcoin context: construct a tx paying 546 sats (relay dust limit,
// well below DUST = 10_000) to the multisig's P2TR script
let key: ProjectivePoint = /* multisig group key */;
let scanner = Scanner::new(key).unwrap();

let dust_tx = Transaction {
  version: Version(2),
  lock_time: LockTime::ZERO,
  input: vec![/* any funding input */],
  output: vec![TxOut {
    value: Amount::from_sat(546),
    script_pubkey: p2tr_script_buf(key).unwrap(),
  }],
};

let received = scanner.scan_transaction(&dust_tx);
// received.len() == 1: a 546-sat output is reported as received funds.
// Spending it costs ~285+ sats of input weight at minimum relay fees and it can
// never fund a >= DUST payment output economically; under Serai's DUST policy it
// is unspendable, yet it was registered as a received output.
```

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

**File:** networks/bitcoin/src/wallet/send.rs (L165-169)
```rust
    for (_, amount) in payments {
      if *amount < DUST {
        Err(TransactionError::DustPayment)?;
      }
    }
```

**File:** networks/bitcoin/src/wallet/send.rs (L228-234)
```rust
      if let Some(value) = input_sat.checked_sub(payment_sat + fee_with_change) {
        if value >= DUST {
          tx_outs.push(TxOut { value: Amount::from_sat(value), script_pubkey: change });
          weight = weight_with_change;
          needed_fee = fee_with_change;
        }
      }
```
