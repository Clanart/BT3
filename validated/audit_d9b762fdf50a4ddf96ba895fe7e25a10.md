### Title
Payments to the multisig's own scanned scripts are not filtered, so self-payments burn fees and re-enter as new `ReceivedOutput`s — (File: networks/bitcoin/src/wallet/send.rs)

### Summary
Analogous to the WETH→WETH swap that charged a fee for a no-op, `SignableTransaction::new` accepts payments whose `script_pubkey` belongs to the very wallet spending the transaction (the change script or any offset registered in `Scanner::scripts`). There is no equality check anywhere in the in-scope code that drops such self-payments, so the multisig spends its UTXOs, pays a miner fee, and the resulting output is later re-detected by `Scanner::scan_transaction` as a fresh inbound deposit.

### Finding Description
The bug class is a missing identity/equality fast-path: an operation whose "from" and "to" are the same party proceeds and is charged a cost anyway.

In `SignableTransaction::new`, payments are copied verbatim into `tx_outs` with only a dust check:

```rust
for (_, amount) in payments {
  if *amount < DUST { Err(TransactionError::DustPayment)?; }
}
``` [1](#0-0) 

The change output is appended only if the leftover exceeds `DUST`: [2](#0-1) 

No code checks whether `payment.0` equals `change` or any script the wallet scans. `Scanner::scan_transaction` will match any output whose `script_pubkey` is in `self.scripts` — which includes the base key's script (inserted in `Scanner::new`) and every registered offset: [3](#0-2) 

So a transaction paying the wallet's own script produces a `ReceivedOutput` indistinguishable from a genuine inbound deposit. The importance of filtering self-payments is already recognized elsewhere in the codebase — `processor/src/multisigs/scheduler/utxo.rs` explicitly drops payments to the multisig's own branch address, and its comment concedes this is "not comprehensive": a payment "may still be made to another active multisig's branch address" — and, per the code, to any non-branch offset script of the same multisig, which is never filtered at all: [4](#0-3) 

### Impact Explanation
An unprivileged user controls the destination address of an outbound payment (e.g., a withdrawal/burn instruction). They can set it to any script the multisig scans — most easily their own deposit address, which is a `Scanner`-registered offset script derived from the public group key. Consequences:

1. The multisig spends real UTXOs and pays `needed_fee` in miner fees for a payment that returns the funds to itself — pure loss, exactly the "unnecessary fee for a no-op" class of the reference finding.
2. The change output plus the self-payment output are re-scanned by `Scanner::scan_block`/`scan_transaction` and emitted as new `ReceivedOutput`s, inflating the wallet's spendable-output set with outputs that correspond to already-accounted funds, corrupting downstream accounting.

### Likelihood Explanation
The destination script is fully attacker-chosen public input, and the target scripts are publicly derivable (`p2tr_script_buf` of the public group key plus publicly known/registered offsets). Nothing in `SignableTransaction::new`, `Scanner`, or the scheduling path rejects a payment to a scanned script other than the single branch-address filter. The only mitigation is whether higher-layer code happens to refuse such addresses — no such check exists in the in-scope code.

### Recommendation
Mirror the recommended `toToken != WETH` guard: in `SignableTransaction::new` (and/or the scheduler, comprehensively rather than for the branch address only), reject or drop any `payment` whose `script_pubkey` is `change` or is present in the wallet's `Scanner::scripts` map. Since the scanner owns the script set, the cleanest fix is a `Scanner::is_our_script(&ScriptBuf) -> bool` check applied to every payment before transaction construction.

### Proof of Concept
1. Attacker deposits to the multisig via a registered offset, obtaining deposit address script `S` (`p2tr_script_buf(key + G*offset)`), which is in `Scanner::scripts`.
2. Attacker triggers an outbound payment with `(S, amount)` where `amount >= DUST`.
3. `SignableTransaction::new` builds a tx spending the multisig's UTXOs, paying `needed_fee = fee_per_vbyte * vbytes` to miners, with an output to `S`.
4. `Scanner::scan_transaction` on the confirmed tx returns a `ReceivedOutput { offset, outpoint }` for the self-payment — funds "received" that were never inbound, plus the fee irreversibly spent.

### Citations

**File:** networks/bitcoin/src/wallet/send.rs (L165-169)
```rust
    for (_, amount) in payments {
      if *amount < DUST {
        Err(TransactionError::DustPayment)?;
      }
    }
```

**File:** networks/bitcoin/src/wallet/send.rs (L199-214)
```rust
            .expect("data didn't fit into PushBytes depsite being checked"),
        ),
      })
    }

    let (mut weight, vbytes) = Self::calculate_weight_vbytes(tx_ins.len(), payments, None);

    let mut needed_fee = fee_per_vbyte * vbytes;
    // Technically, if there isn't change, this TX may still pay enough of a fee to pass the
    // minimum fee. Such edge cases aren't worth programming when they go against intent, as the
    // specified fee rate is too low to be valid
    // bitcoin::policy::DEFAULT_MIN_RELAY_TX_FEE is in sats/kilo-vbyte
    if needed_fee < ((u64::from(bitcoin::policy::DEFAULT_MIN_RELAY_TX_FEE) * vbytes) / 1000) {
      Err(TransactionError::TooLowFee)?;
    }

```

**File:** networks/bitcoin/src/wallet/send.rs (L224-235)
```rust
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

**File:** processor/src/multisigs/scheduler/utxo.rs (L318-333)
```rust
    // Drop payments to our own branch address
    /*
      created_output will be called any time we send to a branch address. If it's called, and it
      wasn't expecting to be called, that's almost certainly an error. The only way to guarantee
      this however is to only have us send to a branch address when creating a branch, hence the
      dropping of pointless payments.

      This is not comprehensive as a payment may still be made to another active multisig's branch
      address, depending on timing. This is safe as the issue only occurs when a multisig sends to
      its *own* branch address, since created_output is called on the signer's Scheduler.
    */
    {
      let branch_address = N::branch_address(self.key).unwrap();
      payments =
        payments.drain(..).filter(|payment| payment.address != branch_address).collect::<Vec<_>>();
    }
```
