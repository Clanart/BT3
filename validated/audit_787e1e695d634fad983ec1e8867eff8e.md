### Title
Attacker-controlled offer `requested_payments` amounts cause an unhandled `uint64` overflow, crashing offer summary/validation - ([File: chia/wallet/trading/offer.py])

### Summary
`Offer.get_offered_amounts()` and `Offer.get_requested_amounts()` sum a list of coin/payment amounts and cast the raw Python-integer sum directly into `uint64`: [1](#0-0) 

For the *requested* side, the list being summed (`self.requested_payments[asset_id]`, a list of `NotarizedPayment`) is not derived from real, already-consensus-validated coins — it is attacker-controlled data embedded directly in the serialized offer file/bech32 that any counterparty can craft and hand to a victim. Each individual `NotarizedPayment.amount` is a `uint64` field (inherited from `CreateCoin`) and can individually be as large as `uint64` max, so summing even two or three such payments for the same asset id overflows 64 bits before the `uint64(...)` cast is applied, raising an unhandled `OverflowError`.

### Finding Description
`NotarizedPayment` extends `CreateCoin` and is populated straight from deserialized offer data: [2](#0-1) 

`Offer.requested_payments` is a plain `dict[bytes32 | None, list[NotarizedPayment]]` stored on the `Offer` dataclass with no bound placed on the *sum* of amounts per asset — only an individual `CreateCoin.amount` is constrained to fit in a `uint64` (i.e., up to `2**64-1`), not the aggregate across multiple payments requesting the same asset.

`get_requested_amounts()` (and the analogous `get_offered_amounts()`) reduce this list with plain Python `sum()` and then coerce to `uint64`: [3](#0-2) 

Because `sum()` operates on unbounded Python ints, two `NotarizedPayment` entries each near `2**64-1` sum to roughly `2**65`, which cannot be represented in `uint64`. `chia_rs`'s `sized_ints.uint64` constructor raises `OverflowError` on out-of-range values (as demonstrated by existing tests expecting `OverflowError` from `Coin(...)` construction with out-of-range amounts): [4](#0-3) 

This is the same bug class as the external report: a computed aggregate (sum of multiple legitimate-looking entries) is downcast into a fixed-width integer without pre-validating that the aggregate fits, so a value that is individually valid becomes an unrecoverable overflow when combined — causing a hard crash instead of a graceful validation error.

`arbitrage()` and `summary()` — used by essentially every code path that inspects an offer (CLI `take_offer`/`get_offers`, RPC `get_offer_summary`, `is_valid()`, trade manager bookkeeping) — call `get_offered_amounts()`/`get_requested_amounts()`: [5](#0-4) 

### Impact Explanation
Any wallet user, RPC caller, or CLI operator who merely inspects (not even accepts) a maliciously crafted offer file will hit an unhandled `OverflowError`. This affects:
- `chia wallet get_offer_summary` / `wallet_rpc_api.get_offer_summary`, which calls `request.parsed_offer.summary()`.
- `chia wallet take_offer --examine-only` and the take-offer confirmation flow (`cmds/wallet_funcs.py`), which calls `offer.summary()` before any confirmation.
- `Offer.is_valid()` (used internally to accept/aggregate offers), which calls `arbitrage()`.

Since this triggers on parsing/inspection rather than requiring the offer to actually be accepted on-chain, it is a straightforward, low-cost denial-of-service against wallet/RPC callers processing untrusted offer files — a normal, expected trust boundary (offer counterparties routinely exchange offer files that have not yet been validated on-chain). It does not itself cause coin theft or consensus divergence, but it is a reliable crash triggered purely from an attacker-supplied artifact reachable by any offer counterparty.

### Likelihood Explanation
High likelihood of triggering: constructing the malicious offer requires only crafting two or three `NotarizedPayment` entries with amounts near `uint64` max for the same requested asset id inside an otherwise-normal-looking offer bech32/file — no special node/network access or timing is needed. The victim needs only to view or attempt to take the offer through any of the normal wallet/RPC/CLI entry points.

### Recommendation
- In `get_offered_amounts()` / `get_requested_amounts()` (and `fees()`, which has the same unguarded `sum()` pattern), sum into a wide/unbounded integer first and explicitly validate the total against `MAX_COIN_AMOUNT` (or the relevant bound) with a graceful `ValueError`/`ValidationError` instead of letting the `uint64()` cast raise an uncaught `OverflowError`.
- Enforce this bound as part of `Offer.__post_init__`/deserialization so malformed offers are rejected immediately with a clear error rather than crashing deep inside summary/validation logic.
- Audit other locations using the same `uint64(sum(...))` pattern over attacker-influenced collections (e.g., `chia/wallet/nft_wallet/nft_wallet.py`, `chia/wallet/vc_wallet/cr_cat_wallet.py`) for the same issue.

### Proof of Concept
1. Craft an `Offer` (or its serialized bech32 form) whose `requested_payments[asset_id]` contains two `NotarizedPayment` entries for the same `asset_id`, each with `amount = uint64(2**64 - 1)` (the max representable value), differing only in `puzzle_hash`/`nonce` so they are not treated as duplicates.
2. Serialize this offer and deliver it to a victim (e.g., as an offer file, or via any offer-sharing mechanism).
3. Victim calls any inspection path, e.g.:
   - `wallet_rpc_client.get_offer_summary(...)` → `request.parsed_offer.summary()`
   - `chia wallet take_offer <file> --examine-only`
4. `Offer.summary()` → `get_requested_amounts()` executes `uint64(sum(c.amount for c in coins))` where the sum (`~2**65 - 2`) exceeds `uint64` max, raising `OverflowError`, propagating as an unhandled exception up through the CLI/RPC call and aborting the operation. [1](#0-0)

### Citations

**File:** chia/wallet/trading/offer.py (L66-78)
```python
@dataclass(frozen=True)
class NotarizedPayment(CreateCoin):
    nonce: bytes32 = bytes32.zeros

    @classmethod
    def from_condition_and_nonce(cls, condition: Program, nonce: bytes32) -> NotarizedPayment:
        with_opcode: Program = Program.to((51, condition))  # Gotta do this because the super class is expecting it
        p = CreateCoin.from_program(with_opcode)
        return cls(p.puzzle_hash, p.amount, p.memos, nonce=nonce)

    def name(self) -> bytes32:
        return self.to_program().get_tree_hash()

```

**File:** chia/wallet/trading/offer.py (L313-327)
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
```

**File:** chia/wallet/trading/offer.py (L329-367)
```python
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
```

**File:** chia/_tests/core/custom_types/test_coin.py (L73-83)
```python
def test_construction() -> None:
    H1 = b"a" * 32
    H2 = b"b" * 32

    with pytest.raises(OverflowError, match="int too big to convert"):
        # overflow
        Coin(H1, H2, 0x10000000000000000)  # type: ignore[arg-type]

    with pytest.raises(OverflowError, match="can't convert negative int to unsigned"):
        # overflow
        Coin(H1, H2, -1)  # type: ignore[arg-type]
```
