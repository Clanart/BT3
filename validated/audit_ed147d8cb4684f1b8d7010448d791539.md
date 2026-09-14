## Finding: Integer truncation in NFT royalty trade-price computation can eliminate on-chain royalty enforcement — analogous to the reported division-order bug

### Title
Division-before-multiplication truncation in NFT offer royalty `trade_prices` computation can zero out on-chain royalty enforcement - (`chia/wallet/nft_wallet/nft_wallet.py`)

### Summary
The external report's root cause is a classic "divide-before-multiply" truncation bug: dividing a value first and then multiplying/dividing again causes legitimate non-zero results to floor to `0`, disabling an entire payment path. The same structural pattern exists in Chia's NFT royalty enforcement logic used when constructing offers for royalty-enabled NFTs (`NFTWallet.make_nft1_offer`).

### Finding Description
When an offer maker sells one or more royalty-enabled NFTs together, the wallet computes a per-NFT `trade_price` by dividing the requested fungible payment by the number of royalty NFTs *before* any multiplication by the royalty percentage: [1](#0-0) 

```python
trade_prices: list[tuple[uint64, bytes32]] = []
for asset, amount in fungible_asset_dict.items():  # requested fungible items
    if amount > 0 and offer_side_royalty_split > 0:
        ...
        trade_prices.append((uint64(amount // offer_side_royalty_split), settlement_ph))
```

This `trade_price` (already floor-divided by `offer_side_royalty_split`) is later combined with the royalty percentage and, critically, used to decide whether to even *include* the trade price (and therefore the royalty enforcement condition) at all: [2](#0-1) 

```python
trade_prices_list=[
    list(price)
    for price in trade_prices
    if price[0] * offered_royalty_percentages[asset] // MAX_ROYALTY_BASIS_POINTS != 0
],
```

If `amount // offer_side_royalty_split` truncates to `0` (which happens whenever the requested payment amount is smaller than the number of royalty NFTs bundled in the same offer), `price[0]` is `0`, so `price[0] * percentage // 10000` is also `0`, and the trade price is filtered out entirely. This `trade_prices_list` is what gets passed into the NFT ownership layer's `-10` transfer condition, which is what the on-chain transfer program (`NFT_TRANSFER_PROGRAM_DEFAULT` / `NFT_OWNERSHIP_TRANSFER_PROGRAM_ONE_WAY_CLAIM_WITH_ROYALTIES`) uses to require a corresponding royalty-payment announcement from the paying coin, as demonstrated in the simulator test: [3](#0-2) 

The same divide-before-multiply order also appears in the royalty-amount computation itself: [4](#0-3) 

```python
def compute_royalty_amount(offered_amount: int, royalty_split: int, percentage: int) -> uint64:
    ...
    amount = abs(offered_amount) // royalty_split * percentage // MAX_ROYALTY_BASIS_POINTS
```

and in the display-only summary helper: [5](#0-4) 

### Impact Explanation
Because the trade price is floor-divided by the number of bundled royalty NFTs *before* it is scaled by the royalty percentage, an offer maker can construct an offer bundling multiple royalty-enabled NFTs against a fungible payment amount small enough that `amount // offer_side_royalty_split == 0`. The wallet then omits the trade price (and hence the royalty-payment condition) from the spend entirely for that NFT/asset pair — the NFT can be transferred without the on-chain announcement that would otherwise force payment of the royalty owed to the (third-party) royalty address, even though the NFT's curried royalty percentage is non-zero. This is a truncation-driven bypass of the intended royalty payment guarantee, i.e., settlement theft against the royalty recipient, mirroring the reported Solidity issue where `amountToBuyLeftUSD * 1e18 / collateralval / 1e18` truncates to zero and silently disables the intended operation.

### Likelihood Explanation
Reachable purely through normal offer construction (`NFTWallet.make_nft1_offer` / `TradeManager.check_for_special_offer_making`), triggered whenever a maker bundles several royalty NFTs against a comparatively small fungible request — no privileged access or malicious peer/node behavior required, only crafting of offer parameters by any offer maker.

### Recommendation
Perform the multiplication by the royalty percentage before dividing by `offer_side_royalty_split` (and only floor-divide once at the end), matching the pattern already fixed elsewhere for `compute_royalty_amount`'s overflow-safety validation, and ensure the `trade_prices_list` inclusion check does not use an already-truncated intermediate value to decide whether royalty enforcement should be skipped.

### Proof of Concept
1. Maker creates an offer bundling 2 royalty-enabled NFTs (`royalty_percentage > 0`) as the offered assets, requesting a total fungible payment of `1` mojo.
2. In `make_nft1_offer`, `offer_side_royalty_split = 2`, so `trade_prices.append((uint64(1 // 2), settlement_ph))` yields `trade_price = 0`.
3. At [6](#0-5)  the filter `price[0] * offered_royalty_percentages[asset] // MAX_ROYALTY_BASIS_POINTS != 0` evaluates `0 * percentage // 10000 == 0`, so the trade price entry is dropped from `trade_prices_list`.
4. The resulting NFT-sale spend bundle contains no `-10` royalty trade-price obligation for that NFT, and the transfer completes without any enforced royalty announcement/payment, even though the NFT's on-chain royalty percentage is non-zero.

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

**File:** chia/wallet/nft_wallet/nft_wallet.py (L1026-1032)
```python
                            coins=offered_coins_by_asset[asset],
                            trade_prices_list=[
                                list(price)
                                for price in trade_prices
                                if price[0] * offered_royalty_percentages[asset] // MAX_ROYALTY_BASIS_POINTS != 0
                            ],
                            extra_conditions=(*extra_conditions, *announcements_to_assert),
```

**File:** chia/_tests/wallet/nft_wallet/test_nft_lifecycle.py (L306-322)
```python
        ownership_spend = make_spend(
            ownership_coin,
            ownership_puzzle,
            Program.to(
                [[[51, ACS_PH, 1], [-10, FAKE_LAUNCHER_ID, [[100, ACS_PH], [100, FAKE_CAT.get_tree_hash()]], ACS_PH]]]
            ),
        )

        did_announcement_spend = make_spend(
            singleton_coin,
            FAKE_SINGLETON,
            Program.to([[[62, FAKE_LAUNCHER_ID]]]),
        )

        expected_announcement_data = Program.to(
            (FAKE_LAUNCHER_ID, [[ROYALTY_ADDRESS, 50, [ROYALTY_ADDRESS]]])
        ).get_tree_hash()
```
