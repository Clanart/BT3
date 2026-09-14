Based on my research, I found a plausible analog for the ImageMagick unbounded-loop CPU-exhaustion class in the offer-processing code, though I was unable to fully trace every call site before running out of tool calls (noted below).

### Title
Unbounded CLVM execution (`INFINITE_COST`) when computing offer cancellation coins allows a malicious offer counterparty to hang the wallet - (File: `chia/wallet/trading/offer.py`)

### Summary
`Offer.get_cancellation_coins()` re-executes every non-addition coin spend's puzzle reveal against its solution using `run_with_cost(spend.puzzle_reveal, INFINITE_COST, spend.solution)` [1](#0-0)  — with no CLVM cost ceiling at all, unlike the normal admission path which always bounds execution with `MAX_BLOCK_COST_CLVM` or `max_tx_clvm_cost` [2](#0-1) .

### Finding Description
The offer's `conditions()` accessor budgets execution against `DEFAULT_CONSTANTS.MAX_BLOCK_COST_CLVM` and decrements it per coin spend, raising `BLOCK_COST_EXCEEDS_MAX` if exceeded [2](#0-1) . In contrast, `get_cancellation_coins()`, used to determine the minimal set of coins that must be spent to invalidate an aggregated offer bundle, runs every coin spend's puzzle with `INFINITE_COST` [3](#0-2) . Because `Offer` objects are built via `Offer.aggregate()` which merges coin spends from multiple parties without re-validating puzzle cost [4](#0-3) , an offer counterparty can embed a coin spend whose `puzzle_reveal` contains a CLVM program that loops an extremely large (or effectively unbounded, e.g. very deep tail recursion) number of times. When the local wallet later calls `get_cancellation_coins()` (e.g., to cancel a pending trade), that puzzle is executed with no cost limit, and the call blocks the wallet's processing thread for as long as the CLVM interpreter needs to finish (or indefinitely for a sufficiently large loop), analogous to `ReadWPGImage`'s unbounded loop in the CVE causing CPU exhaustion from a crafted input file.

The surrounding `while True` cancellation-resolution loops in the same function also add unmetered CPU work on top of the unmetered puzzle execution [5](#0-4) , compounding the exposure once puzzle execution returns.

### Impact Explanation
If reachable, this is a spend-triggered processing halt confined to the local wallet handling a malicious/aggregated offer — the wallet's transaction-processing thread can be tied up indefinitely by attacker-supplied CLVM in a coin spend that is never cost-checked before this code path runs.

### Likelihood Explanation
Uncertain to Medium. I confirmed the unbounded `run_with_cost(..., INFINITE_COST, ...)` call and that `get_cancellation_coins()` iterates over `self._bundle.coin_spends`, which can include spends contributed by an offer counterparty via `Offer.aggregate()` [4](#0-3) . However, I was not able to fully verify, within the remaining tool budget, whether `get_cancellation_coins()` is invoked on attacker-influenced `Offer` data *before* any cost-bounded validation elsewhere in `chia/wallet/trade_manager.py` (a single reference to this function was found there but not resolved) [6](#0-5) . This should be confirmed by tracing the call site in `trade_manager.py` and the offer-acceptance/cancellation RPC flow before treating this as a confirmed finding.

### Recommendation
Bound the CLVM execution in `get_cancellation_coins()` with the same cost limit used elsewhere (e.g., `DEFAULT_CONSTANTS.MAX_BLOCK_COST_CLVM` or `max_tx_clvm_cost`) instead of `INFINITE_COST`, and raise a `ValidationError` (as `conditions()` already does) if the limit is exceeded, before performing any further per-coin-spend analysis.

### Proof of Concept
Not independently verified end-to-end due to tool-call limits. Conceptually: construct an `Offer` (via aggregation with a counterparty-supplied `CoinSpend`) whose `puzzle_reveal` is a CLVM program performing a large/deep self-recursive loop, then trigger a code path that calls `Offer.get_cancellation_coins()` on the aggregated bundle (e.g., attempting to cancel the trade) and observe unbounded CPU time in `run_with_cost(spend.puzzle_reveal, INFINITE_COST, spend.solution)` [1](#0-0) .

### Citations

**File:** chia/wallet/trading/offer.py (L188-200)
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
```

**File:** chia/wallet/trading/offer.py (L424-443)
```python
    # This returns the minimum coins that when spent will invalidate the rest of the bundle
    def get_cancellation_coins(self) -> list[Coin]:
        # First, we're going to gather:
        dependencies: dict[bytes32, list[bytes32]] = {}  # all of the hashes that each coin depends on
        announcements: dict[bytes32, list[bytes32]] = {}  # all of the hashes of the announcement that each coin makes
        coin_names: list[bytes32] = []  # The names of all the coins
        additions = self.additions()
        for spend in [cs for cs in self._bundle.coin_spends if cs.coin not in additions]:
            name = bytes32(spend.coin.name())
            coin_names.append(name)
            dependencies[name] = []
            announcements[name] = []
            conditions: Program = run_with_cost(spend.puzzle_reveal, INFINITE_COST, spend.solution)[1]
            for condition in conditions.as_iter():
                if condition.first() == 60:  # create coin announcement
                    announcements[name].append(
                        AssertCoinAnnouncement(asserted_id=name, asserted_msg=condition.at("rf").as_python()).msg_calc
                    )
                elif condition.first() == 61:  # assert coin announcement
                    dependencies[name].append(bytes32(condition.at("rf").as_python()))
```

**File:** chia/wallet/trading/offer.py (L445-469)
```python
        # We now enter a loop that is attempting to express the following logic:
        # "If I am depending on another coin in the same bundle, you may as well cancel that coin instead of me"
        # By the end of the loop, we should have filtered down the list of coin_names to include only those that will
        # cancel everything else
        while True:
            removed = detect_dependent_coin(coin_names, dependencies, announcements)
            if removed is None:
                break
            removed_coin, provider = removed
            removed_announcements: list[bytes32] = announcements[removed_coin]
            remove_these_keys: list[bytes32] = [removed_coin]
            while True:
                for coin, deps in dependencies.items():
                    if set(deps) & set(removed_announcements) and coin != provider:
                        remove_these_keys.append(coin)
                removed_announcements = []
                for coin in remove_these_keys:
                    dependencies.pop(coin)
                    removed_announcements.extend(announcements.pop(coin))
                coin_names = [n for n in coin_names if n not in remove_these_keys]
                if removed_announcements == []:
                    break
                else:
                    remove_these_keys = []

```

**File:** chia/wallet/trading/offer.py (L472-498)
```python
    @classmethod
    def aggregate(cls, offers: list[Offer]) -> Offer:
        total_requested_payments: dict[bytes32 | None, list[NotarizedPayment]] = {}
        total_bundle = WalletSpendBundle([], G2Element())
        total_driver_dict: dict[bytes32, PuzzleInfo] = {}
        for offer in offers:
            # First check for any overlap in inputs
            total_inputs: set[Coin] = {cs.coin for cs in total_bundle.coin_spends}
            offer_inputs: set[Coin] = {cs.coin for cs in offer._bundle.coin_spends}
            if total_inputs & offer_inputs:
                raise ValueError("The aggregated offers overlap inputs")

            # Next, do the aggregation
            for asset_id, payments in offer.requested_payments.items():
                if asset_id in total_requested_payments:
                    total_requested_payments[asset_id].extend(payments)
                else:
                    total_requested_payments[asset_id] = payments

            for key, value in offer.driver_dict.items():
                if key in total_driver_dict and total_driver_dict[key] != value:
                    raise ValueError(f"The offers to aggregate disagree on the drivers for {key.hex()}")

            total_bundle = WalletSpendBundle.aggregate([total_bundle, offer._bundle])
            total_driver_dict.update(offer.driver_dict)

        return cls(total_requested_payments, total_bundle, total_driver_dict)
```

**File:** chia/wallet/trade_manager.py (L1-30)
```python
from __future__ import annotations

import dataclasses
import logging
import time
from collections import deque
from typing import TYPE_CHECKING, Any, Literal

from chia_rs import CoinState
from chia_rs.sized_bytes import bytes32
from chia_rs.sized_ints import uint32, uint64

from chia.data_layer.data_layer_wallet import DataLayerSummary, DataLayerWallet
from chia.server.ws_connection import WSChiaConnection
from chia.types.blockchain_format.coin import Coin, coin_as_list
from chia.types.blockchain_format.program import Program, run
from chia.util.db_wrapper import DBWrapper2
from chia.util.hash import std_hash
from chia.wallet.cat_wallet.cat_wallet import CATWallet
from chia.wallet.conditions import (
    AssertCoinAnnouncement,
    Condition,
    ConditionValidTimes,
    CreateCoin,
    CreateCoinAnnouncement,
    parse_conditions_non_consensus,
    parse_timelock_info,
)
from chia.wallet.db_wallet.db_wallet_puzzles import ACS_MU_PH
from chia.wallet.estimate_fees import estimate_fees
```
