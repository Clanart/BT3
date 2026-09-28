### Title
Coinbase outputs are reported as received by `Scanner::scan_block` despite being unspendable until maturity - (File: networks/bitcoin/src/wallet/mod.rs)

### Summary
The external report's bug class is a divergence between accounted value and spendable value: EigenLayer caps the credited amount per validator and diverts the excess to a delayed-withdrawal router, so the LRT's accounting assumes funds were received that cannot actually be settled/withdrawn. In Serai's bitcoin wallet, `Scanner::scan_block` iterates over the entire `block.txdata`, including the coinbase transaction at `txdata[0]`, and returns every matching output as a `ReceivedOutput`. Coinbase outputs are consensus-immature for 100 blocks, so the scanner reports funds as received that are not spendable, exactly matching the "value routed to a location the accounting layer can't claim" class.

### Finding Description
`Scanner::scan_block` calls `scan_transaction` for every transaction in the block, with no exclusion of `txdata[0]` (the coinbase):

```rust
// networks/bitcoin/src/wallet/mod.rs
pub fn scan_block(&self, block: &Block) -> Vec<ReceivedOutput> {
    let mut res = Vec::new();
    for tx in &block.txdata {
        res.extend(self.scan_transaction(tx));
    }
    res
}
```

`scan_transaction` matches purely on `output.script_pubkey` against the registered offset map and emits a `ReceivedOutput` for each match, with no maturity check:

```rust
if let Some(offset) = self.scripts.get(&output.script_pubkey) {
    res.push(ReceivedOutput {
        offset: *offset,
        output: output.clone(),
        outpoint: OutPoint::new(tx.compute_txid(), vout),
    });
}
```

The code comments acknowledge the hazard (`"This will also scan the coinbase transaction which is bound by maturity. If received outputs must be immediately spendable, a post-processing pass is needed"`), but nothing in the in-scope wallet code enforces that pass: `scan_block` unconditionally returns coinbase outputs as normal `ReceivedOutput`s indistinguishable from spendable ones, and `ReceivedOutput` carries no flag that the output is immature. The test helper `send_and_get_output` even relies on this path, mining 100 blocks after the coinbase before using the output — confirming the output is only usable once externally matured.

Analogous to the EigenPod issue: just as the LRT requests 3200 ETH but only ~credited shares are claimable while the excess sits in the delayed router, a consumer of `scan_block` sees a `ReceivedOutput` with a full balance while the underlying UTXO cannot be spent in any transaction until 100 confirmations have elapsed. Any balance/spendable-funds accounting built on these outputs will over-report spendable funds.

### Impact Explanation
An unprivileged party (anyone who mines a block, or who can cause a coinbase to pay a Serai-registered script) can cause the wallet/processor to report received, spendable funds that consensus forbids spending. If such an output is selected as an input to `SignableTransaction::new` / the signing pipeline, the resulting transaction is invalid and rejected by the network — burning the attempt — and until maturity the accounted balance overstates spendable funds, which can cause downstream withdrawal/payment plans to be built on funds that do not exist yet. This is "funds reported received that are not spendable," one of the accepted impact classes.

### Likelihood Explanation
Coinbase outputs pay to miner-chosen addresses, but a miner has full control of the coinbase outputs and can include a P2TR output to any publicly known Serai vault/offset script at no cost beyond normal mining. Because `Scanner` matches on `script_pubkey` alone, any registered script (including external/deposit scripts derivable from the group key) is a target. Reachability requires no key material and no collusion — only a mined block, which is precisely the untrusted-transaction input surface permitted for this analysis. Likelihood is bounded by mining difficulty on mainnet but is trivial on any chain where the attacker has hash power, and requires only that downstream code consume `scan_block` (or `scan_transaction` on `txdata[0]`) without the manual filtering the doc comment demands.

### Recommendation
Filter the coinbase transaction inside `scan_block`/`scan_transaction` rather than relying on callers to remember the documented post-pass — e.g., skip `tx.is_coinbase()` (or skip `block.txdata[0]`) and only emit coinbase-matched outputs after recording their block height so maturity can be enforced. Alternatively, mark `ReceivedOutput`s sourced from coinbase transactions with an immaturity flag/height so consumers cannot treat them as spendable inputs.

### Proof of Concept
1. Obtain the vault group key's P2TR script (or any registered offset script) — it is publicly derivable, e.g. via `p2tr_script_buf(key)`.
2. Mine a block whose coinbase (`txdata[0]`) pays that script.
3. Call `Scanner::scan_block(&block)`. The coinbase output is returned as a `ReceivedOutput` with the full value and a valid `OutPoint`, indistinguishable from a mature deposit — as demonstrated by `networks/bitcoin/tests/wallet.rs::send_and_get_output`, which scans the coinbase and must externally mine 100 additional blocks before the output is usable.
4. Feed that `ReceivedOutput` into `SignableTransaction::new`: construction succeeds and the transaction is signed, but any node rejects broadcast because the coinbase input is immature — funds were accounted as spendable that are not.

Note: the doc comment at `scan_block` discloses the maturity caveat, so this finding stands only where the in-scope API is consumed without the manual exclusion; the in-scope code itself performs no enforcement and provides no marker distinguishing immature outputs.