### Title
Scanner reports dust/unspendable outputs as received funds with no minimum-value check — (`networks/bitcoin/src/wallet/mod.rs`)

### Summary
The external report describes a liquidation path that deliberately leaves a "dust" residue small enough that no rational actor will ever clean it up, so the protocol's bad-debt/dust guard (left at `0`) never fires and the residue poisons the pool's accounting. The Serai analog is `Scanner::scan_transaction`: it matches outputs solely by `script_pubkey` and returns a `ReceivedOutput` for **any** value — including dust and even zero-value outputs — with no lower bound. These outputs are reported as spendable received funds even though spending them costs more than they are worth (or is non-relayable entirely), so they can never be economically cleaned up.

### Finding Description
`scan_transaction` iterates `tx.output` and pushes a `ReceivedOutput` for every output whose `script_pubkey` is in `self.scripts`, without inspecting `output.value` at all (`networks/bitcoin/src/wallet/mod.rs:199-214`). `ReceivedOutput` is documented as "A spendable output" (`mod.rs:88-89`), yet nothing enforces spendability.

The only dust bound in the crate is `DUST = 546` in `send.rs`, and it is applied exclusively to *outgoing* payments (`send.rs:165-169`) and to the *change* output (`send.rs:228-234`). Inbound value is never checked:

- An attacker can mine (or have relayed under future policy changes) an output paying to a Serai-controlled script with value `0`..`546` sats. A zero-or-sub-dust-value output is consensus-valid even though it is non-standard. `scan_transaction`/`scan_block` will return it as a `ReceivedOutput`, i.e. "funds received."
- Even outputs above 546 but below the marginal cost of spending them are net-negative: each Taproot input adds ~57 vbytes (see the weight analysis cited in `processor/src/networks/bitcoin.rs:606-637`, which sets `DUST = 10_000` precisely because the spend cost dwarfs small values). The wallet scanner has no such bound.

Like the Fraxlend bug, the guard effectively sits at `0`: there is no `min_value` analog of `minCollateralRequiredOnDirtyLiquidation`, so an unprivileged sender can permanently lodge dust positions against the wallet. `SignableTransaction::new` will accept any `ReceivedOutput` passed to it as an input — it computes `input_sat` from all provided outputs and deducts per-input weight in `calculate_weight_vbytes` — so dust inputs either get burned as fee (losing their nominal value plus extra fee) or accumulate forever as liabilities in the reported balance.

### Impact Explanation
Funds reported received that are not spendable: any downstream consumer crediting deposits from `scan_transaction`/`scan_block` results (the processor's scheduler aggregates outputs and amortizes `COST_TO_AGGREGATE`/fees across them) will account value that is economically or literally unspendable. Repeated dust deposits create positions no rational signer set will spend — the exact "dust left behind, never cleaned, accounting insolvency" shape of the Fraxlend finding — inflating the wallet's apparent balance while the last spenders absorb the deficit.

### Likelihood Explanation
Requires only that an unprivileged party send Bitcoin to a scanned `script_pubkey` (depositor-facing address or any registered offset). No collusion, no validator misbehavior, no leaked key. Cost per dust output is trivial (a single non-dust output can be split into hundreds of dust outputs in one transaction). The only constraint is that sub-546-sat outputs need miner cooperation to be mined under current relay policy, which is readily available via out-of-band submission or any policy relaxation; outputs between 546 sats and the spend cost need no accommodation at all.

### Recommendation
Enforce a minimum value in `Scanner::scan_transaction`/`scan_block` (or in `ReceivedOutput` construction), rejecting outputs below a bound tied to the marginal spend cost (the processor's `DUST = 10_000` sats / `COST_TO_AGGREGATE` reasoning, or at minimum the relay dust limit of 546 sats). This is the direct analog of setting `minCollateralRequiredOnDirtyLiquidation` to a non-zero value so dust positions cannot be created in the first place. Alternatively, return the output but flag sub-threshold outputs so callers can exclude them from credited balances and input selection.

### Proof of Concept
1. `let mut scanner = Scanner::new(key).unwrap();` for the multisig's tweaked `key`.
2. Craft a transaction with an output `TxOut { value: Amount::from_sat(1), script_pubkey: p2tr_script_buf(key).unwrap() }` (consensus-valid; non-standard but minable), or `value` of e.g. `600` sats which relays freely yet is below the ~57-vbyte × feerate cost of spending it.
3. `scanner.scan_transaction(&tx)` returns a `ReceivedOutput` with `value() == 1` (or `600`) — reported as a spendable output, while `SignableTransaction::new` spending it would consume more in added fee (`fee_per_vbyte * 57+` vbytes) than the input contributes. Repeated across many outputs, the wallet accumulates unspendable "received" value that no one will ever clean up.