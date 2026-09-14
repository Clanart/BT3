### Title
Unbounded CLVM execution of untrusted offer puzzles enables wallet-side DoS when parsing a counterparty's offer — (File: `chia/wallet/trading/offer.py`)

### Summary
`Offer._get_offered_coins()` runs the inner puzzle of every coin spend in an untrusted, counterparty-supplied `SpendBundle` with `cost_left = INFINITE_COST`, i.e. with no real CLVM cost ceiling, when a wallet inspects an offer (`get_offered_coins`/`get_offered_amounts`/`summary`/`get_pending_amounts`/`get_primary_coins`). This is the closest in-scope analog to the reported "return bomb" bug class: instead of an unbounded returned byte array causing out-of-gas, a malicious offer author can craft a puzzle whose CLVM execution cost/memory is effectively unbounded from this call's point of view, causing the receiving wallet to hang or exhaust memory/CPU while merely trying to *display* or *evaluate* an offer it received — before the wallet ever decides to accept it.

### Finding Description
`Offer.__post_init__` bounds total additions computation across all coin spends to `DEFAULT_CONSTANTS.MAX_BLOCK_COST_CLVM` via `compute_spend_hints_and_additions` [1](#0-0) . However, the separate helper `_get_offered_coins`, which is used purely to figure out which coins are "offered" for UI/summary purposes, initializes its own local budget as `INFINITE_COST` and never enforces a real limit against it:

```python
cost_left = INFINITE_COST
for parent_spend in self._bundle.coin_spends:
    ...
    puzzle_cost, conditions = inner_puzzle.run_with_cost(cost_left, inner_solution)
    assert cost_left >= puzzle_cost
    cost_left -= puzzle_cost
``` [2](#0-1) 

Because `cost_left` starts at `INFINITE_COST`, the `assert cost_left >= puzzle_cost` check can never trigger for any realistic puzzle, so this loop places no actual bound on how much CLVM work (time/memory) is spent evaluating the untrusted inner puzzle taken from a coin spend supplied by the offer's counterparty. This function is reached via `get_offered_coins()` [3](#0-2) , which is in turn used by `get_offered_amounts()`, `summary()` (the UI JSON summary method), `get_pending_amounts()`, and `get_primary_coins()` [4](#0-3)  — all routine operations a wallet performs on any offer it is shown or asked to evaluate, including offers received from an untrusted counterparty before the user decides to accept them.

This mirrors the report's core defect class: code that runs/consumes attacker-controlled execution output without a hard resource bound, exposing the caller (here, the local wallet process) to a denial of service. In the Solidity report, the untrusted quantity was the returned bytes array from an external call; here it is the CLVM execution cost of an attacker-crafted inner puzzle run with no enforced ceiling.

### Impact Explanation
A wallet user who receives (via file, RPC, or DataLayer/offer exchange UI) a maliciously crafted offer containing a coin spend whose inner puzzle is computationally expensive (e.g., deeply recursive CLVM operations) can cause the wallet process evaluating that offer (to compute `summary()`, `get_offered_amounts()`, etc., which most wallet UIs/RPCs call automatically when displaying an offer) to consume excessive CPU/memory, potentially hanging or crashing the wallet process before the user ever accepts or rejects the offer. This is a local, spend-triggered processing halt reachable purely by handing an untrusted offer to the wallet — it does not require the offer to be broadcast, accepted, or confirmed on-chain.

### Likelihood Explanation
Medium. Any wallet user or offer counterparty can construct an offer with an inner puzzle designed to consume excessive CLVM cost; the wallet's `__post_init__` already computes additions under an explicit `MAX_BLOCK_COST_CLVM` budget for the whole bundle, but `_get_offered_coins` performs an entirely separate, unmetered puzzle run. This makes it plausible that a puzzle can be constructed to pass the cheaper `compute_spend_hints_and_additions` accounting in `__post_init__` while still being expensive to run through `inner_puzzle.run_with_cost(INFINITE_COST, inner_solution)`, especially since the two code paths run different programs (the raw puzzle reveal vs. the extracted "inner puzzle" via `get_inner_puzzle`).

### Recommendation
Replace `cost_left = INFINITE_COST` in `Offer._get_offered_coins` with a real, finite budget (e.g., `DEFAULT_CONSTANTS.MAX_BLOCK_COST_CLVM`, mirroring `__post_init__`), and raise/handle a `ValidationError` (as `__post_init__` already does) when the budget is exceeded rather than relying on an assertion that can never fail against an effectively infinite budget. This ensures wallet-side inspection of an untrusted offer cannot be turned into an unbounded computation.

### Proof of Concept
Not independently executed against a live wallet in this review; conceptually: construct a `SpendBundle`/`Offer` whose coin spend's inner puzzle (as resolved by `get_inner_puzzle`) contains an intentionally expensive CLVM program (e.g., large loop/recursion producing high operation count) attached to a `NotarizedPayment`-style settlement coin. When a wallet calls `Offer.summary()` or `get_offered_amounts()` on this offer (as most offer-viewing RPC/CLI/GUI flows do), `_get_offered_coins` will execute `inner_puzzle.run_with_cost(INFINITE_COST, inner_solution)` [5](#0-4)  with no effective cost ceiling, tying up the wallet process for an attacker-controlled amount of CPU/memory.

### Citations

**File:** chia/wallet/trading/offer.py (L163-186)
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
        object.__setattr__(self, "_hints", hints)
        object.__setattr__(self, "_conditions", None)
```

**File:** chia/wallet/trading/offer.py (L244-266)
```python
    def _get_offered_coins(self) -> dict[bytes32 | None, list[Coin]]:
        offered_coins: dict[bytes32 | None, list[Coin]] = {}

        cost_left = INFINITE_COST
        for parent_spend in self._bundle.coin_spends:
            coins_for_this_spend: list[Coin] = []

            parent_puzzle: UnknownPuzzle = UnknownPuzzle(known_program=parent_spend.puzzle_reveal)
            parent_solution = Program.from_serialized(parent_spend.solution)
            additions: list[Coin] = self._additions[parent_spend.coin]

            puzzle_driver = match_puzzle(parent_puzzle)
            if puzzle_driver is not None:
                asset_id = create_asset_id(puzzle_driver)
                inner_puzzle: Program | None = get_inner_puzzle(puzzle_driver, parent_puzzle, parent_solution)
                inner_solution: Program | None = get_inner_solution(puzzle_driver, parent_solution)
                assert inner_puzzle is not None and inner_solution is not None

                # We're going to look at the conditions created by the inner puzzle
                puzzle_cost, conditions = inner_puzzle.run_with_cost(cost_left, inner_solution)
                assert cost_left >= puzzle_cost
                cost_left -= puzzle_cost
                expected_num_matches: int = 0
```

**File:** chia/wallet/trading/offer.py (L305-311)
```python
    def get_offered_coins(self) -> dict[bytes32 | None, list[Coin]]:
        try:
            if self._offered_coins is not None:
                return self._offered_coins
        except AttributeError:
            object.__setattr__(self, "_offered_coins", self._get_offered_coins())
        return self._offered_coins
```

**File:** chia/wallet/trading/offer.py (L313-420)
```python
    def get_offered_amounts(self) -> dict[bytes32 | None, int]:
        offered_coins: dict[bytes32 | None, list[Coin]] = self.get_offered_coins()
        offered_amounts: dict[bytes32 | None, int] = {}
        for asset_id, coins in offered_coins.items():
            offered_amounts[asset_id] = uint64(sum(c.amount for c in coins))
        return offered_amounts

    def get_requested_payments(self) -> dict[bytes32 | None, list[NotarizedPayment]]:
        return self.requested_payments

    def get_requested_amounts(self) -> dict[bytes32 | None, int]:
        requested_amounts: dict[bytes32 | None, int] = {}
        for asset_id, coins in self.get_requested_payments().items():
            requested_amounts[asset_id] = uint64(sum(c.amount for c in coins))
        return requested_amounts

    def arbitrage(self) -> dict[bytes32 | None, int]:
        """
        Returns a dictionary of the type of each asset and amount that is involved in the trade
        With the amount being how much their offered amount within the offer
        exceeds/falls short of their requested amount.
        """
        offered_amounts: dict[bytes32 | None, int] = self.get_offered_amounts()
        requested_amounts: dict[bytes32 | None, int] = self.get_requested_amounts()

        arbitrage_dict: dict[bytes32 | None, int] = {}
        for asset_id in [*requested_amounts.keys(), *offered_amounts.keys()]:
            arbitrage_dict[asset_id] = offered_amounts.get(asset_id, 0) - requested_amounts.get(asset_id, 0)

        return arbitrage_dict

    # This is a method mostly for the UI that creates a JSON summary of the offer
    def summary(self) -> tuple[dict[str, str], dict[str, str], dict[str, PuzzleInfo], ConditionValidTimes]:
        offered_amounts: dict[bytes32 | None, int] = self.get_offered_amounts()
        requested_amounts: dict[bytes32 | None, int] = self.get_requested_amounts()

        def keys_and_amounts_to_strings(dic: dict[bytes32 | None, int]) -> dict[str, str]:
            new_dic: dict[str, str] = {}
            for key, val in dic.items():
                if key is None:
                    new_dic["xch"] = str(val)
                else:
                    new_dic[key.hex()] = str(val)
            return new_dic

        driver_dict: dict[str, PuzzleInfo] = {}
        for key, value in self.driver_dict.items():
            driver_dict[key.hex()] = value

        return (
            keys_and_amounts_to_strings(offered_amounts),
            keys_and_amounts_to_strings(requested_amounts),
            driver_dict,
            self.absolute_valid_times_ban_relatives(),
        )

    # Also mostly for the UI, returns a dictionary of assets and how much of them is pended for this offer
    # This method is also imperfect for sufficiently complex spends
    def get_pending_amounts(self) -> dict[str, int]:
        all_additions: list[Coin] = self.additions()
        all_removals: list[Coin] = self.removals()
        non_ephemeral_removals: list[Coin] = list(filter(lambda c: c not in all_additions, all_removals))

        pending_dict: dict[str, int] = {}
        # First we add up the amounts of all coins that share an ancestor with the offered coins (i.e. a primary coin)
        for asset_id, coins in self.get_offered_coins().items():
            name = "xch" if asset_id is None else asset_id.hex()
            pending_dict[name] = 0
            for coin in coins:
                root_removal: Coin = self.get_root_removal(coin)

                for addition in filter(lambda c: c.parent_coin_info == root_removal.name(), all_additions):
                    pending_dict[name] += addition.amount

        # Then we gather anything else as unknown
        sum_of_additions_so_far: int = sum(pending_dict.values())
        unknown: int = sum(c.amount for c in non_ephemeral_removals) - sum_of_additions_so_far
        if unknown > 0:
            pending_dict["unknown"] = unknown

        return pending_dict

    # This method returns all of the coins that are being used in the offer (without which it would be invalid)
    def get_involved_coins(self) -> list[Coin]:
        additions = self.additions()
        return list(filter(lambda c: c not in additions, self.removals()))

    # This returns the non-ephemeral removal that is an ancestor of the specified coin
    # This should maybe move to the SpendBundle object at some point
    def get_root_removal(self, coin: Coin) -> Coin:
        all_removals: set[Coin] = set(self.removals())
        all_removal_ids: set[bytes32] = {c.name() for c in all_removals}
        non_ephemeral_removals: set[Coin] = {
            c for c in all_removals if c.parent_coin_info not in {r.name() for r in all_removals}
        }
        if coin.name() not in all_removal_ids and coin.parent_coin_info not in all_removal_ids:
            raise ValueError("The specified coin is not a coin in this bundle")

        while coin not in non_ephemeral_removals:
            coin = next(c for c in all_removals if c.name() == coin.parent_coin_info)

        return coin

    # This will only return coins that are ancestors of settlement payments
    def get_primary_coins(self) -> list[Coin]:
        primary_coins: set[Coin] = set()
        for _, coins in self.get_offered_coins().items():
            for coin in coins:
```
