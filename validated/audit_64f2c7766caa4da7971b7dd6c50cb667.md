### Title
Wallet hangs indefinitely on a crafted offer with cyclic coin-parent references - (File: chia/wallet/trading/offer.py)

### Summary
`Offer.get_root_removal()` walks a coin's parentage back to a non-ephemeral removal by repeatedly looking up `parent_coin_info` among the bundle's own removals, with no cycle detection or iteration bound, analogous to the unterminated array-parsing loop in CVE-2018-5686 (MuPDF `pdf_parse_array` looping forever because it never checks for EOF).

### Finding Description
`get_root_removal()` iterates:
```
while coin not in non_ephemeral_removals:
    coin = next(c for c in all_removals if c.name() == coin.parent_coin_info)
``` [1](#0-0) 

Because an `Offer`/`SpendBundle` is untrusted, attacker-controlled data before it is validated by consensus, the `coin.parent_coin_info` field of each `CoinSpend.coin` in the bundle can be set to any bytes32 the offer creator likes — it does not have to correspond to a real coin lineage. An attacker can construct two (or more) `CoinSpend`s, A and B, both marked "ephemeral" relative to each other (i.e. each one's `parent_coin_info` equals the other's `name()`), forming a cycle that never resolves to a coin in `non_ephemeral_removals`. The `while` loop's `next(...)` generator expression will always find a match (the other coin in the cycle) and will loop forever, exactly mirroring the missing-EOF/termination-check bug class in the reported MuPDF CVE.

`get_root_removal()` is called from `get_primary_coins()` [2](#0-1) 
and `get_pending_amounts()`-style aggregation code that is exercised whenever wallet code inspects an offer (e.g., displaying/summarizing an offer, or preparing to accept/cancel it) via `TradeManager` [3](#0-2) 
. The related `get_cancellation_coins()` loop also lacks any bound and depends on the same coin/announcement graph being acyclic and finite [4](#0-3) 
.

### Impact Explanation
Any wallet user or RPC caller who loads/inspects a maliciously crafted offer file (a normal offer-counterparty interaction path — offers are shared as files/strings between untrusted parties) can trigger an unbounded, CPU-pegging infinite loop in the wallet process. This halts the wallet's ability to process the offer (and, depending on threading, may block other wallet/RPC processing), producing a spend-triggered denial of service consistent with the "spend-triggered transaction-processing halt" impact category.

### Likelihood Explanation
Constructing two `CoinSpend`s whose `Coin.parent_coin_info` values point at each other requires no cryptographic breaking — `parent_coin_info` is just an arbitrary bytes32 field chosen by whoever builds the unsigned offer bundle, and offers are commonly exchanged before full signing/broadcast. Any party who can hand another user an offer file (a normal, expected trust boundary in the offer/trade flow) can trigger this. Likelihood is high for anyone who processes untrusted offers.

### Recommendation
Add cycle/visited-set tracking (or a maximum iteration bound based on `len(all_removals)`) in `get_root_removal()`'s while loop, raising a `ValueError` (invalid offer) if no non-ephemeral ancestor is found within that bound. Apply the same defensive bound to the dependency-removal loops in `get_cancellation_coins()`.

### Proof of Concept
1. Construct an `Offer` whose `SpendBundle.coin_spends` contains two `CoinSpend` entries, `cs_a` and `cs_b`, where `cs_a.coin.parent_coin_info == cs_b.coin.name()` and `cs_b.coin.parent_coin_info == cs_a.coin.name()`, and neither coin appears in `self.additions()` (so both are treated as "removals" and neither is non-ephemeral).
2. Call `offer.get_primary_coins()` (or any wallet/RPC path that calls it, e.g. displaying/accepting the offer through `TradeManager`).
3. `get_root_removal()` enters the `while coin not in non_ephemeral_removals` loop; the `next()` lookup keeps oscillating between `cs_a.coin` and `cs_b.coin` forever, hanging the calling task/thread indefinitely.

### Citations

**File:** chia/wallet/trading/offer.py (L411-414)
```python
        while coin not in non_ephemeral_removals:
            coin = next(c for c in all_removals if c.name() == coin.parent_coin_info)

        return coin
```

**File:** chia/wallet/trading/offer.py (L416-422)
```python
    # This will only return coins that are ancestors of settlement payments
    def get_primary_coins(self) -> list[Coin]:
        primary_coins: set[Coin] = set()
        for _, coins in self.get_offered_coins().items():
            for coin in coins:
                primary_coins.add(self.get_root_removal(coin))
        return list(primary_coins)
```

**File:** chia/wallet/trading/offer.py (L425-470)
```python
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

        return [cs.coin for cs in self._bundle.coin_spends if cs.coin.name() in coin_names]
```

**File:** chia/wallet/trade_manager.py (L1-60)
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
from chia.wallet.nft_wallet.nft_wallet import NFTWallet
from chia.wallet.outer_puzzles import AssetType
from chia.wallet.puzzle_drivers import PuzzleInfo, Solver
from chia.wallet.trade_record import TradeRecord
from chia.wallet.trading.offer import NotarizedPayment, Offer
from chia.wallet.trading.trade_status import TradeStatus
from chia.wallet.trading.trade_store import TradeStore
from chia.wallet.transaction_record import TransactionRecord
from chia.wallet.util.compute_hints import compute_spend_hints_and_additions
from chia.wallet.util.query_filter import HashFilter
from chia.wallet.util.transaction_type import TransactionType
from chia.wallet.util.wallet_types import WalletType
from chia.wallet.vc_wallet.cr_cat_drivers import ProofsChecker, construct_pending_approval_state
from chia.wallet.vc_wallet.vc_wallet import VCWallet
from chia.wallet.wallet import Wallet
from chia.wallet.wallet_action_scope import WalletActionScope
from chia.wallet.wallet_coin_record import WalletCoinRecord
from chia.wallet.wallet_protocol import WalletProtocol
from chia.wallet.wallet_sync_scope import WalletSyncScope, WebSocketEvent

if TYPE_CHECKING:
    from chia.wallet.wallet_state_manager import WalletStateManager
from chia.wallet.puzzles.puzzle_drivers import UnknownPuzzle
from chia.wallet.wallet_spend_bundle import WalletSpendBundle


class TradeManager:
    """
    This class is a driver for creating and accepting settlement_payments.clsp style offers.

```
