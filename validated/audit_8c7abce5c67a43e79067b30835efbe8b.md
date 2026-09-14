### Title
Crafted-offer coin-announcement self-dependency causes unhandled exception/DoS in `Offer.get_cancellation_coins` - (File: chia/wallet/trading/offer.py)

### Summary
The reported CVE (CVE-2016-2523) is a parsing routine that fails to guarantee forward progress on attacker-controlled structured input, causing an infinite loop / hang. The closest reachable analog in this codebase is `Offer.get_cancellation_coins()` in `chia/wallet/trading/offer.py`, which builds dependency/announcement maps directly from the CLVM conditions of an untrusted, counterparty-supplied `Offer` and then runs a fixed-point removal loop over those maps without guarding against a coin whose own `ASSERT_COIN_ANNOUNCEMENT` dependency matches its own `CREATE_COIN_ANNOUNCEMENT` output.

### Finding Description
`get_cancellation_coins` walks every non-addition coin spend in the offer bundle and, for each coin, executes its puzzle/solution with `INFINITE_COST` to extract raw condition data, then records: [1](#0-0) 

It builds `dependencies[name]` (coins this coin's spend asserts on) and `announcements[name]` (announcement hashes this coin's spend creates) purely from condition opcodes `60`/`61` found in the coin's own solution/puzzle output — values fully controlled by whoever constructed the offer's coin spends, since this data comes from parsing an `Offer` object that a wallet user receives from a counterparty (`Offer.from_bytes`/`from_bech32` in the same module, invoked when examining or attempting to cancel an offer before validating it against chain state).

The core reduction loop then repeatedly removes coins whose dependencies intersect a just-removed coin's announcements: [2](#0-1) 

and the helper that seeds each round: [3](#0-2) 

The inner loop seeds `remove_these_keys = [removed_coin]` and then, in the same pass, re-scans all `dependencies.items()` for entries whose deps intersect `removed_announcements` (initialized to `announcements[removed_coin]`, i.e., the just-removed coin's *own* announcements). If a coin's `ASSERT_COIN_ANNOUNCEMENT` condition is crafted to reference an announcement hash equal to one of its own `CREATE_COIN_ANNOUNCEMENT` outputs (a self-referential coin, which is trivially constructible in the puzzle/solution the offer author controls and is never checked against `provider`/self-exclusion), that same coin name gets appended to `remove_these_keys` a second time. The subsequent `dependencies.pop(coin)` / `announcements.pop(coin)` calls over `remove_these_keys` then attempt to pop the same key twice, raising an unhandled `KeyError`.

### Impact Explanation
This is not a memory-safety or fund-theft bug, but it is a spend-triggered halt of wallet-side transaction processing: any code path that calls `Offer.get_cancellation_coins()` (via `TradeManager` when a user tries to cancel/inspect a trade involving a maliciously crafted offer) raises an unhandled exception, denying the wallet operator the ability to process or cancel that trade. This maps to the "spend-triggered transaction-processing halt" outcome accepted by the validation criteria, analogous to the Wireshark dissector's crafted-packet DoS, except manifesting as an uncaught exception rather than a true infinite loop.

### Likelihood Explanation
An unprivileged offer counterparty can construct such an offer bundle by adding, to one of the coin spends, an `ASSERT_COIN_ANNOUNCEMENT` condition whose asserted hash equals the `std_hash` of that same coin's own `(coin_name || announcement_message)` pair from a `CREATE_COIN_ANNOUNCEMENT` it also emits. This requires no signature over conditions that affect validity (self-referential announcement assert/create pairs are satisfiable by construction) and no special privileges — only the ability to send/publish an offer file, which is the normal offer-exchange workflow.

### Recommendation
- In `detect_dependent_coin`/the reduction loop in `get_cancellation_coins`, exclude self-dependencies (`coin == name`/`coin == removed_coin`) explicitly, matching the existing `coin != provider` guard.
- De-duplicate `remove_these_keys` (e.g., using a `set`) before popping from `dependencies`/`announcements`, or use `dict.pop(coin, None)` to make the removal idempotent instead of raising on a missing key.
- Wrap the whole cancellation-coin computation in a `try/except` at the `TradeManager` call site so a malformed/adversarial offer surfaces a handled trade error instead of crashing the caller.

### Proof of Concept
1. Construct two coin spends `A` and `B` for an `Offer`, where `A`'s output conditions include both a `CREATE_COIN_ANNOUNCEMENT` with message `m` and an `ASSERT_COIN_ANNOUNCEMENT` for `std_hash(A.name() + m)` (i.e., `A` depends on its own announcement).
2. Include this in an `Offer` object (bundle with these coin spends, valid enough to pass basic offer structural checks) and deliver it to a victim wallet (e.g., as a `.offer` file or via `chia wallet take_offer`/`show`/`cancel_offer` inspection flow).
3. When the victim wallet calls `TradeManager`/`Offer.get_cancellation_coins()` on this offer, `detect_dependent_coin` returns `(A, A)`-style self match, seeding `remove_these_keys = [A]`; the inner scan re-adds `A` because `A`'s deps intersect `announcements[A]` and `A != provider`; `dependencies.pop(A)` succeeds once then `KeyError` is raised on the duplicate, halting the wallet's offer-cancellation/processing flow.

Note: I was unable to fully trace every call site of `get_cancellation_coins()`/`TradeManager` (e.g., exact RPC/CLI entry points that reach it) within the available search budget; this should be verified against the current call graph before treating impact as confirmed in all wallet flows.

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

**File:** chia/wallet/trading/offer.py (L424-444)
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

**File:** chia/wallet/trading/offer.py (L449-470)
```python
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
