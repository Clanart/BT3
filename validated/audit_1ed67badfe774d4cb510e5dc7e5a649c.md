### Title
Unhandled `StopIteration` crash in `DataLayerWallet.get_offer_summary()` when parsing a malicious Data Layer offer - ([File: chia/data_layer/data_layer_wallet.py])

### Summary
`DataLayerWallet.get_offer_summary()` uses a bare `next()` call with no default value to locate the "child spend" of a Data-Layer singleton graftroot offer. If an attacker crafts an `Offer` whose graftroot-carrying spend has no corresponding child coin spend in the bundle, `next()` raises `StopIteration` with no fallback, which — analogous to the FFmpeg `decode_main_header()` bug where a function fails to check the return value of `avformat_new_stream()` before dereferencing it — crashes the caller because the missing-value case is never validated before being used.

### Finding Description
In `chia/data_layer/data_layer_wallet.py`, `get_offer_summary()` iterates over an offer's coin spends looking for ones that match the Data Layer singleton puzzle (`match_dl_singleton`). When a matched spend also carries a `GRAFTROOT_DL_OFFERS` delegated puzzle, the code attempts to find the singleton's child spend with: [1](#0-0) 

```
child_spend: CoinSpend = next(
    cs for cs in offer.coin_spends() if cs.coin.parent_coin_info == spend.coin.name()
)
```

This assumes a matching child spend always exists in the offer bundle. There is no `default=` argument and no surrounding `try/except`. `offer.coin_spends()` simply returns whatever `CoinSpend`s were included in the (attacker/counterparty-supplied) `Offer` object [2](#0-1) ; nothing in `Offer` construction, `match_dl_singleton`, or the graftroot detection logic (`match_dl_singleton(spend.puzzle_reveal)` plus `graftroot.uncurry() == GRAFTROOT_DL_OFFERS`) guarantees a corresponding child coin spend is present. An attacker can build an offer file containing a DL singleton spend with a graftroot delegated puzzle but omit (or use a mismatched `parent_coin_info` for) the child spend, causing `next()` to fail to find any matching item and raise `StopIteration`.

This mirrors the reported bug class: a function (`decode_main_header`) that fails to validate the result of a call (`avformat_new_stream()`) before using it, causing a crash on attacker-supplied input. Here, `get_offer_summary()` fails to validate that the generator expression yields a value before assigning it to `child_spend`, causing an unhandled exception on attacker-supplied offer data.

### Impact Explanation
`get_offer_summary()` is called by `DataLayerWallet`/`TradeManager` code paths that summarize an untrusted offer (e.g., when a wallet user or Data Layer client inspects/loads an offer supplied by an offer counterparty, imported from `Offer.from_bytes()`/`.from_bech32()`). This function is reached through `TradeManager.get_dl_offer_summary`, which is exposed via `WalletRpcApi` and used before a user accepts a Data Layer offer. A crafted offer can crash this parsing path (StopIteration escaping a generator/async call typically surfaces as an unhandled exception or `RuntimeError` per PEP 479), disrupting the wallet's ability to process/display offers and constituting a spend/offer-input-triggered processing halt. This is scoped to a Medium severity because it affects availability of the offer-inspection path (a local RPC/wallet function) but does not directly enable unauthorized fund movement, forged asset identity, or consensus divergence.

### Likelihood Explanation
Likelihood is fairly high for a targeted DoS: any offer counterparty or third party who can hand a malformed/malicious Data Layer offer file to a wallet user (a realistic and common flow — offers are exchanged out-of-band and inspected via RPC before acceptance) can trigger the crash purely by crafting a `CoinSpend` with a `match_dl_singleton`-matching puzzle reveal and a `GRAFTROOT_DL_OFFERS` graftroot solution, while omitting the expected child spend. No privileged access or malicious full node/peer/farmer is required — this is reachable strictly through offer content, consistent with in-scope "offers and trades" reachability.

### Recommendation
Add an explicit `default=None` to the `next()` call and validate the result before use, raising a clear `ValueError` (e.g., "Malformed Data Layer offer: missing child spend for graftroot singleton") instead of allowing an unhandled `StopIteration`/`RuntimeError` to propagate. This mirrors the null-check fix applied upstream in FFmpeg for `avformat_new_stream()`'s return value.

### Proof of Concept
1. Construct a `WalletSpendBundle` containing a single `CoinSpend` whose `puzzle_reveal` matches `match_dl_singleton` (a DL singleton puzzle) and whose `solution` contains a delegated puzzle at path `rrffrf` that uncurries to `GRAFTROOT_DL_OFFERS`.
2. Ensure no other `CoinSpend` in the bundle has `coin.parent_coin_info` equal to this spend's `coin.name()` (i.e., omit or mismatch the expected child spend).
3. Wrap this bundle in an `Offer` object (e.g., via `Offer({}, bundle, driver_dict)`) and serialize/exchange it as an offer file.
4. Have the victim wallet call `TradeManager.get_dl_offer_summary` (or `DataLayerWallet.get_offer_summary` directly) on this offer, e.g. through the `get_offer_summary` wallet RPC before accepting the offer.
5. Observe that the `next(...)` call in `get_offer_summary()` at `chia/data_layer/data_layer_wallet.py:1221-1223` raises `StopIteration` (surfacing as an unhandled exception/`RuntimeError`), crashing or erroring out the offer-summary RPC call instead of gracefully reporting a malformed offer.

### Citations

**File:** chia/data_layer/data_layer_wallet.py (L1221-1223)
```python
                    child_spend: CoinSpend = next(
                        cs for cs in offer.coin_spends() if cs.coin.parent_coin_info == spend.coin.name()
                    )
```

**File:** chia/wallet/trading/offer.py (L236-237)
```python
    def coin_spends(self) -> list[CoinSpend]:
        return self._bundle.coin_spends
```
