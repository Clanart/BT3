### Title
Unauthenticated AssertionError on malformed `CREATE_COIN` puzzle-hash argument crashes wallet spend/offer processing - (File: `chia/wallet/util/compute_hints.py`)

### Summary
`compute_spend_hints_and_additions()` blindly asserts that the puzzle-hash argument of a `CREATE_COIN` condition is an atom, without validating that assumption first. A coin spend whose puzzle reveal outputs a `CREATE_COIN` condition with a non-atom (cons/list) puzzle-hash argument causes an uncaught `AssertionError` (or, if run with `python -O`, a `TypeError` from `bytes32(None)`), analogous to how GStreamer's `subrip_unescape_formatting` dereferenced a NULL pointer on malformed subtitle input (ALPINE-CVE-2025-47807). Several wallet code paths call this function on attacker-influenced data (offers, spend bundles) without a surrounding try/except.

### Finding Description
`chia/wallet/util/compute_hints.py:52-53`:
```python
rf = condition.at("rf").atom
assert rf is not None
```
`condition.at("rf")` returns the puzzle-hash argument (2nd element) of a `CREATE_COIN` condition emitted by running an arbitrary puzzle/solution pair. `.atom` is `None` whenever that value is a cons-pair (a list) instead of an atom — which is trivial to produce from CLVM (e.g. `(CREATE_COIN (a . b) amount)`). The code assumes this can never happen and enforces it with a bare `assert`, which:
- raises `AssertionError` in normal (non-optimized) execution, or
- is silently skipped under `-O`, in which case the following line, `bytes32(rf)` with `rf is None`, raises `TypeError`.

Either way, an untrusted, attacker-controlled `CoinSpend` (its puzzle reveal/solution, fully controlled by whoever creates the spend) can force an unhandled exception inside a function meant to process arbitrary spend bundles. [1](#0-0) 

This is the same code (`condition.at("rf").atom`) that in the sibling function `compute_additions_with_cost` (`chia/wallet/util/compute_additions.py`) is called without the assert-and-continue guard used elsewhere, i.e. the assumption "arg 2 of CREATE_COIN is always an atom" recurs across parsers but is not defensively checked.

Critically, this function is invoked directly, with no exception handling, inside `TradeManager.calculate_tx_records_for_offer`:
```python
for hinted_coins, _ in (
    compute_spend_hints_and_additions(spend) for spend in final_spend_bundle.coin_spends
):
``` [2](#0-1) 

`calculate_tx_records_for_offer(offer, True)` is called from `TradeManager.respond_to_offer`, which is the code path exercised whenever a wallet accepts (takes) an offer from a counterparty:
```python
tx_records: list[TransactionRecord] = await self.calculate_tx_records_for_offer(complete_offer, True)
``` [3](#0-2) 

`final_spend_bundle` here is `complete_offer.to_valid_spend(...)`, i.e. the aggregate of the maker's offer bundle and the taker's own spends — the maker (an untrusted offer counterparty) fully controls the puzzle reveal/solution of their coin spends included in this bundle. By crafting an offer whose settlement/coin-spend puzzle emits a malformed `CREATE_COIN` condition (non-atom puzzle-hash), a malicious offer maker can make any user who tries to accept ("take") that offer suffer an unhandled exception inside `respond_to_offer`, aborting the offer-acceptance transaction flow.

By contrast, `Offer.__post_init__` (`chia/wallet/trading/offer.py:169-181`) wraps the same call in a broad `except Exception: continue`, showing the code author was aware this call can raise unexpected exceptions on attacker data — but that defensive wrapper was not applied to the `calculate_tx_records_for_offer` call site. [4](#0-3) 

### Impact Explanation
This is a spend/offer-triggered transaction-processing halt: any wallet user who attempts to accept a maliciously crafted offer can have that offer-acceptance operation crash with an unhandled exception. Depending on the caller (RPC endpoint wrapper, CLI, GUI), this can surface as a hard failure of the `respond_to_offer`/take-offer RPC call, disrupting the wallet's trade-processing flow for that offer. It does not directly enable coin theft or supply inflation, but it satisfies the "spend-triggered transaction-processing halt" acceptance criterion: an untrusted, remotely-suppliable offer (or any spend bundle routed through this code path) can deny normal processing.

### Likelihood Explanation
High likelihood of triggering: constructing a CLVM puzzle that returns `(CREATE_COIN (a . b) amount ...)` (puzzle-hash as a cons instead of an atom) is trivial and requires no special privileges — it only requires publishing/sharing a crafted offer file, which is a completely standard, unprivileged offer-counterparty action. No signature bypass or consensus-level acceptance is needed; the crash happens purely during local wallet-side parsing of the untrusted spend before validation/signing.

### Recommendation
In `compute_spend_hints_and_additions` (and the analogous logic in `compute_additions_with_cost`), replace the bare `assert rf is not None` with an explicit check that raises a caught/expected exception type (e.g. `ValidationError`) or otherwise skip/ignore conditions whose puzzle-hash argument is not a well-formed atom, mirroring the defensive handling already used for the hint field. Additionally, wrap the `compute_spend_hints_and_additions` call in `TradeManager.calculate_tx_records_for_offer` in a try/except (as is already done in `Offer.__post_init__`) so that malformed attacker-supplied spends in an offer degrade gracefully instead of raising an unhandled exception.

### Proof of Concept
1. Construct a CLVM puzzle `P` whose solution, when run, returns conditions including one condition list `(CREATE_COIN X amount)` where `X` is a cons pair (e.g., `(1 . 2)`) instead of a 32-byte atom.
2. Build an `Offer` (via `Offer.notarize_payments` / bech32 offer file) whose maker-side settlement/coin spend uses puzzle `P`, so that `Offer._bundle.coin_spends` includes this crafted `CoinSpend`.
3. Have a victim wallet call `TradeManager.respond_to_offer(offer, ...)` to accept/take the offer.
4. Inside `respond_to_offer` → `calculate_tx_records_for_offer(complete_offer, True)`, `compute_spend_hints_and_additions(spend)` is invoked on the crafted spend without exception handling; `condition.at("rf").atom` returns `None`, the `assert rf is not None` fails, raising an unhandled `AssertionError` that propagates out of `respond_to_offer`, aborting the take-offer operation.

Note: I was not able to trace every possible downstream caller of `respond_to_offer` (e.g., exact RPC error-handling wrappers) within the indexed portion of the codebase, so the precise user-visible failure mode (RPC 500 vs. GUI error dialog) could not be fully confirmed; this should be verified in a live/full Devin session if further certainty is required.

### Citations

**File:** chia/wallet/util/compute_hints.py (L48-64)
```python
        if op != ConditionOpcode.CREATE_COIN.value:
            continue
        cost += ConditionCost.CREATE_COIN.value

        rf = condition.at("rf").atom
        assert rf is not None

        coin: Coin = Coin(cs.coin.name(), bytes32(rf), uint64(condition.at("rrf").as_int()))
        hint: bytes32 | None = None
        if (
            condition.at("rrr") != Program.NIL  # There's more than two arguments
            and condition.at("rrrf").atom is None  # The 3rd argument is a cons
        ):
            potential_hint: bytes | None = condition.at("rrrff").atom
            if potential_hint is not None and len(potential_hint) == 32:
                hint = bytes32(potential_hint)
        hinted_coins[bytes32(coin.name())] = HintedCoin(coin, hint)
```

**File:** chia/wallet/trade_manager.py (L692-702)
```python
    async def calculate_tx_records_for_offer(self, offer: Offer, validate: bool) -> list[TransactionRecord]:
        if validate:
            final_spend_bundle: WalletSpendBundle = offer.to_valid_spend()
            hint_dict: dict[bytes32, bytes32] = {}
            additions_dict: dict[bytes32, Coin] = {}
            for hinted_coins, _ in (
                compute_spend_hints_and_additions(spend) for spend in final_spend_bundle.coin_spends
            ):
                hint_dict.update({id: hc.hint for id, hc in hinted_coins.items() if hc.hint is not None})
                additions_dict.update({id: hc.coin for id, hc in hinted_coins.items()})
            all_additions: list[Coin] = list(a for a in additions_dict.values())
```

**File:** chia/wallet/trade_manager.py (L887-894)
```python
        self.log.info("COMPLETE OFFER: %s", complete_offer.to_bech32())
        assert complete_offer.is_valid()
        final_spend_bundle: WalletSpendBundle = complete_offer.to_valid_spend(
            solver=Solver({**valid_spend_solver.info, **solver.info})
        )
        await self.maybe_create_wallets_for_offer(complete_offer)

        tx_records: list[TransactionRecord] = await self.calculate_tx_records_for_offer(complete_offer, True)
```

**File:** chia/wallet/trading/offer.py (L166-183)
```python
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
```
