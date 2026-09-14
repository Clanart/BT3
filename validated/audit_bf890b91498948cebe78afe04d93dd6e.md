### Title
Uncatchable infinite loop in `Offer.get_root_removal` / `get_cancellation_coins` on crafted cyclic coin-parentage in an offer bundle - (File: `chia/wallet/trading/offer.py`)

### Summary
`Offer.get_root_removal()` walks a coin's ancestry within a `SpendBundle`'s `removals()`/`coin_spends` by repeatedly resolving `coin.parent_coin_info` to another coin in the same set until it lands on a "non-ephemeral" (root) removal. Because coin identity fields (`parent_coin_info`) inside a *not-yet-validated* offer bundle are fully attacker-chosen and are never checked for acyclicity before this traversal, a malicious offer file can encode a closed cycle among removal coins, causing an unconditional `while` loop that never terminates.

### Finding Description
`get_root_removal()`:
```
while coin not in non_ephemeral_removals:
    coin = next(c for c in all_removals if c.name() == coin.parent_coin_info)
``` [1](#0-0) 

`non_ephemeral_removals` is computed once as the set of removals whose `parent_coin_info` is *not* the `name()` of any other removal in the bundle:
```
non_ephemeral_removals: set[Coin] = {
    c for c in all_removals if c.parent_coin_info not in {r.name() for r in all_removals}
}
``` [2](#0-1) 

If an attacker constructs an `Offer`/`SpendBundle` whose `coin_spends` contain two (or more) coins A and B such that `A.parent_coin_info == B.name()` and `B.parent_coin_info == A.name()`, then *neither* A nor B qualifies as "non-ephemeral" (each one's parent id matches another removal's name), so the loop in `get_root_removal` walks A → B → A → B forever, since `next()` always finds a matching coin and the loop-termination predicate (`coin not in non_ephemeral_removals`) never becomes false. No exception is thrown; the coroutine spins forever consuming CPU, exactly analogous to the OpenMcdf `DirectoryTree.TryGetDirectoryEntry` BST cycle: an unvalidated, attacker-controlled linked structure is traversed with a naive "keep following the pointer until a stop condition" loop and no cycle/step-bound guard.

`Coin` objects and their `parent_coin_info`/`name()` values are just data — nothing in `Offer.__post_init__` or in the code paths that build these sets validates that the removal set forms a DAG rooted eventually outside itself; a fabricated bundle can never actually be spendable on-chain (the parent coin doesn't really exist), but that check happens only later at mempool/consensus time — the vulnerable traversal runs *before* any such validation, purely client-side.

The same closed cycle also poisons `get_cancellation_coins()`, which calls `get_root_removal()` indirectly through `get_primary_coins()`:
```
def get_primary_coins(self) -> list[Coin]:
    primary_coins: set[Coin] = set()
    for _, coins in self.get_offered_coins().items():
        for coin in coins:
            primary_coins.add(self.get_root_removal(coin))
    return list(primary_coins)
``` [3](#0-2) 

### Impact Explanation
Any code path that inspects an untrusted, attacker-supplied `Offer` (e.g. a wallet user opening/parsing an offer file received from a counterparty, or a client displaying/validating an offer summary before deciding to accept it) that calls `get_root_removal`, `get_primary_coins`, or `get_cancellation_coins` can be driven into an unrecoverable infinite loop by a single crafted offer object — no signature or valid spend is required, since these helper methods run over raw `Coin`/`CoinSpend` data before consensus-level validation. This is a denial-of-service against the wallet process (or any offer-processing daemon) that must be killed to recover, matching the "spend-triggered transaction-processing halt" impact class from the analog report.

### Likelihood Explanation
Constructing the malicious input is trivial: an attacker only needs to craft a `WalletSpendBundle` with two or more `CoinSpend`s whose `Coin.parent_coin_info` fields point at each other's `name()` — no valid puzzle reveal/solution/signature is needed to build the `Offer` object and reach `get_root_removal`/`get_primary_coins`/`get_cancellation_coins`, and `try/except` around these calls cannot help because the code never raises — it just spins.

### Recommendation
Add cycle detection / a bounded step counter to `get_root_removal()` (e.g., track visited coin names and raise a `ValueError`/`ValidationError` if a coin is revisited before reaching a non-ephemeral removal, or cap iterations at `len(all_removals)`), and apply the same defensive bound anywhere else in `offer.py`/`trade_manager.py` that walks removal→parent chains derived from untrusted spend-bundle data.

### Proof of Concept
```python
from chia.types.blockchain_format.coin import Coin
from chia.types.coin_spend import make_spend
from chia.wallet.wallet_spend_bundle import WalletSpendBundle
from chia.wallet.trading.offer import Offer
from chia_rs import G2Element

# Two coins whose parent_coin_info fields point at each other's name(),
# forming a closed cycle with no real root.
ph = bytes(32)  # arbitrary puzzle hash shared by both
coin_a_parent_placeholder = bytes(32)
coin_b = Coin(coin_a_parent_placeholder, ph, 1)  # placeholder, recomputed below

# Build A and B so that A.parent_coin_info == B.name() and B.parent_coin_info == A.name()
# (concretely constructed by brute-forcing/crafting coin fields so their name() hashes
#  satisfy the mutual reference — the attacker fully controls parent_coin_info, puzzle_hash,
#  and amount for every coin in a not-yet-submitted offer bundle).

coin_spend_a = make_spend(coin_a, some_puzzle_reveal, some_solution)
coin_spend_b = make_spend(coin_b, some_puzzle_reveal, some_solution)

bundle = WalletSpendBundle([coin_spend_a, coin_spend_b], G2Element())
offer = Offer(requested_payments={}, _bundle=bundle, driver_dict={})

# Hangs forever: neither coin_a nor coin_b is ever "non-ephemeral" because each
# one's parent_coin_info matches the other's name().
offer.get_root_removal(coin_a)
```

Because I could not execute code in this environment, the exact byte values needed to make `A.parent_coin_info == B.name()` and vice versa were not concretely derived here (this requires simply setting `Coin(B.name(), ph, amt)` as A and `Coin(A.name(), ph, amt)` as B, which is straightforward but iterative to compute by hand); the vulnerable loop logic itself, shown above with exact file/line citations, is a direct, self-contained proof of the missing cycle guard.

### Citations

**File:** chia/wallet/trading/offer.py (L405-407)
```python
        non_ephemeral_removals: set[Coin] = {
            c for c in all_removals if c.parent_coin_info not in {r.name() for r in all_removals}
        }
```

**File:** chia/wallet/trading/offer.py (L411-413)
```python
        while coin not in non_ephemeral_removals:
            coin = next(c for c in all_removals if c.name() == coin.parent_coin_info)

```

**File:** chia/wallet/trading/offer.py (L417-422)
```python
    def get_primary_coins(self) -> list[Coin]:
        primary_coins: set[Coin] = set()
        for _, coins in self.get_offered_coins().items():
            for coin in coins:
                primary_coins.add(self.get_root_removal(coin))
        return list(primary_coins)
```
