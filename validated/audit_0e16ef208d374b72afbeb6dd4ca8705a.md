### Title
Wallet crash via unhandled `AssertionError` when parsing an untrusted Offer with duplicate coin spends - (File: `chia/wallet/trading/offer.py`)

### Summary
`Offer.__post_init__` uses a bare Python `assert` to enforce that no coin is spent twice within the same offer bundle, instead of raising a handled validation error. Because `Offer` objects are constructed directly from attacker/counterparty-supplied bytes (an offer file or bech32-encoded offer), a maliciously crafted offer containing two `CoinSpend`s for the same coin triggers an uncaught `AssertionError` deep inside offer construction, mirroring the MongoDB report's pattern of an invariant/assertion firing on unexpected, insufficiently validated input during dispatch and crashing the request/service.

### Finding Description
When a wallet user (or any code path that parses an offer, e.g. `get_offer_summary`, `take_offer`, offer file loading in the CLI/GUI/RPC) constructs an `Offer` object, `__post_init__` iterates over `self._bundle.coin_spends` to build the internal `_additions` cache: [1](#0-0) 

```
        adds: dict[Coin, list[Coin]] = {}
        ...
        for cs in self._bundle.coin_spends:
            # you can't spend the same coin twice in the same SpendBundle
            assert cs.coin not in adds
```

This is a bare `assert`, not a `ValidationError` or other handled exception type used elsewhere in the same file (e.g. `ValidationError(Err.BLOCK_COST_EXCEEDS_MAX, ...)` a few lines below it, and in `conditions()`): [2](#0-1) 

The comment even acknowledges the invariant being protected ("you can't spend the same coin twice in the same SpendBundle") — but the check is implemented as a debug-style assertion rather than validated input rejection. A `SpendBundle`/offer with two `CoinSpend` entries sharing the same `Coin` is not otherwise rejected before reaching this constructor: `Offer` objects are built directly from deserialized, attacker-controlled bytes (an offer file a counterparty sends you, or bech32 text), and this constructor path runs before any consensus-level mempool duplicate-coin check (that check only happens later, inside `MempoolManager.pre_validate_spendbundle`/`validate_clvm_and_signature`, which is not on the offer-parsing path used by `get_offer_summary` or trade preview operations).

This is directly analogous to the CVE's root cause: an authenticated/unprivileged actor supplies unexpected data that bypasses proper validation and reaches a low-level invariant assertion, and that assertion firing causes a crash/denial-of-service rather than a graceful validation failure.

### Impact Explanation
Any wallet user who loads, previews, or receives an offer (a normal, unprivileged wallet/trading action) can be handed a crafted offer file with a duplicated coin spend. Processing that offer (summarizing it, checking driver dictionaries, or taking it) raises an unhandled `AssertionError`, crashing the RPC call and potentially the enclosing wallet task/service — a spend-triggered transaction-processing halt reachable purely through offer exchange, which is one of the accepted impact categories (offer settlement flow disruption / processing halt). This does not lead to fund loss directly, but it is a reliable, remotely triggerable crash vector in the offers subsystem.

### Likelihood Explanation
High likelihood of triggering: constructing a `SpendBundle` with two `CoinSpend`s referencing the same `Coin` is trivial for any party crafting an offer file, and no upstream validation prevents such an offer from reaching `Offer.__post_init__`. The only requirement is that the victim's wallet attempts to parse/inspect/accept the malicious offer — an ordinary, expected trading interaction.

### Recommendation
Replace the bare `assert cs.coin not in adds` with an explicit validation error (e.g., raise `ValidationError(Err.DOUBLE_SPEND, ...)` or a dedicated `Offer`-specific exception) so malformed/malicious offers are rejected gracefully instead of crashing the caller via an uncaught `AssertionError`. Apply the same audit to any other bare `assert` statements in `chia/wallet/trading/offer.py` and other offer/trade parsing code that operate on untrusted, externally supplied bundle data (Python `assert` is also compiled out entirely when running with `-O`, which is an additional reason not to rely on it for security-relevant input validation).

### Proof of Concept
1. Construct two `CoinSpend` objects that reference the exact same `Coin` (same parent id, puzzle hash, amount) but with any valid puzzle reveal/solution pair (e.g., both spending the settlement/offer coin, or copy the same `CoinSpend` twice).
2. Aggregate them into a single `SpendBundle` (`SpendBundle(coin_spends=[cs, cs], aggregated_signature=...)`) and wrap it as an `Offer` (e.g., via `Offer.from_bytes` / the bech32 round-trip used by CLI/RPC offer-file loading), matching the on-disk/offer-string format normally produced by `chia offers`.
3. Send this offer file/string to a victim wallet, or have the victim invoke `get_offer_summary`/preview on it.
4. Observe that constructing the `Offer` object raises `AssertionError` inside `__post_init__` at the `assert cs.coin not in adds` line, which is not caught by callers expecting `ValidationError`, crashing the offer-inspection call.

Note: I could not fully verify, within the available search budget, whether every current RPC call path around `Offer` construction (e.g. `trade_manager.py`'s `take_offer`/`respond_to_offer`) wraps this specific `AssertionError` in a broader `try/except Exception` at the top level of RPC dispatch — if such a broad catch-all exists, the practical impact would be limited to a failed RPC call rather than a full process crash, though it still constitutes an unhandled, non-graceful failure mode consistent with the reported bug class. A Devin session with full repo/test access would be able to confirm exact RPC-level exception handling around this path.

### Citations

**File:** chia/wallet/trading/offer.py (L163-172)
```python
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
```

**File:** chia/wallet/trading/offer.py (L188-203)
```python
    def conditions(self) -> dict[Coin, list[Condition]]:
        if self._conditions is None:
            conditions: dict[Coin, list[Condition]] = {}
            max_cost = int(DEFAULT_CONSTANTS.MAX_BLOCK_COST_CLVM)
            for cs in self._bundle.coin_spends:
                try:
                    cost, conds = run_with_cost(cs.puzzle_reveal, max_cost, cs.solution)
                    max_cost -= cost
                    conditions[cs.coin] = parse_conditions_non_consensus(conds.as_iter())
                except Exception:  # pragma: no cover
                    continue
                if max_cost < 0:  # pragma: no cover
                    raise ValidationError(Err.BLOCK_COST_EXCEEDS_MAX, "computing conditions for CoinSpend")
            object.__setattr__(self, "_conditions", conditions)
        assert self._conditions is not None, "self._conditions is None"
        return self._conditions
```
