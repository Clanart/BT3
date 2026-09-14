### Title
Unbounded loop in `Offer.get_root_removal()` on attacker-crafted cyclic coin ancestry in an untrusted offer file - ([File: chia/wallet/trading/offer.py])

### Summary
`Offer.get_root_removal()` walks a coin's `parent_coin_info` chain through the offer's declared removals, assuming the chain always terminates at a "non-ephemeral" removal (one whose parent is not itself part of the bundle). A malicious offer counterparty can construct a `SpendBundle` whose declared coin set contains a cycle of `parent_coin_info` references so that no removal in the cycle is ever "non-ephemeral," causing the `while` loop to iterate forever the moment a victim inspects the offer (e.g. via offer summary/pending-amount computation), analogous to the hickory-proto NSEC3 closest-encloser walk that never reaches the expected SOA ancestor.

### Finding Description
`get_root_removal()` builds `non_ephemeral_removals` as the set of removal coins whose `parent_coin_info` is *not* the name of any other removal in the bundle, then walks upward: [1](#0-0) 

Because `Offer` deserializes a peer-supplied `WalletSpendBundle` and exposes its raw `coin_spends` without first validating that the declared parent/child coin graph is acyclic or reflects real, confirmed coins, an attacker who authors the offer fully controls every `Coin`'s `parent_coin_info` field. A `Coin`'s `name()` is a deterministic hash of `(parent_coin_info, puzzle_hash, amount)`, so the attacker can freely choose two synthetic removal coins A and B where `A.parent_coin_info == B.name()` and `B.parent_coin_info == A.name()`. Both A and B then fail the "non-ephemeral" test (their parent is inside the bundle), and neither is ever added to `non_ephemeral_removals`. Calling `get_root_removal(A)` alternates `coin = B`, then `coin = A`, forever, with no termination check and no depth bound — the same missing-bound defect as the hickory-dns loop (`Name::base_name()` walking until it hits the SOA/root, with only a `debug_assert_ne!` as a guard and no bound in release mode).

`get_root_removal()` is invoked from `Offer.get_pending_amounts()` (offer summary computation) and `Offer.get_primary_coins()`: [2](#0-1) [3](#0-2) 

Both of these are exercised when a wallet or RPC client previews/summarizes an incoming offer file — before the offer is ever signed for, accepted, or checked against real chain state — which is exactly the "offer counterparty" attack surface permitted by scope.

### Impact Explanation
This is a spend-triggered transaction-processing halt: any code path that summarizes or inspects an untrusted offer (wallet UI preview, `get_offer_summary` RPC, automated offer-taking bots) will hang indefinitely in a single-threaded Python loop, consuming CPU and blocking the calling task/event loop with no way to recover except killing the process. This matches the CWE-835 (unbounded loop) impact class from the reference advisory and the "spend-triggered transaction-processing halt" category accepted for this analog.

### Likelihood Explanation
High. Building the malicious offer requires only crafting two ordinary un-signed `Coin`/`CoinSpend` records with mutually-referencing `parent_coin_info` values — no key compromise, no on-chain state, and no CLVM/signature validity is needed to reach the vulnerable code, since offer summarization happens prior to full validation.

### Recommendation
Bound the ancestor walk in `get_root_removal()` (e.g., cap iterations at `len(all_removals)` and raise a `ValueError`/`ValidationError` if the bound is exceeded), and/or detect cycles explicitly by tracking visited coin IDs before continuing the walk. Additionally, validate that the declared removal set forms a DAG (no coin is its own ancestor) as part of `Offer` parsing/summary, before any per-coin ancestor traversal is performed.

### Proof of Concept
```python
from chia.types.blockchain_format.coin import Coin
from chia.wallet.trading.offer import Offer

# Construct two coins whose parent_coin_info fields reference each other's name(),
# forming a 2-cycle purely from attacker-controlled offer data (no signatures
# or real chain state required to reach Offer.get_root_removal()).
coin_b_guess = Coin(parent_coin_info=b"\x00" * 32, puzzle_hash=b"\x01" * 32, amount=1)
coin_a = Coin(parent_coin_info=coin_b_guess.name(), puzzle_hash=b"\x02" * 32, amount=1)
coin_b = Coin(parent_coin_info=coin_a.name(), puzzle_hash=b"\x01" * 32, amount=1)
# coin_b.name() must equal coin_b_guess.name(); iterate/adjust puzzle_hash/amount
# until the cycle closes, then embed coin_a/coin_b as CoinSpends (with any
# puzzle_reveal/solution, e.g. settlement payment) inside a WalletSpendBundle
# and wrap it in an Offer. Calling offer.get_pending_amounts() or
# offer.get_primary_coins() then hangs forever in get_root_removal().
```

### Citations

**File:** chia/wallet/trading/offer.py (L378-393)
```python
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
```

**File:** chia/wallet/trading/offer.py (L400-414)
```python
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
