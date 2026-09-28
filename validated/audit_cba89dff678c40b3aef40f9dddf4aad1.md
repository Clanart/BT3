### Title
Scanner reports dust/unspendable-value outputs as received funds - ([File: networks/bitcoin/src/wallet/mod.rs](networks/bitcoin/src/wallet/mod.rs))

### Summary
Analogous to the Gitcoin `vote` issue — where a payable entry point accepts value without checking it against what the contract can actually use, permanently trapping the excess — `Scanner::scan_transaction`/`scan_block` in `bitcoin-serai` credit any output paying to a watched `script_pubkey` as a `ReceivedOutput`, with no minimum-value check. An unprivileged third party can send outputs whose value is below the cost of spending them (or even zero-value-adjacent dust) to a Serai P2TR address; the wallet reports them as received balance that can never be economically — or in the extreme, at all — spent.

### Finding Description
`scan_transaction` iterates every `tx.output`, and if `output.script_pubkey` is a registered script, pushes a `ReceivedOutput` regardless of `output.value` (mod.rs:199-214). `ReceivedOutput` itself (mod.rs:90-118) exposes `value()` straight from the `TxOut` with no floor. The only dust constant in the crate, `DUST = 546` (send.rs:32), is enforced solely on *outgoing payments* (`DustPayment`, send.rs:165-169) and on the change output (send.rs:228-234). Nothing rejects a dust input.

Once such an output is accepted into `SignableTransaction::new`, spending it costs ~57 vbytes of witness/input weight (per the fee model in `calculate_weight_vbytes` and the commentary in `processor/src/networks/bitcoin.rs`), so any output below the marginal spend cost burns more in fees than it contributes — value credited that is net-negative to spend. The downstream processor had to bolt on its own `N::DUST` filter in `get_outputs` (`output.balance().amount.0 >= N::DUST`) precisely because the library scanner doesn't do it; any integrator using `bitcoin-serai` directly gets unfiltered crediting. Like the audit issue's ETH trapped in `VotingStrategyImplementation`, the sats are reported as received but have no economical path out.

### Impact Explanation
An attacker can permanently inflate a wallet's reported received balance with unspendable outputs at trivial cost (dust-level transactions to a known P2TR script). If the wallet/accounting layer auto-consolidates scanned outputs, each dust input added makes the transaction *lose* money relative to excluding it; if it rejects on `NotEnoughFunds` for a dust-only input set, the funds sit credited but unmovable — the same "stuck value the contract can't pull out" shape as the source report.

### Likelihood Explanation
Reachable entirely by an unprivileged party: sending a low-value Bitcoin transaction output to a Serai address is a public-input action requiring no key material and no malformed bytes — the scanner accepts it verbatim. The only mitigation is out-of-crate caller discipline.

### Recommendation
Enforce a minimum value in `scan_transaction`/`scan_block` (or at `ReceivedOutput` construction/`read`): skip outputs with `value < DUST` (or a spend-cost-derived floor), mirroring the `DustPayment` check already applied to outgoing payments in `SignableTransaction::new`.

### Proof of Concept
```rust
// Attacker sends a tx paying `value = 1` sat to p2tr_script_buf(victim_key).
// Inside the wallet:
let outputs = scanner.scan_transaction(&attacker_tx);
assert_eq!(outputs.len(), 1);            // credited despite 1-sat value
assert_eq!(outputs[0].value(), 1);       // < DUST, < cost to spend (~57 vb input)

// Wallet attempts to use it:
SignableTransaction::new(vec![outputs[0]], &payments, change, None, fee_rate);
// Either NotEnoughFunds (funds stuck, credited but unspendable),
// or the dust input is spent at a fee cost >> 1 sat (net loss per input).
``` [1](#0-0) [2](#0-1) [3](#0-2)

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

**File:** networks/bitcoin/src/wallet/send.rs (L32-32)
```rust
pub const DUST: u64 = 546;
```

**File:** networks/bitcoin/src/wallet/send.rs (L165-169)
```rust
    for (_, amount) in payments {
      if *amount < DUST {
        Err(TransactionError::DustPayment)?;
      }
    }
```
