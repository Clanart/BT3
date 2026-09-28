### Title
`Scanner::scan_block` reports immature coinbase outputs as spendable, enabling forced invalid spends / fee griefing - (File: networks/bitcoin/src/wallet/mod.rs)

### Summary
The external incident describes spam transactions that coerced node operators into repeatedly spending funds from their wallets. The Serai analog lives in the Bitcoin wallet scanner: `Scanner::scan_block` iterates `block.txdata` starting at index `0`, i.e. it scans the coinbase transaction, and returns any coinbase output paying to a registered Serai script as a `ReceivedOutput` indistinguishable from a normal spendable UTXO.

### Finding Description
`scan_block` calls `scan_transaction` for every transaction in `block.txdata`, including `txdata[0]` (the coinbase). `scan_transaction` performs a pure `script_pubkey` match against the registered script table and pushes a `ReceivedOutput { offset, output, outpoint }` with no maturity or coinbase flag. A coinbase output is consensus-unspendable for 100 blocks; any `SignableTransaction` constructed over it (`SignableTransaction::new` in `networks/bitcoin/src/wallet/send.rs`) will produce a signed transaction that every relay node rejects (`bad-txns-premature-spend-of-coinbase`).

The only mitigation is a doc comment on `scan_block` stating "a post-processing pass is needed to remove those outputs" — but the `ReceivedOutput` type itself carries no coinbase marker, so nothing downstream can distinguish the output, and the in-scope wallet API silently treats it as spendable balance.

Reachability: mining is permissionless. Any unprivileged party who wins a block (or rents hash power, or — realistically — any miner pool crafting a coinbase) can include a coinbase output paying to the Serai multisig's P2TR script (e.g. the external/branch/change/forwarded offset scripts). This is "a Bitcoin transaction they send" within the threat model.

### Impact Explanation
- Funds are reported received that are not spendable: the scanner emits a `ReceivedOutput` for an output that cannot be spent for 100 confirmations.
- An attacker can spam coinbase payments to Serai addresses across blocks, inflating the reported balance and causing the wallet/processor to construct and (in the threshold case) sign transactions that are guaranteed to fail relay, wasting the signing quorum's work and stalling payout plans — the same "spam forces wasteful signing/spending" shape as the reference incident, with griefing of threshold-signing resources as the primary loss.

### Likelihood Explanation
- Requires the attacker to control a coinbase (mine a block or direct a pool payout), which is cheap relative to the target value in aggregated payout scenarios, and each Serai block scanned deterministically picks up the output.
- The bug is deterministic whenever `scan_block` (or equivalent whole-block scanning) is used; there is no coinbase filter anywhere in `networks/bitcoin/src`.
- Caveat: I could not fully verify whether the out-of-scope processor (`processor/src/networks/bitcoin.rs::get_outputs`) uses `scan_block` or filters coinbase transactions itself. If it already filters `txdata[0]` or enforces 100-confirm maturity, impact is reduced to a library-level footgun; the doc comment suggests callers are expected to handle it.

### Recommendation
- In `scan_block`, skip `block.txdata[0]`, or better: mark `ReceivedOutput` with a `coinbase`/immaturity flag and reject such outputs as `SignableTransaction` inputs until mature (track the block height at scan time).
- Alternatively, add a `maturity: Option<NonZeroU64>` field populated when scanning blocks, so downstream consumers can enforce the 100-block rule without rescanning.

### Proof of Concept
```rust
// networks/bitcoin conceptual PoC
let mut scanner = Scanner::new(key).unwrap(); // Serai multisig key

// Attacker mines a block whose coinbase pays to the Serai P2TR script
let block: Block = /* block where txdata[0].output[0].script_pubkey ==
                       p2tr_script_buf(key).unwrap() */;

let outputs = scanner.scan_block(&block);      // networks/bitcoin/src/wallet/mod.rs:221
assert_eq!(outputs.len(), 1);                  // immature coinbase reported as a normal output

// Downstream code treats it as spendable
let tx = SignableTransaction::new(
    outputs.clone(),
    &[(dest_script, outputs[0].value() - 10_000)],
    None, None, 1,
).unwrap();                                    // constructs fine

// After threshold signing, broadcast fails:
// bitcoind rejects with bad-txns-premature-spend-of-coinbase
```
No post-processing can fix this inside `ReceivedOutput` because neither `Output`, `scan_transaction`, nor `scan_block` records that the output originated from a coinbase transaction — the information needed to enforce the maturity rule is discarded at scan time (`networks/bitcoin/src/wallet/mod.rs:199-227`).