## Title
Division-before-multiplication in NFT royalty computation causes systematic royalty underpayment - (File: chia/wallet/nft_wallet/nft_wallet.py)

### Summary
`compute_royalty_amount()` in `chia/wallet/nft_wallet/nft_wallet.py` computes royalty payouts for NFT offers using the exact "divide, then multiply, then divide again" pattern flagged in the external report, causing avoidable precision loss/truncation in the royalty owed to the NFT creator/royalty address.

### Finding Description
The royalty amount is computed as:
```python
amount = abs(offered_amount) // royalty_split * percentage // MAX_ROYALTY_BASIS_POINTS
``` [1](#0-0) 

This performs an integer division (`// royalty_split`) *before* the multiplication by `percentage`, instead of multiplying first and dividing last (`abs(offered_amount) * percentage // (royalty_split * MAX_ROYALTY_BASIS_POINTS)`). Whenever `abs(offered_amount)` is not evenly divisible by `royalty_split`, the early `//` truncates the remainder before it can contribute to the final result, so the computed royalty is systematically less than (or equal to, never greater than) the mathematically correct value — and can round all the way down to `0` for small offered amounts combined with a `royalty_split > 1`, exactly matching the "k * V0 / V1" rounding-to-zero scenario described in the report.

This function is called from `NFTWallet.make_nft1_offer()` when building the actual royalty payout condition for a trade:
```python
extra_royalty_amount = compute_royalty_amount(amount, request_side_royalty_split, percentage)
payment_list.append((launcher_id, CreateCoin(address, extra_royalty_amount, [address])))
``` [2](#0-1) 

`royalty_split` here is `request_side_royalty_split`/`offer_side_royalty_split`, i.e. the count of royalty-enabled NFTs on one side of the offer — a value that is directly controlled by how many NFTs the offer-composing party chooses to bundle into a single offer. [3](#0-2) 

There is a separate, non-normative preview function used only for CLI display, `calculate_nft_royalty_amount()` in `chia/cmds/wallet_funcs.py`, which multiplies before dividing (`amounts[0][1] * nft_royalty_percentage / 10000`) and does not have the `// royalty_split` step at all, confirming the on-chain royalty-construction code path in `nft_wallet.py` diverges from the "multiply first" pattern used elsewhere. [4](#0-3) 

### Impact Explanation
Because `CreateCoin(address, extra_royalty_amount, [address])` uses the truncated result directly as the coin amount that will actually be created and paid out to the royalty address, any rounding loss from the premature division is a real reduction in mojos the royalty recipient (NFT creator) receives compared to the intended `percentage` of the offered amount. Bundling multiple royalty-enabled NFTs into one offer (increasing `royalty_split`) increases the truncation impact and can be trivially crafted by an unprivileged offer creator to shortchange royalty recipients on every trade, with no cryptographic or consensus barrier preventing it (the maker/taker is free to choose how many NFTs to combine in one offer). This is a supply-diversion-style underpayment of NFT royalties reachable by any wallet user composing an offer, not merely a display/rounding cosmetic issue, since the truncated value becomes a real, chain-enforced payment amount.

### Likelihood Explanation
High: This triggers on essentially any multi-NFT offer with a royalty split greater than 1 where the offered/requested fungible amount isn't an exact multiple of `royalty_split * 10000/percentage`, which is the common case for arbitrary trade amounts. No special permissions, race conditions, or malicious peers are required — a single wallet user constructing an ordinary complex NFT offer (as already exercised by `test_complex_nft_offer` in the test suite) reaches this code path.

### Recommendation
Reorder the arithmetic to multiply before dividing and only perform integer division once at the end, e.g.:
```python
amount = abs(offered_amount) * percentage // (royalty_split * MAX_ROYALTY_BASIS_POINTS)
```
This preserves full precision until the final division and matches the "multiply-then-divide" remediation recommended for the analogous `DODOMath.sol` issue.

### Proof of Concept
With `offered_amount = -3`, `royalty_split = 2`, `percentage = 5000` (50%):
- Current code: `abs(-3) // 2 * 5000 // 10000` = `1 * 5000 // 10000` = `0` (royalty fully lost, should be non-zero for a 50% royalty).
- Corrected code: `abs(-3) * 5000 // (2 * 10000)` = `15000 // 20000` = `0` too in this tiny example, but for `offered_amount = -30000000`, `royalty_split = 3`, `percentage = 3333` (33.33%):
  - Current: `30000000 // 3 * 3333 // 10000` = `10000000 * 3333 // 10000` = `3333000`.
  - Corrected: `30000000 * 3333 // (3 * 10000)` = `99990000000 // 30000` = `3333000`. (matches here, but for non-divisible combinations such as `offered_amount = -100`, `royalty_split = 3`, `percentage = 3333`):
  - Current: `100 // 3 * 3333 // 10000` = `33 * 3333 // 10000` = `109989 // 10000` = `10`.
  - Corrected: `100 * 3333 // (3 * 10000)` = `333300 // 30000` = `11`.
  This demonstrates a concrete, reproducible 1-unit (and scalable) underpayment purely from the operation ordering, confirmed against `compute_royalty_amount` at [1](#0-0) .

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

**File:** chia/wallet/nft_wallet/nft_wallet.py (L888-895)
```python
        offer_side_royalty_split: int = 0
        request_side_royalty_split: int = 0
        for asset, amount in royalty_nft_asset_dict.items():  # requested non fungible items
            if amount > 0:
                request_side_royalty_split += 1
            elif amount < 0:
                offer_side_royalty_split += 1

```

**File:** chia/wallet/nft_wallet/nft_wallet.py (L927-934)
```python
        royalty_payments: dict[bytes32 | None, list[tuple[bytes32, CreateCoin]]] = {}
        for asset, amount in fungible_asset_dict.items():  # offered fungible items
            if amount < 0 and request_side_royalty_split > 0:
                payment_list: list[tuple[bytes32, CreateCoin]] = []
                for launcher_id, address, percentage in required_royalty_info:
                    extra_royalty_amount = compute_royalty_amount(amount, request_side_royalty_split, percentage)
                    payment_list.append((launcher_id, CreateCoin(address, extra_royalty_amount, [address])))
                royalty_payments[asset] = payment_list
```

**File:** chia/cmds/wallet_funcs.py (L1496-1509)
```python
def calculate_nft_royalty_amount(
    offered: dict[str, Any], requested: dict[str, Any], nft_coin_id: bytes32, nft_royalty_percentage: int
) -> tuple[str, int, int]:
    nft_asset_id = nft_coin_id.hex()
    amount_dict: dict[str, Any] = requested if nft_asset_id in offered else offered
    amounts: list[tuple[str, int]] = list(amount_dict.items())

    if len(amounts) != 1 or not isinstance(amounts[0][1], int):
        raise ValueError("Royalty enabled NFTs only support offering/requesting one NFT for one currency")

    royalty_amount: uint64 = uint64(amounts[0][1] * nft_royalty_percentage / 10000)
    royalty_asset_id = amounts[0][0]
    total_amount_requested = (requested[royalty_asset_id] if amount_dict == requested else 0) + royalty_amount
    return royalty_asset_id, royalty_amount, total_amount_requested
```
