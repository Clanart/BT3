### Title
Scanner::scan_block reports immature coinbase outputs as spendable ReceivedOutputs - (File: networks/bitcoin/src/wallet/mod.rs)

### Summary
`Scanner::scan_block` iterates over every transaction in a block, including the coinbase (`block.txdata[0]`), and returns matching outputs as `ReceivedOutput`s indistinguishable from ordinary spendable UTXOs. Coinbase outputs are unspendable until 100 blocks of maturity, so a caller can construct and sign a transaction spending an immature output. This mirrors the report's bug class: a validity/freshness window that is too permissive, causing not-yet-valid data to be treated as settled.

### Finding Description
`scan_transaction` (networks/bitcoin/src/wallet/mod.rs:199-214) matches `output.script_pubkey` against the registered scripts and pushes a `ReceivedOutput` for every match, recording only `offset`, `TxOut`, and `OutPoint` — no block height or maturity metadata. `scan_block` (lines 221-227) calls it on `block.txdata` including index 0, the coinbase. The `ReceivedOutput` struct (lines 89-97) carries no field capable of expressing maturity, and `SignableTransaction::new` (networks/bitcoin/src/wallet/send.rs:150-256) performs no coinbase/maturity check — it happily consumes the `ReceivedOutput`s it is given. Because `taproot_key_spend_signature_hash` is signed over `Prevouts::All` (send.rs:375-386), the threshold signing set will produce a fully valid, standard-weight transaction that the Bitcoin network rejects as a non-BIP68/immature coinbase spend, or worse, that gets accepted into a locally built mempool of expected funds.

### Impact Explanation
An unprivileged party sending a Bitcoin transaction to a registered offset address is the normal flow; the reachable impact here is that funds are reported received (as `ReceivedOutput`s) that are not spendable — an accepted impact class. A coordinator/processor that scans a fresh block and immediately builds a `SignableTransaction` will produce a transaction which cannot be broadcast, and depending on accounting may treat immature rewards as liquid collateral. Additionally, since offsets are surjective and `register_offset` collisions return `None` (mod.rs:187-189), an immature coinbase output consumes a registered offset/script slot without ever becoming spendable under the intended flow.

### Likelihood Explanation
Any miner producing a block paying to a scanned script (or any block's coinbase matching a registered script) triggers it — no privileged position required; the bytes are public block data fed to `scan_block`. The only mitigation is the doc comment at lines 217-220 advising a manual post-processing pass, which shifts a consensus-critical invariant (coinbase maturity) onto integrators rather than enforcing it in the type that represents spendability.

### Recommendation
Track confirmation/maturity status in `ReceivedOutput` (e.g., store the block height or a `coinbase: bool` flag, set when `tx.is_coinbase()` during `scan_block`), and have `SignableTransaction::new` reject immature coinbase inputs. At minimum, skip `block.txdata[0]` inside `scan_block` and expose a separate explicitly-marked `scan_coinbase` path so a too-permissive default cannot silently report unspendable funds.

### Proof of Concept
```rust
// Given a Scanner with a registered offset whose P2TR script `s` is in `scripts`:
let mut scanner = Scanner::new(group_key).unwrap();
scanner.register_offset(offset_scalar);

// Construct/regtest-mine a block whose coinbase pays to `s`.
let block: Block = mined_block_paying_to(s);
let received = scanner.scan_block(&block);
// `received[0]` is a coinbase output: unspendable for 100 blocks,
// yet indistinguishable from a normal output.
assert!(!received.is_empty());

// No maturity check exists — this builds and the FROST machines sign it.
let tx = SignableTransaction::new(received, &payments, None, None, fee_per_vbyte).unwrap();
// Broadcast fails: "bad-txns-premature-spend-of-coinbase".
```