### Title
Hardcoded relay-policy constants (`DUST`, `DEFAULT_MIN_RELAY_TX_FEE`) used instead of live network parameters produce non-relayable transactions - (File: networks/bitcoin/src/wallet/send.rs)

### Summary
The report's bug class is "compile-time defaults used where a live, network-supplied parameter is required, yielding objects that fail the real admission rules" (`Rent::default` vs `Rent::get` → non-rent-exempt accounts → DoS). The direct analog in Serai's in-scope code is `SignableTransaction::new` in `networks/bitcoin/src/wallet/send.rs`, which hardcodes Bitcoin Core's *default policy defaults* — a `DUST` floor of `546` satoshis and `bitcoin::policy::DEFAULT_MIN_RELAY_TX_FEE` — rather than querying the live node/network fee conditions before building and signing the transaction.

### Finding Description
`SignableTransaction::new` computes `needed_fee = fee_per_vbyte * vbytes` and rejects only if that is below the compile-time relay floor:

```rust
// networks/bitcoin/src/wallet/send.rs:204-213
if needed_fee < ((u64::from(bitcoin::policy::DEFAULT_MIN_RELAY_TX_FEE) * vbytes) / 1000) {
  Err(TransactionError::TooLowFee)?;
}
```

`DEFAULT_MIN_RELAY_TX_FEE` (1000 sat/kvB) is Bitcoin Core's *default* `minrelaytxfee`, not the chain's actual admission rule. The real relay floor is dynamic: a node's effective mempool minimum fee rises above this constant under mempool pressure (`-maxmempool` eviction), and individual nodes may be configured higher. No node query (e.g., `getmempoolinfo`'s `mempoolminfee`/`minrelaytxfee`) is performed anywhere in `networks/bitcoin/src`.

The same pattern exists for the dust gate: `pub const DUST: u64 = 546` is a hardcoded policy value (the comment at `send.rs:27-32` explicitly declines to differentiate output types), and change outputs are only created when `value >= DUST` (`send.rs:228-233`). Dust thresholds are likewise node/policy-dependent.

Additionally, `Scanner::scan_block` (`networks/bitcoin/src/wallet/mod.rs:216-228`) iterates `block.txdata` including `txdata[0]`, the coinbase. Its own doc comment admits coinbase outputs "are bound by maturity" and requires a post-processing pass that does not exist in the wallet crate itself — a second instance of the same class: a protocol-rule parameter (100-block coinbase maturity, live consensus context) ignored in favor of treating every scanned output as spendable.

### Impact Explanation
Analogous to the report, the created/signed object fails the *actual* admission rule rather than the hardcoded one:

- A transaction built with `fee_per_vbyte` satisfying `DEFAULT_MIN_RELAY_TX_FEE` but below the node's current `mempoolminfee` is constructed, threshold-signed via `SignableTransaction`'s FROST flow, and broadcast — then silently fails to propagate/confirm. Inputs committed to the sighash (`Prevouts::All`) are locked in a dead transaction, producing the same DoS outcome as underfunded non-rent-exempt accounts.
- Outputs returned by `scan_block` on the coinbase transaction are reported as spendable `ReceivedOutput`s; a plan built on them produces an invalid transaction (BIP-113/consensus maturity violation), stalling the signing pipeline — funds reported received that are not spendable.

### Likelihood Explanation
Mempool minimum fees exceeding the 1 sat/vB floor is routine during congestion, and anyone can send payments/outputs to the scanned Taproot scripts (public inputs). A miner can also send a coinbase output to a registered script_pubkey; mining is permissionless. The wallet performs no liveness check before finalizing, so the bad object is created deterministically whenever the live parameter diverges from the hardcoded default.

### Recommendation
Query the node's actual fee conditions (e.g., `getmempoolinfo`/`estimatesmartfee`) at transaction-construction time and enforce `needed_fee >= mempoolminfee * vbytes`, rather than `DEFAULT_MIN_RELAY_TX_FEE`. Derive the dust floor per output script type from the live relay fee instead of the fixed `546` constant. In `scan_block`, skip `block.txdata[0]` or tag coinbase outputs so callers cannot treat immature outputs as spendable.

### Proof of Concept
```rust
// networks/bitcoin/src/wallet/send.rs
pub const DUST: u64 = 546;                       // L32: hardcoded policy default
...
let mut needed_fee = fee_per_vbyte * vbytes;
// L211: admission checked against compile-time default, not node's mempoolminfee
if needed_fee < ((u64::from(bitcoin::policy::DEFAULT_MIN_RELAY_TX_FEE) * vbytes) / 1000) {
  Err(TransactionError::TooLowFee)?;
}
```

1. Node's `mempoolminfee` has risen to 5 sat/vB (mempool eviction). Call `SignableTransaction::new(inputs, &payments, change, None, 2)` — passes the hardcoded 1 sat/vB floor, produces and signs a transaction paying 2 sat/vB that will never relay or confirm. Inputs are pinned to the dead sighash → DoS.
2. `Scanner::scan_block` on a block whose coinbase pays `p2tr_script_buf(key)` returns a `ReceivedOutput` that cannot be spent for 100 blocks; any downstream plan spending it yields a consensus-invalid transaction.

Both are reachable purely from public inputs (outputs sent to registered scripts, a chosen fee rate) and stem from substituting static defaults for live chain parameters — the same root cause as `Rent::default` vs `Rent::get`.