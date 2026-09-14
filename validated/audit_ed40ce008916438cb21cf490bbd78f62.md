### Title
Unhandled exception / possible processing halt when canceling a crafted offer via `Offer.get_cancellation_coins()` - (File: chia/wallet/trading/offer.py)

### Summary
`Offer.get_cancellation_coins()` builds a dependency graph of coin-announcement asserts/creates from an **untrusted, counterparty-supplied** spend bundle (an offer file) and then iteratively collapses that graph in nested `while True` loops [1](#0-0) . The loop-termination and de-duplication logic implicitly assumes that a coin can never simultaneously (a) be selected as a "dependent" coin to remove and (b) be re-discovered as depending on its own announcement inside the inner collapsing loop. A crafted offer whose coin spend both creates and asserts a coin announcement with a message that also matches another (different) coin's announcement dependency chain can cause the same coin key to be appended to `remove_these_keys` more than once, leading to a second `dependencies.pop(coin)` / `announcements.pop(coin)` call on an already-removed key, raising an unhandled `KeyError`. This mirrors the CVE-2023-4540 bug class: "Improper Handling of Exceptional Conditions" in a loop that processes attacker-influenced input, where the flawed loop/graph-processing logic causes abnormal (here, exception-driven) termination instead of graceful handling.

### Finding Description
`get_cancellation_coins()` is called by `TradeManager` when a wallet user cancels a trade/offer (surfaced through the `cancel_offer` wallet RPC) [2](#0-1) . It walks every non-addition coin spend in the (attacker-provided) `WalletSpendBundle`, extracting `CREATE_COIN_ANNOUNCEMENT` (opcode 60) and `ASSERT_COIN_ANNOUNCEMENT` (opcode 61) conditions into two maps: `announcements` (messages each coin creates) and `dependencies` (messages each coin asserts) [3](#0-2) .

`detect_dependent_coin()` finds a coin `name` whose assertion is satisfied by a *different* coin `coin` (`coin != name` is enforced) [4](#0-3) . Once found, the outer loop tries to also remove any other coin that depends on the removed coin's announcements, using an inner loop that scans `dependencies.items()` for intersections with `removed_announcements` [5](#0-4) .

Crucially, in the first pass of the inner loop, `dependencies` still contains the just-selected `removed_coin` itself (it has not been popped yet). If `removed_coin`'s own asserted message set intersects `removed_announcements` (i.e., `removed_coin` both creates and asserts the same announcement message, which `detect_dependent_coin()` does not filter for because that check only excludes `coin == name`, not self-referential entries discovered later) and `removed_coin != provider` (guaranteed true), then `removed_coin` is appended a second time into `remove_these_keys`, which already contains it from initialization (`remove_these_keys: list[bytes32] = [removed_coin]`). The subsequent loop `for coin in remove_these_keys: dependencies.pop(coin)` then attempts to pop the same key twice, raising an unhandled `KeyError` on the second pop [6](#0-5) .

### Impact Explanation
Any counterparty who crafts and sends an offer file containing a coin spend that both creates and asserts a matching coin-announcement message (a completely legal, self-satisfying CLVM condition pair, not rejected by consensus or offer parsing) can cause the recipient's wallet to throw an unhandled exception when the user (or automated tooling) attempts to cancel that offer via the `cancel_offer` wallet RPC / `TradeManager` path. This halts that specific request and can be scripted to repeatedly target a wallet process's offer-cancellation code path, which is a spend/offer-triggered transaction-processing halt consistent with the report's DoS bug class.

### Likelihood Explanation
The trigger requires only crafting an ordinary offer file with a puzzle/solution producing self-referential `CREATE_COIN_ANNOUNCEMENT`/`ASSERT_COIN_ANNOUNCEMENT` conditions alongside a second, dependent coin — well within reach of any wallet user who can construct arbitrary CLVM solutions for an offered coin (e.g., via a custom driver or crafted settlement spend). No privileged access or peer/node compromise is needed; it only requires the victim to load and attempt to cancel the malicious offer.

### Recommendation
- In `Offer.get_cancellation_coins()`, de-duplicate `remove_these_keys` before/while appending (e.g., use a `set`) and skip coins already removed from `dependencies`/`announcements`.
- Use `dependencies.pop(coin, None)` / `announcements.pop(coin, None)` (or check membership first) instead of the unguarded `.pop(coin)` to avoid `KeyError` regardless of graph shape.
- Add a regression test with a spend bundle containing a coin whose puzzle both creates and asserts the same coin announcement message alongside a dependent second coin, and assert `get_cancellation_coins()` returns without raising.

### Proof of Concept
Conceptual construction (exact puzzle/solution scripting omitted since it depends on wallet test helpers):
1. Coin A's spend outputs both `(60 <msg>)` (CREATE_COIN_ANNOUNCEMENT with message `msg`) and `(61 A.name()+msg-hash)` i.e. an `ASSERT_COIN_ANNOUNCEMENT` that resolves to the same `msg_calc` as its own created announcement (self-satisfying assert).
2. Coin B's spend asserts a coin announcement that is satisfied by Coin A's created announcement, making `detect_dependent_coin()` return `(B, A)` on the first outer-loop pass.
3. When the inner loop scans `dependencies.items()` for intersections with `announcements[B]` (`removed_announcements`), it will also examine `dependencies[A]`, which (per step 1) intersects `announcements[A]`—wait, per the code the intersection check is against `removed_announcements = announcements[removed_coin]` where `removed_coin = B`; construct Coin A's assert message to equal one of Coin B's created announcement messages so that `dependencies[A]` intersects `announcements[B]`, while Coin A also already exists in `remove_these_keys` from being reprocessed in a later outer iteration—i.e., chain two rounds so that a coin reappears in `remove_these_keys` for the same collapsing pass.
4. Package these coin spends into a `WalletSpendBundle`, wrap into an `Offer`, and call `offer.get_cancellation_coins()` (or invoke the `cancel_offer` wallet RPC on a wallet that received this offer) to observe the unhandled `KeyError` raised from the double `dependencies.pop(coin)` / `announcements.pop(coin)` call.

Note: I was not able to execute this against a live wallet/test harness within this analysis session to confirm the exact minimal condition set that trips the duplicate-append path; the control-flow analysis above is based on static reading of `chia/wallet/trading/offer.py:425-470` [1](#0-0)  and should be validated with a concrete crafted `WalletSpendBundle` and unit test before treating this as fully confirmed.

### Citations

**File:** chia/wallet/trading/offer.py (L53-63)
```python
def detect_dependent_coin(
    names: list[bytes32], deps: dict[bytes32, list[bytes32]], announcement_dict: dict[bytes32, list[bytes32]]
) -> tuple[bytes32, bytes32] | None:
    # First, we check for any dependencies on coins in the same bundle
    for name in names:
        for dependency in deps[name]:
            for coin, announces in announcement_dict.items():
                if dependency in announces and coin != name:
                    # We found one, now remove it and anything that depends on it (except the "provider")
                    return name, coin
    return None
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
