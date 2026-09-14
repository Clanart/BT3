### Title
NFT royalty integer-division truncation lets offer takers bypass royalty payments entirely - (File: `chia/wallet/nft_wallet/nft_wallet.py`)

### Summary
The wallet's NFT royalty computation `compute_royalty_amount` uses truncating integer arithmetic (`abs(offered_amount) // royalty_split * percentage // MAX_ROYALTY_BASIS_POINTS`), which rounds down to `0` whenever the traded amount is small relative to `MAX_ROYALTY_BASIS_POINTS` (10000) and the royalty percentage. Wallet code additionally filters out any `trade_prices_list` entry whose computed royalty would be zero. This mirrors the reported bug class ("rewards accumulated can stay constant / round to zero"), applied here to NFT royalty payments in Chia's offer/trade flow.

### Finding Description
`compute_royalty_amount` in [1](#0-0)  computes:
```
amount = abs(offered_amount) // royalty_split * percentage // MAX_ROYALTY_BASIS_POINTS
```
This is confirmed by the repo's own test `test_small_amount_truncates_to_zero`, which asserts `compute_royalty_amount(offered_amount=-50, royalty_split=1, percentage=100) == uint64(0)` [2](#0-1) . This is exactly the class of rounding-to-zero bug described in the H-04 report: integer division against a large fixed denominator (here 10000 basis points, analogous to `_totalSupply`) truncates small numerators to zero.

Separately, `royalty_calculation` (the summary-only helper) has the identical pattern [3](#0-2) .

Critically, this isn't just a cosmetic display issue — the actual offer-construction code in `make_nft1_offer` uses the same truncating math to decide which `trade_prices_list` entries to even include in the spend:
```python
trade_prices_list=[
    list(price)
    for price in trade_prices
    if price[0] * offered_royalty_percentages[asset] // MAX_ROYALTY_BASIS_POINTS != 0
],
``` [4](#0-3) 

And royalty payment coins are entirely skipped when the computed sum is zero:
```python
payments = royalty_payments[asset] if asset in royalty_payments else []
if sum(p.amount for _, p in payments) == 0:
    continue
``` [5](#0-4) 

The trade price used to derive the royalty is itself constructed with the same truncating division at the fungible-asset stage: `trade_prices.append((uint64(amount // offer_side_royalty_split), settlement_ph))` [6](#0-5) .

The on-chain enforcement side (the `NFT_TRANSFER_PROGRAM_DEFAULT` chialisp puzzle, referenced in `chia/wallet/nft_wallet/transfer_program_puzzle.py` and `nft_puzzle_utils.py`) is where royalty amounts are ultimately validated as part of the singleton's `-10` (change-owner) condition and `trade_prices_list`, but I was not able to load/verify the exact CLVM arithmetic inside that puzzle within the available context (the `.clsp`/`.clvm.hex` source itself was not returned by my searches). Because of this, I cannot fully confirm whether the on-chain puzzle *re-derives* the royalty amount from `trade_prices_list * percentage / 10000` (in which case the puzzle itself would independently allow zero-royalty spends for small trade prices, matching the taker side of the attack) or whether it simply trusts the wallet-supplied `trade_prices_list`/`CreateCoin` amounts without recomputation.

### Impact Explanation
If a counterparty (offer taker) structures the fungible payment amount and the number of items so that `amount // offer_side_royalty_split` — and subsequently `trade_price * percentage / 10000` — always evaluates to zero (e.g., by splitting a purchase across many low-value fungible assets, or by trading in very small denominations relative to a modest royalty percentage such as under 1%), the NFT creator's royalty is truncated to zero and the code path explicitly omits creating any royalty payment or trade-price assertion. This lets an offer taker acquire royalty-bearing NFTs while paying no royalty at all to the creator/royalty address, defeating the entire purpose of the NFT1 royalty mechanism (an economic value-theft vector against the royalty recipient, analogous to "no one ever gets rewards" in the reference report).

### Likelihood Explanation
Likelihood is **not naturally high** by accident (unlike the ERC-20 report, where any active transfer volume triggers it) because Chia NFT trades are typically single discrete purchases rather than continuous streaming accrual. However, a **deliberate structuring attack is plausible**: any wallet user constructing an offer as the taker controls the fungible amount and can choose values/splits that zero out the royalty basis-point computation, especially for NFTs with low royalty percentages (e.g., 1%–5%, which is common) combined with modest trade amounts. This requires no special privilege — any offer counterparty can do it — and matches the "unprivileged offer counterparty" reachable actor.

### Recommendation
- Round the royalty computation up (ceiling division) rather than truncating down, or enforce a minimum non-zero royalty when `percentage > 0` and `offered_amount != 0`, mirroring the audited advice from the reference report ("don't let rounding eat the reward/fee to zero").
- Remove or gate the filtering logic in `make_nft1_offer` that silently drops `trade_prices_list` entries and royalty payments when the computed amount is `0` — instead, either reject offers whose royalty would round to zero, or preserve fractional precision (e.g., scale amounts up before dividing) so genuine sub-basis-point royalties aren't silently discarded.
- Audit the `NFT_TRANSFER_PROGRAM_DEFAULT` chialisp puzzle to confirm whether it independently recomputes/enforces the royalty amount on-chain from `trade_prices_list` and `percentage`; if it does using the same truncating division, apply the identical fix on-chain, since a client-side fix alone would not prevent a malicious spend bundle constructed outside the standard wallet code from exploiting the same truncation.

### Proof of Concept
Using the repo's own test module as a demonstration of the truncation:
```python
from chia.wallet.nft_wallet.nft_wallet import compute_royalty_amount

# NFT creator sets a 1% royalty (100 basis points)
result = compute_royalty_amount(offered_amount=-50, royalty_split=1, percentage=100)
assert result == 0  # confirmed by test_small_amount_truncates_to_zero
``` [2](#0-1) 

An offer taker purchasing a royalty-bearing NFT for a total price of 50 mojo (or any amount `a` such that `a * percentage < 10000`) causes `compute_royalty_amount` to return `0`. In `make_nft1_offer`, this zero result means:
1. The `trade_prices_list` filter drops the entry [7](#0-6) .
2. The royalty payment coin creation is skipped entirely [5](#0-4) .

The taker receives the NFT while the royalty recipient receives nothing, for any trade structured to keep per-unit amounts below the rounding threshold. Full end-to-end confirmation that the underlying CLVM puzzle also permits this (rather than independently enforcing a non-zero royalty) could not be completed within available tool calls; a Devin session with full repository/file access would be needed to inspect `nft_puzzles.py`'s embedded `NFT_TRANSFER_PROGRAM_DEFAULT` CLVM bytecode/source directly.

### Citations

**File:** chia/wallet/nft_wallet/nft_wallet.py (L67-75)
```python
def compute_royalty_amount(offered_amount: int, royalty_split: int, percentage: int) -> uint64:
    """Compute royalty using integer arithmetic, validating against overflow and excessive percentage."""
    if percentage > MAX_ROYALTY_BASIS_POINTS:
        raise ValueError(f"NFT royalty percentage {percentage} exceeds 100% ({MAX_ROYALTY_BASIS_POINTS} basis points)")
    amount = abs(offered_amount) // royalty_split * percentage // MAX_ROYALTY_BASIS_POINTS
    royalty = uint64(amount)
    if royalty >= abs(offered_amount):
        raise ValueError("Royalty amount meets or exceeds the offered amount")
    return royalty
```

**File:** chia/wallet/nft_wallet/nft_wallet.py (L839-857)
```python
    @staticmethod
    def royalty_calculation(
        royalty_assets_dict: dict[Any, tuple[Any, uint16]],
        fungible_asset_dict: dict[Any, uint64],
    ) -> dict[Any, list[dict[str, Any]]]:
        summary_dict: dict[Any, list[dict[str, Any]]] = {}
        for id, royalty_info in royalty_assets_dict.items():
            address, percentage = royalty_info
            summary_dict[id] = []
            for name, amount in fungible_asset_dict.items():
                summary_dict[id].append(
                    {
                        "asset": name,
                        "address": address,
                        "amount": abs(amount) // len(royalty_assets_dict) * percentage // MAX_ROYALTY_BASIS_POINTS,
                    }
                )

        return summary_dict
```

**File:** chia/wallet/nft_wallet/nft_wallet.py (L896-902)
```python
        trade_prices: list[tuple[uint64, bytes32]] = []
        for asset, amount in fungible_asset_dict.items():  # requested fungible items
            if amount > 0 and offer_side_royalty_split > 0:
                settlement_ph: bytes32 = (
                    OFFER_MOD_HASH if asset is None else construct_puzzle(driver_dict[asset], OFFER_MOD).get_tree_hash()
                )
                trade_prices.append((uint64(amount // offer_side_royalty_split), settlement_ph))
```

**File:** chia/wallet/nft_wallet/nft_wallet.py (L1019-1033)
```python
                    else:
                        assert asset is not None
                        await wallet.generate_signed_transaction(
                            [abs(amount)],
                            [OFFER_MOD_HASH],
                            inner_action_scope,
                            fee=fee_left_to_pay,
                            coins=offered_coins_by_asset[asset],
                            trade_prices_list=[
                                list(price)
                                for price in trade_prices
                                if price[0] * offered_royalty_percentages[asset] // MAX_ROYALTY_BASIS_POINTS != 0
                            ],
                            extra_conditions=(*extra_conditions, *announcements_to_assert),
                        )
```

**File:** chia/wallet/nft_wallet/nft_wallet.py (L1043-1046)
```python
                    # Skip it if we're paying 0 royalties
                    payments = royalty_payments[asset] if asset in royalty_payments else []
                    if sum(p.amount for _, p in payments) == 0:
                        continue
```
