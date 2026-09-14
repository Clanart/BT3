### Title
NFT royalty enforcement silently bypassed by basis-points truncation - (File: `chia/wallet/nft_wallet/nft_wallet.py`)

### Summary
The Panoptic report shows a fee mechanism that computes `fee = value * percentage / FIXED_DECIMALS` using integer division, which silently truncates to zero when `value * percentage < FIXED_DECIMALS`, letting the payer skip the fee entirely. Chia's NFT royalty ("creator fee") system uses the exact same arithmetic pattern with a fixed basis-points denominator of `10000`, and the wallet code explicitly special-cases and tolerates the zero-truncation outcome rather than preventing it.

### Finding Description
`compute_royalty_amount` computes the royalty owed on an NFT trade using pure integer division against a fixed `MAX_ROYALTY_BASIS_POINTS = 10000` denominator: [1](#0-0) 

When `abs(offered_amount) // royalty_split * percentage` is less than `10000`, the result truncates to `0`, and the function returns `uint64(0)` without error — this is confirmed by the project's own unit test: [2](#0-1) 

`NFTWallet.royalty_calculation` (used both by `make_nft1_offer` and exposed via the `nft_calculate_royalties` RPC) performs the identical truncating division: [3](#0-2) 

The offer-construction code additionally filters out trade prices whose royalty would compute to zero before even attempting to enforce them on-chain via the `trade_prices_list` passed to the transfer-program puzzle: [4](#0-3) 

The on-chain enforcement itself lives in the NFT ownership-layer transfer program, which is curried with a `royalty_percentage` (basis points out of `10000`) and a `royalty_address`: [5](#0-4) 

Because the on-chain puzzle computes `trade_price * royalty_percentage // 10000` with the same fixed 10000 denominator (verified by the `TRADE_PRICE_PERCENTAGE` curried-parameter test fixture using basis points), any trade whose `trade_price * royalty_percentage` product is less than `10000` produces a computed royalty of exactly zero, and the ownership-layer puzzle does not require any `CREATE_COIN` payment to the royalty address for that trade. Nothing in the puzzle or wallet code rejects or flags an offer that structures payments below this threshold — it is treated as a normal, valid, zero-royalty trade.

### Impact Explanation
An NFT minter sets `royalty_percentage` once at mint time (any value up to `10000`, i.e., up to 100%, is legal, including very small values such as `1` = 0.01%). Because both the wallet's offer-construction logic and the on-chain transfer-program enforcement use the same fixed `10000` basis-point denominator with floor (integer) division, a counterparty can structure the settlement price of a purchase (or split a large purchase into many small trade legs, each priced under `10000 / royalty_percentage`) so that the computed royalty is `0` for every leg, while the aggregate consideration paid to the seller is unchanged. This fully defeats the royalty/creator-fee guarantee that the ownership-layer transfer program is specifically designed to enforce, without requiring any invalid spend, double-spend, or malicious peer — it is achievable by any ordinary offer-taker using only the standard NFT-offer flow. The wallet code's own handling (skipping the trade price from `trade_prices_list` entirely whenever the computed royalty is zero) demonstrates that the code path is a routine, reachable outcome, not a mere theoretical edge case.

This is a "broken functionality" class issue analogous to the confirmed Panoptic finding: fee enforcement that is intended to always apply for a nonzero configured rate can be evaded by an ordinary user for small enough transaction values, due to fixed-decimal truncation.

### Likelihood Explanation
Likelihood is high for any NFT with a low royalty percentage or when a buyer deliberately structures multiple small-value trade legs. No privileged access, malicious peer, or protocol-level exploit is required — only a standard offer/counter-offer using the public wallet RPC and CLVM offer flow, which is the intended user-facing NFT trading mechanism.

### Recommendation
- Enforce a minimum trade-price threshold relative to `royalty_percentage` before allowing an NFT offer to settle with zero royalty (reject or round up rather than silently zeroing), or
- Round up (`ceil`) the royalty computation instead of floor division so any nonzero `royalty_percentage` always yields a nonzero payment for any nonzero trade price, and
- Ensure the on-chain transfer-program puzzle enforces the same non-zero-rounding rule so wallet-side filtering (`nft_wallet.py:1019-1033`) cannot be bypassed by a non-standard offer constructor that omits the zero-check.

### Proof of Concept
1. Mint an NFT with `royalty_percentage = 1` (0.01%) via `puzzle_for_transfer_program` (`chia/wallet/nft_wallet/transfer_program_puzzle.py:22-28`).
2. Create/accept an NFT-for-XCH offer where the fungible trade price for that NFT is less than `10000` mojos (e.g., `9999` mojos), using the standard `NFTWallet.make_nft1_offer` flow.
3. `compute_royalty_amount(offered_amount=-9999, royalty_split=1, percentage=1)` returns `uint64(0)` (per `chia/wallet/nft_wallet/nft_wallet.py:67-75`, confirmed by `test_small_amount_truncates_to_zero` in `chia/_tests/wallet/nft_wallet/test_nft_royalty.py:42-44`).
4. Because the computed royalty is `0`, `nft_wallet.py:1019-1033` omits the trade price from `trade_prices_list`, and the offer settles fully with zero royalty paid to the NFT creator, despite the NFT having a nonzero configured `royalty_percentage`.
5. Repeating this with many small trade legs allows evading royalty entirely on an arbitrarily large aggregate purchase.

### Citations

**File:** chia/wallet/nft_wallet/nft_wallet.py (L64-75)
```python
MAX_ROYALTY_BASIS_POINTS = 10000


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

**File:** chia/_tests/wallet/nft_wallet/test_nft_royalty.py (L42-44)
```python
def test_small_amount_truncates_to_zero() -> None:
    result = compute_royalty_amount(offered_amount=-50, royalty_split=1, percentage=100)
    assert result == uint64(0)
```

**File:** chia/wallet/nft_wallet/transfer_program_puzzle.py (L22-28)
```python
def puzzle_for_transfer_program(launcher_id: bytes32, royalty_puzzle_hash: bytes32, percentage: uint16) -> Program:
    singleton_struct = Program.to((SINGLETON_MOD_HASH, (launcher_id, SINGLETON_LAUNCHER_HASH)))
    return NFT_TRANSFER_PROGRAM_DEFAULT.curry(
        singleton_struct,
        royalty_puzzle_hash,
        percentage,
    )
```
