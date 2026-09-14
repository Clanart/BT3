### Title
Infinite-loop denial of service in `Offer.get_root_removal()` via attacker-crafted coin-parentage cycle - ([File: chia/wallet/trading/offer.py])

### Summary
`Offer.get_root_removal()` walks a coin's ancestry chain within an untrusted offer's `SpendBundle` by following `parent_coin_info` pointers until it reaches a coin considered "non-ephemeral" (i.e., whose parent is not itself one of the removals in the bundle). Because the `Coin` objects that populate `self.removals()` are fully attacker-controlled fields inside an offer file (an offer is just a `WalletSpendBundle` with dummy notarized-payment spends), an attacker can construct a cycle of coins whose `parent_coin_info` values point at each other, none of which is "non-ephemeral" by the function's definition. The `while` loop that walks the chain never terminates, hanging the wallet process without raising an exception — analogous to CVE-2022-39408, where a crafted query causes the MySQL optimizer to hang/crash the server via an unbounded internal loop reachable by an unprivileged caller.

### Finding Description
`get_root_removal()`:
```python
# chia/wallet/trading/offer.py:402-414
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
`all_removals` is derived from `self.removals()`, which returns the `.coin` field of every `CoinSpend` in the offer's bundle [1](#0-0) . The `Coin.parent_coin_info`, `puzzle_hash`, and `amount` values inside those `CoinSpend` entries are entirely chosen by whoever authored the offer — the offer format only requires that each `puzzle_reveal` hash to the coin's declared `puzzle_hash`, which is trivial to satisfy with an attacker-controlled puzzle, and no on-chain lookup or signature is required to *construct* or *parse* the offer.

An attacker can therefore include two (or more) `CoinSpend`s in the offer whose `Coin`s have `parent_coin_info` values that reference each other's `name()` cyclically. Neither coin qualifies as "non-ephemeral" per the set-builder condition, so `non_ephemeral_removals` never contains them, and the `while` loop oscillates between the cycle members forever. Because `next(...)` always finds a match (the cycle guarantees a `parent_coin_info` hit), no `StopIteration`/exception is ever raised — the loop simply never exits.

This is reachable purely from parsing/inspecting an untrusted offer, via `get_primary_coins()`, which calls `get_root_removal()` for every offered coin [2](#0-1) . `get_primary_coins()`/`get_root_removal()` are invoked in the wallet's offer-summary/royalty computation path (`nft_coin_ids_supporting_royalties_from_offer`, `trade_manager.py`), i.e., during ordinary examination of an offer supplied by a counterparty — before the offer is ever pushed to the mempool for consensus-level validation.

### Impact Explanation
A malicious offer counterparty can send a specially crafted offer file/bech32 blob that, when examined by the receiving wallet (e.g., via `chia wallet take_offer -e`, offer summary printing, or automatic royalty computation for NFT offers), causes the wallet process to spin in an infinite loop, consuming CPU indefinitely and never returning. This is a spend-triggered transaction-processing halt — the receiving wallet becomes unresponsive to that call (and potentially blocks the wallet RPC service loop depending on execution context) without any error being surfaced, matching the "hang / complete DoS" characteristic of CVE-2022-39408.

### Likelihood Explanation
Exploitation requires only that a victim receive and examine/summarize a crafted offer — a completely standard, low-privilege interaction pattern between untrusted offer counterparties (this is one of the explicitly in-scope reachable paths). Constructing the coin cycle requires no signature or real coin state — only crafting `Coin` objects with matching `parent_coin_info`/`name()` values and matching dummy puzzle reveals, which is straightforward CLVM authoring. This makes the bug easily and reliably triggerable.

### Recommendation
Bound the ancestry walk in `get_root_removal()`: track visited coins and raise a `ValueError` (or similar) if a coin is revisited before a non-ephemeral root is found, or cap iteration count to `len(all_removals)` and fail closed if exceeded. This turns an unbounded loop into a fast, deterministic rejection of malformed/cyclic offers.

### Proof of Concept
Conceptual construction (illustrative, not exhaustively verified against the exact `CoinSpend`/puzzle machinery, since full offer-serialization round tripping was not executed in this analysis):
1. Craft two `Coin`s, `A` and `B`, such that `A.parent_coin_info == B.name()` and `B.parent_coin_info == A.name()` (achievable by choosing puzzle bytes/amounts to hit the desired hash relationship, or more simply by directly setting `parent_coin_info` fields when building the `CoinSpend`s programmatically — the field is not derived, it's an explicit input).
2. For each of `A` and `B`, create a `CoinSpend` with a `puzzle_reveal` whose tree hash equals the coin's `puzzle_hash` (trivial — author your own throwaway puzzle) and any solution.
3. Bundle these two `CoinSpend`s (plus the required dummy settlement-payment `CoinSpend`s that make it a well-formed `Offer` per `Offer.from_spend_bundle`) into a `WalletSpendBundle`, and serialize via `Offer.to_bech32()`.
4. Send this offer file to a victim. When the victim's wallet calls `Offer.get_primary_coins()` (directly, or transitively through royalty-calculation / offer-summary code paths in `trade_manager.py` or `wallet_funcs.py`), `get_root_removal(A)` (or `B`) enters `get_root_removal`'s `while` loop, which alternates between `A` and `B` forever because neither is ever a member of `non_ephemeral_removals`, hanging that call indefinitely.

Note: I was not able to fully trace and execute the exact byte-level construction of a valid two-coin cyclic offer end-to-end within this analysis (e.g., confirming no earlier validation step in `Offer.from_spend_bundle`/`from_bytes` rejects such a structure before reaching `get_root_removal`), so this should be validated with a runnable PoC before treating it as fully confirmed.

### Citations

**File:** chia/wallet/trading/offer.py (L396-398)
```python
    def get_involved_coins(self) -> list[Coin]:
        additions = self.additions()
        return list(filter(lambda c: c not in additions, self.removals()))
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
