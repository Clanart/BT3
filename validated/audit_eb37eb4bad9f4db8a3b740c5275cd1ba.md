### Title
Broad `except Exception` in `Offer.__post_init__` silently drops CoinSpend additions, corrupting `_additions` cache used for offer accounting - (File: chia/wallet/trading/offer.py)

### Summary
`Offer.__post_init__` computes the `_additions` cache by calling `compute_spend_hints_and_additions()` for every `CoinSpend` in the bundle, but wraps the call in a broad `except Exception: continue` (in addition to a specific `ValueError` check for cost-exceeded). Any non-cost exception raised while computing hints/additions for a given coin spend causes that coin's entry to be silently omitted from `_additions`, rather than the offer being rejected as invalid.

### Finding Description
In `chia/wallet/trading/offer.py`, `__post_init__` populates `self._additions`: [1](#0-0) 

```python
for cs in self._bundle.coin_spends:
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
```

This is the same class of bug flagged in the external report: an unconditional catch of a wide error surface (all `Exception` subclasses, and even non-cost `ValueError`s) that is only intended to filter one specific failure mode (cost exhaustion). `compute_spend_hints_and_additions()` runs `run_with_cost(cs.puzzle_reveal, max_cost, cs.solution)` and does CLVM-level parsing (`condition.at("rf")`, `.at("rrf")`, etc., with an `assert rf is not None`) which can throw various exceptions (e.g. `AssertionError`, CLVM `EvalError`, type errors from malformed conditions) that have nothing to do with cost: [2](#0-1) 

When such an exception occurs, the coin's entry is never added to `adds`/`self._additions`, silently under-representing the true additions of the spend bundle rather than causing the offer to be rejected.

A near-identical pattern exists in `Offer.conditions()`, which also uses `except Exception: continue`: [3](#0-2) 

### Impact Explanation
`self._additions` backs several safety-relevant offer accounting paths used both by the wallet UI and (more importantly) by trade/offer processing logic:
- `additions()` returns the flattened contents of `self._additions.values()`, so a dropped entry silently reduces the total additions the wallet believes the bundle produces. [4](#0-3) 
- `fees()` is computed as `amount_in - amount_out` using `additions()`/`removals()`, so an undercount of additions inflates the apparent fee. [5](#0-4) 
- `_get_offered_coins()` directly indexes `self._additions[parent_spend.coin]` for every coin spend in the bundle: [6](#0-5) 
If a coin's additions failed to compute, this dict lookup raises an unguarded `KeyError`, crashing `get_offered_coins()`, `get_offered_amounts()`, `arbitrage()`, `summary()`, and `get_pending_amounts()` — all used when a wallet parses/evaluates an incoming or outgoing offer. This turns a single crafted `CoinSpend` inside an otherwise-signed offer into a denial-of-service against offer inspection/acceptance flows (the offer can never be summarized or accepted by an unprivileged wallet processing it), and more subtly, if the failure path is later hit only in some accounting call and not others, `arbitrage()`/`fees()` results can diverge from the offer's real economic content, undermining the trust a counterparty places in wallet-reported offer terms before accepting/signing.

### Likelihood Explanation
Likelihood is low-to-moderate. It requires a `CoinSpend`, embedded inside an `Offer`, whose puzzle+solution execution triggers a non-cost exception during `compute_spend_hints_and_additions` (e.g. malformed/nonstandard `CREATE_COIN` condition structure hitting the `assert rf is not None`, or an unusual puzzle causing a CLVM evaluation exception unrelated to cost). Any counterparty constructing an offer file controls the puzzle reveals/solutions of the coins they contribute, so they can deliberately craft such a spend. This mirrors the disputed-but-ultimately-accepted C4 finding's core claim: broad exception/empty-error catching intended for one failure mode (OOG/cost) ends up masking unrelated failures, with realistic reachability by an unprivileged party (here, an offer-file author) rather than requiring esoteric conditions.

### Recommendation
Do not use a blanket `except Exception: continue` (nor a fallback `except ValueError: continue`) to filter out only cost-related failures. Catch only the specific, well-defined error condition (cost exhaustion) and let all other exceptions propagate as a hard failure/`ValidationError`, so a malformed or adversarial `CoinSpend` causes the offer to be rejected outright rather than silently truncating `_additions`. Additionally, guard `_get_offered_coins()`'s `self._additions[parent_spend.coin]` lookup (or ensure `_additions` always has an entry, even if empty, for every coin spend) so a missing key cannot raise an unguarded `KeyError`.

### Proof of Concept
1. Construct a `CoinSpend` whose puzzle reveal, when run with the coin's solution, emits a `CREATE_COIN` condition where the second CLVM argument (the puzzle-hash slot, `rf`) is a cons pair (structured data) rather than an atom.
2. `compute_spend_hints_and_additions()` executes `rf = condition.at("rf").atom`, which evaluates to `None` for a non-atom node, and the subsequent `assert rf is not None` raises `AssertionError`.
3. Include this `CoinSpend` in an `Offer`'s `WalletSpendBundle`.
4. `Offer.__post_init__` catches the `AssertionError` via the bare `except Exception: continue`, and `adds[cs.coin]` is never populated for that coin.
5. Any subsequent call to `offer.get_offered_coins()`, `offer.summary()`, or `offer.arbitrage()` triggers `self._additions[parent_spend.coin]` for that coin and raises an unguarded `KeyError`, crashing offer evaluation instead of properly rejecting the malformed offer with a `ValidationError`.

### Citations

**File:** chia/wallet/trading/offer.py (L166-184)
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
        object.__setattr__(self, "_additions", adds)
```

**File:** chia/wallet/trading/offer.py (L192-200)
```python
            for cs in self._bundle.coin_spends:
                try:
                    cost, conds = run_with_cost(cs.puzzle_reveal, max_cost, cs.solution)
                    max_cost -= cost
                    conditions[cs.coin] = parse_conditions_non_consensus(conds.as_iter())
                except Exception:  # pragma: no cover
                    continue
                if max_cost < 0:  # pragma: no cover
                    raise ValidationError(Err.BLOCK_COST_EXCEEDS_MAX, "computing conditions for CoinSpend")
```

**File:** chia/wallet/trading/offer.py (L224-225)
```python
    def additions(self) -> list[Coin]:
        return [c for additions in self._additions.values() for c in additions]
```

**File:** chia/wallet/trading/offer.py (L230-234)
```python
    def fees(self) -> int:
        """Unsafe to use for fees validation!!!"""
        amount_in = sum(_.amount for _ in self.removals())
        amount_out = sum(_.amount for _ in self.additions())
        return int(amount_in - amount_out)
```

**File:** chia/wallet/trading/offer.py (L244-253)
```python
    def _get_offered_coins(self) -> dict[bytes32 | None, list[Coin]]:
        offered_coins: dict[bytes32 | None, list[Coin]] = {}

        cost_left = INFINITE_COST
        for parent_spend in self._bundle.coin_spends:
            coins_for_this_spend: list[Coin] = []

            parent_puzzle: UnknownPuzzle = UnknownPuzzle(known_program=parent_spend.puzzle_reveal)
            parent_solution = Program.from_serialized(parent_spend.solution)
            additions: list[Coin] = self._additions[parent_spend.coin]
```

**File:** chia/wallet/util/compute_hints.py (L28-55)
```python
    cost, result_program = run_with_cost(cs.puzzle_reveal, max_cost, cs.solution)

    hinted_coins: dict[bytes32, HintedCoin] = {}
    for condition in result_program.as_iter():
        if cost > max_cost:
            raise ValidationError(Err.BLOCK_COST_EXCEEDS_MAX, "compute_spend_hints_and_additions() for CoinSpend")
        atoms = condition.as_iter()
        op = next(atoms).atom
        if op in {
            ConditionOpcode.AGG_SIG_PARENT,
            ConditionOpcode.AGG_SIG_PUZZLE,
            ConditionOpcode.AGG_SIG_AMOUNT,
            ConditionOpcode.AGG_SIG_PUZZLE_AMOUNT,
            ConditionOpcode.AGG_SIG_PARENT_AMOUNT,
            ConditionOpcode.AGG_SIG_PARENT_PUZZLE,
            ConditionOpcode.AGG_SIG_UNSAFE,
            ConditionOpcode.AGG_SIG_ME,
        }:
            cost += ConditionCost.AGG_SIG.value
            continue
        if op != ConditionOpcode.CREATE_COIN.value:
            continue
        cost += ConditionCost.CREATE_COIN.value

        rf = condition.at("rf").atom
        assert rf is not None

        coin: Coin = Coin(cs.coin.name(), bytes32(rf), uint64(condition.at("rrf").as_int()))
```
