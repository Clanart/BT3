## Analog Found

### Title
Unauthenticated `AssertionError` (`CHECK`-fail equivalent) crash via malformed Offer with duplicate coin in `SpendBundle` - ([File: chia/wallet/trading/offer.py])

### Summary
`Offer.__post_init__` in `chia/wallet/trading/offer.py` contains a raw Python `assert cs.coin not in adds` that is not wrapped in the surrounding `try/except` block. A counterparty who crafts an offer file/bytes containing a `SpendBundle` with two `CoinSpend`s that reference an identical `Coin` (same `parent_coin_info`, `puzzle_hash`, `amount`) causes this assertion to fail with an uncaught `AssertionError` the moment the offer is parsed/constructed by the recipient's wallet — mirroring the TensorFlow `CHECK`-fail DoS pattern where malformed/edge-case input reaches an unconditional assertion instead of a handled validation path.

### Finding Description
`Offer.__post_init__` builds the `_additions` cache by iterating `self._bundle.coin_spends`: [1](#0-0) 

Line 168, `assert cs.coin not in adds`, executes for every coin spend before the `try/except` block that wraps `compute_spend_hints_and_additions`. That `try/except` only catches `ValidationError`, `ValueError`, and generic `Exception` raised *inside* the `try`, not the `assert` that precedes it in the loop body. Because Python `assert` raises `AssertionError` (not a subclass of `Exception` caught anywhere here — it is a subclass of `Exception` technically, but it's raised outside the `try` scope entirely, so no handler in this function catches it), a duplicate `Coin` object anywhere in the bundle's `coin_spends` list causes an unhandled `AssertionError` to propagate out of `Offer.__init__`/`Offer.from_bytes` construction.

An attacker (offer counterparty) fully controls the raw `SpendBundle` embedded in an offer file, including the list of `CoinSpend`s and the `Coin` objects therein. There is no upstream validation preventing duplicate `Coin` entries in an *unvalidated* offer bundle before it reaches `Offer.__post_init__` — coin uniqueness/double-spend checks normally happen later during mempool/consensus validation (`chia/full_node/mempool_manager.py` `check_removals`), not during offer object construction, which happens first when a wallet inspects/loads/summarizes an incoming offer (`Offer(...)`/`Offer.from_bytes(...)`), well before any spend bundle is submitted to a full node.

### Impact Explanation
This is a spend-triggered/offer-triggered halt of the parsing routine: any code path that constructs an `Offer` from attacker-supplied bytes (e.g., inspecting an offer received from a counterparty, viewing an offer summary, or validating an offer prior to acceptance) raises an unhandled `AssertionError`. Depending on the caller, this can crash the RPC request handling for offer inspection/summary/acceptance flows, denying service to the wallet user attempting to view or act on that specific (attacker-crafted) offer file. This matches the CWE-617 (`Reachable Assertion`) class described in the source TensorFlow advisory: unvalidated attacker input reaches an assertion instead of a graceful error path, producing a denial of service.

### Likelihood Explanation
Likelihood is high for any user who opens/inspects a maliciously crafted offer file from an untrusted counterparty, since constructing the duplicate `Coin` (same parent id, puzzle hash, amount) inside a `SpendBundle` requires no cryptographic capability — it's just repeating a `CoinSpend` entry (or two entries with the same underlying coin's) fields — an offer file/bytes can be trivially handcrafted without needing a valid signature over the actual mempool submission path, because the crash happens during offer object construction/inspection, before signature or chain-state validation.

### Recommendation
Move the coin-uniqueness check inside the exception-handling scope, or replace `assert cs.coin not in adds` with an explicit conditional that raises `ValidationError` (the pattern already used elsewhere in `__post_init__`, e.g. `raise ValueError("Bundle has duplicate requested payments")` at line 155), so the duplicate-coin condition is converted into an ordinary caught error path rather than an unhandled `AssertionError`. More broadly, audit `chia/wallet/trading/offer.py` and other wallet-facing deserialization paths for other bare `assert` statements reachable with attacker-controlled offer/spend-bundle bytes.

### Proof of Concept
1. Construct a `Coin` object `c` (arbitrary `parent_coin_info`, `puzzle_hash`, `amount`).
2. Construct a `SpendBundle` containing two `CoinSpend` entries that both reference the identical `Coin` `c` (e.g. `CoinSpend(c, puzzle1, solution1)` and `CoinSpend(c, puzzle2, solution2)`), with an arbitrary/empty aggregated signature (no valid signature required to reach the crash, since this is purely offer-object construction, not mempool submission).
3. Wrap this bundle in the `Offer` structure fields (`requested_payments={}`, `driver_dict={}`, `bundle=<crafted SpendBundle>`) as done in tests such as: [2](#0-1) 
4. Call `Offer(requested_payments, spend_bundle, driver_dict)` (or serialize/deserialize via `Offer.from_bytes`) from the victim wallet's offer-inspection code path.
5. Observe `__post_init__`'s `assert cs.coin not in adds` at line 168 raises an uncaught `AssertionError` on the second iteration of the loop (since `cs.coin` for the second `CoinSpend` is already a key in `adds` from the first iteration), crashing the calling routine instead of yielding a handled `ValidationError`. [3](#0-2)

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

**File:** chia/_tests/wallet/test_util.py (L98-126)
```python
def test_offer_maps_clvm_cost_exceeded_value_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """After prior spends leave almost no CLVM budget, run_with_cost raises ValueError.

    Offer remaps that to ValidationError. Lower MAX_BLOCK_COST_CLVM so a cheap
    first spend can deplete the budget without needing huge CREATE_COIN lists.
    """
    from chia_rs import G2Element
    from chia_rs.sized_ints import uint64

    import chia.wallet.trading.offer as offer_mod
    from chia.wallet.trading.offer import Offer
    from chia.wallet.wallet_spend_bundle import WalletSpendBundle

    coin_generator = CoinGenerator()
    # Minimal first spend (~44 cost). Leave only 10 for the second so CLVM aborts
    # inside run_with_cost with ValueError("cost exceeded or below zero").
    first = make_spend(coin_generator.get().coin, Program.to(1), Program.to([]))
    _, first_cost = compute_spend_hints_and_additions(first)
    second = make_spend(
        coin_generator.get().coin,
        Program.to(1),
        Program.to([[51, bytes32.zeros, 1]]),
    )
    monkeypatch.setattr(
        offer_mod,
        "DEFAULT_CONSTANTS",
        DEFAULT_CONSTANTS.replace(MAX_BLOCK_COST_CLVM=uint64(first_cost + 10)),
    )
    with pytest.raises(ValidationError, match="compute_additions for CoinSpend"):
```
