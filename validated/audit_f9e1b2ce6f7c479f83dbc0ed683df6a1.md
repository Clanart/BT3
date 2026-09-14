### Title
Uncaught `AssertionError` from a crafted offer with a self-conflicting bundle crashes offer-parsing / RPC processing - ([File: chia/wallet/trading/offer.py])

### Summary
### Finding Description
`Offer.__post_init__` in [1](#0-0)  iterates the underlying `SpendBundle`'s coin spends and asserts uniqueness with a bare `assert cs.coin not in adds`, annotated "you can't spend the same coin twice in the same SpendBundle" [2](#0-1) . This runs unconditionally when an `Offer` object is constructed from attacker-influenced data (e.g. an offer file/bech32 blob received from a trade counterparty), well before any of the deliberate, catchable `ValidationError`/`ValueError` paths that the rest of `__post_init__` uses for cost-exceeded and other malformed-input cases [3](#0-2) .

This is directly analogous to the MongoDB bug class described in the report: a user-facing entry point (there, an aggregation request; here, offer ingestion/RPC) that is expected to reject malformed input gracefully instead trips a raw internal invariant/assert, which is not designed to be a recoverable error path and can propagate as an unhandled exception.

### Impact Explanation
If a counterparty crafts a `SpendBundle` inside an offer where the same coin (same parent, puzzle hash, and amount → same `Coin.name()`) appears twice among `coin_spends`, `Offer.__post_init__` raises a raw `AssertionError` instead of the structured `ValidationError` used elsewhere in the same method. Any caller (e.g. wallet RPC endpoints that construct `Offer` from a submitted/received offer string, or `TradeManager`/`respond_to_offer` flows) that expects `ValidationError`/`ValueError` for malformed offers but does not separately catch `AssertionError` will have that exception escape as an unhandled error. Depending on how the surrounding RPC/task layer handles unexpected exceptions, this can manifest as a request failure, unhandled task exception, or process-level disruption — a spend-triggered processing halt reachable purely from untrusted, attacker-supplied offer data, consistent with the "Medium" severity DoS class in the report.

### Likelihood Explanation
Likelihood is moderate-to-high for the local trigger condition: constructing such a bundle requires no signature validity check inside `__post_init__` at that point (the assert fires before/independently of full CLVM/signature validation), so an attacker only needs to hand-craft two identical `CoinSpend` entries referencing the same coin id in the offer's `_bundle`. The main uncertainty (see below) is whether every call path that ingests untrusted offers wraps `Offer(...)` construction (or the deeper `from_bech32`/`from_bytes` parsing) in a broad `except Exception` that would already normalize this into a handled error — I could not fully verify all such wrapping call sites (`wallet_rpc_api.py`, `trade_manager.py`, `data_layer_wallet.py`) before running out of tool budget.

### Recommendation
Replace the bare `assert cs.coin not in adds` in `Offer.__post_init__` with an explicit check that raises `ValidationError` (or `ValueError`) with a proper `Err` code, matching the pattern already used a few lines below for cost-exceeded conditions, so duplicate-coin-spend offers are rejected through the same structured error path rather than via an interpreter-level assertion.

### Proof of Concept
1. Construct two `CoinSpend` objects for the identical `Coin` (same `parent_coin_info`, `puzzle_hash`, `amount`), i.e. `cs.coin.name()` collides.
2. Build a `WalletSpendBundle`/`SpendBundle` containing both spends and wrap it in an `Offer(requested_payments, bundle, driver_dict)`.
3. Observe that construction raises `AssertionError` at [4](#0-3)  rather than a `ValidationError`, and trace whether the specific ingestion path (offer-file parsing, `take_offer` RPC, etc.) actually catches `AssertionError` — if not, the exception propagates unhandled.

**Caveat / uncertainty:** I was unable to fully trace, within the available tool budget, whether all offer-ingestion call sites (`chia/wallet/wallet_rpc_api.py`, `chia/wallet/trade_manager.py`, `chia/data_layer/data_layer_wallet.py`) already wrap `Offer` construction in a catch-all exception handler that would downgrade this to a benign, already-handled error response. This should be verified with a full Devin session before treating this as a confirmed exploitable DoS versus a defensive-coding/code-quality issue.

### Citations

**File:** chia/wallet/trading/offer.py (L150-184)
```python
    def __post_init__(self) -> None:
        # Verify that there are no duplicate payments
        for payments in self.requested_payments.values():
            payment_programs: list[bytes32] = [p.name() for p in payments]
            if len(set(payment_programs)) != len(payment_programs):
                raise ValueError("Bundle has duplicate requested payments")

        # Verify we have a type for every kind of asset
        for asset_id in self.requested_payments:
            if asset_id is not None and asset_id not in self.driver_dict:
                raise ValueError("Offer does not have enough driver information about the requested payments")

        # populate the _additions cache
        adds: dict[Coin, list[Coin]] = {}
        hints: dict[bytes32, bytes32] = {}
        max_cost = int(DEFAULT_CONSTANTS.MAX_BLOCK_COST_CLVM)
        for cs in self._bundle.coin_spends:
            # you can't spend the same coin twice in the same SpendBundle
            assert cs.coin not in adds
            try:
                hinted_coins, cost = compute_spend_hints_and_additions(cs, max_cost=max_cost)
                max_cost -= cost
                adds[cs.coin] = [hc.coin for hc in hinted_coins.values()]
                hints = {**hints, **{id: hc.hint for id, hc in hinted_coins.items() if hc.hint is not None}}
            except ValidationError:
                raise
            except ValueError as e:
                if e.args and e.args[0] == "cost exceeded or below zero":
                    raise ValidationError(Err.BLOCK_COST_EXCEEDS_MAX, "compute_additions for CoinSpend") from e
                continue
            except Exception:
                continue
            if max_cost < 0:
                raise ValidationError(Err.BLOCK_COST_EXCEEDS_MAX, "compute_additions for CoinSpend")
        object.__setattr__(self, "_additions", adds)
```
