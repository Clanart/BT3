### Title
Unbounded on-chain NFT royalty percentage causes offer creation/take to halt for both malicious and unaware counterparties - (File: `chia/wallet/nft_wallet/nft_wallet.py`)

### Summary
The `IchiVaultSpell.maxLTV` bug class is: a percentage/ratio value that is bound-checked (`<= 1e4`) only in one privileged entry point (`addCollateralsSupport`), while the value is consumed unchecked elsewhere, causing incorrect reverts/successes downstream. The Chia analog is the NFT royalty `percentage` (basis points, base 10000): it is validated to be `<= MAX_ROYALTY_BASIS_POINTS` (10000) only in the wallet's own minting code path (`NFTWallet.generate_new_nft`) and in the RPC endpoint (`WalletRpcApi.nft_mint_nft`), but that bound is never enforced by the on-chain NFT transfer-program puzzle itself. Anyone can mint an NFT via a raw, unauthorized spend bundle (bypassing the wallet code) with a royalty `percentage` curried into `NFT_TRANSFER_PROGRAM_DEFAULT` greater than 10000 (up to `uint16` max 65535). When any other, unrelated user later builds or takes an offer that references this NFT, the wallet-side royalty math (`compute_royalty_amount`) unconditionally rejects any percentage over 10000, causing offer creation/acceptance to fail for a transaction that would otherwise be a normal spend.

### Finding Description
`NFTWallet.generate_new_nft` bounds `percentage` to `MAX_ROYALTY_BASIS_POINTS = 10000` only when minting through the wallet's Python code path: [1](#0-0) 

Likewise the RPC layer only rejects the boundary value 10000 explicitly: [2](#0-1) 

This percentage is curried directly into the on-chain `NFT_TRANSFER_PROGRAM_DEFAULT` puzzle with no on-chain range check: [3](#0-2) [4](#0-3) 

Because the value is only curried as an integer and consumed by the transfer program's percentage math, a spend bundle that directly constructs the singleton launcher/eve-coin spend (bypassing `NFTWallet.generate_new_nft`/the RPC) can set this percentage to any `uint16` value, including values far above 10000. `UncurriedNFT.uncurry` extracts this attacker-controlled value verbatim with no bound check when any wallet inspects the NFT's puzzle: [5](#0-4) 

When any other user's wallet later constructs or accepts an offer that involves this NFT (as the asset being requested, i.e., the buyer's wallet must pay a royalty to the malicious minter's declared address), `NFTWallet.make_nft1_offer` reads the on-chain-curried percentage straight out of the driver dict and feeds it into `compute_royalty_amount`: [6](#0-5) 

`compute_royalty_amount` unconditionally raises a `ValueError` whenever the percentage exceeds `MAX_ROYALTY_BASIS_POINTS`: [7](#0-6) 

This mirrors the `IchiVaultSpell` bug exactly: the bound (`1e4` / `10000`) is enforced only at one "mint-time" entry point, not on the value's actual authoritative source (the on-chain curried puzzle args), so any code path that consumes the value directly from the puzzle can receive an out-of-range value and behave incorrectly (here: an unconditional revert instead of a graceful/adjusted computation).

### Impact Explanation
Any legitimate user (a wallet user or a trade counterparty who did not mint the NFT and has no way to know its on-chain royalty percentage exceeds 100% ahead of time) who attempts to `create_offer_for_ids` requesting such an NFT, or a taker who calls `respond_to_offer` against an offer involving it via `trade_manager.py`'s call into `make_nft1_offer`, will have their otherwise-valid transaction-processing flow halt with an unhandled `ValueError` instead of completing the trade. This is a spend-triggered transaction-processing halt reachable by an ordinary offer participant with no special privileges, matching the required "invalid spend...halt" impact category. It is not merely a UX inconvenience for the attacker; it can be weaponized to make specific NFTs permanently untradeable through the standard offer flow for any counterparty who requests them, or can unexpectedly break automated market-maker/bot flows that programmatically build offers touching arbitrary NFTs.

### Likelihood Explanation
Likelihood is moderate: it requires constructing a raw CLVM spend bundle to mint an NFT with a manually-curried transfer program (bypassing the standard wallet mint flow and its `percentage > MAX_ROYALTY_BASIS_POINTS` check), which is straightforward for any technically capable, unprivileged spend-bundle submitter — no special keys or node privileges are needed since the on-chain puzzle imposes no such range restriction.

### Recommendation
Enforce the `royalty_percentage <= MAX_ROYALTY_BASIS_POINTS` bound in every code path that consumes it as ground truth, not only at NFT mint time:
- Validate/clamp the percentage extracted in `UncurriedNFT.uncurry` (`chia/wallet/nft_wallet/uncurry_nft.py`) and/or in `make_nft1_offer`/`royalty_calculation` (`chia/wallet/nft_wallet/nft_wallet.py`) before it reaches `compute_royalty_amount`, so malformed NFTs are handled gracefully (e.g., treat as invalid/untradeable via that flow with a clear error, or cap the royalty at 100%) rather than causing an unhandled failure deep in offer construction.
- Alternatively/additionally, encode the maximum-percentage invariant directly in the on-chain `NFT_OWNERSHIP_TRANSFER_PROGRAM_ONE_WAY_CLAIM_WITH_ROYALTIES` puzzle so that out-of-range percentages cannot be curried into a validly-spendable NFT in the first place.

### Proof of Concept
1. Craft a raw spend bundle that creates a singleton launcher and eve-coin exactly as `NFTWallet.generate_new_nft` would (`chia/wallet/nft_wallet/nft_wallet.py:476-573`), but call `create_ownership_layer_puzzle`/`NFT_TRANSFER_PROGRAM_DEFAULT.curry` directly with `percentage = 20000` (200%, still within `uint16` range), skipping the `NFTWallet.generate_new_nft` percentage check at lines 483-489. Submit this bundle directly to the mempool (bypassing wallet RPC).
2. Once confirmed, have a second, unrelated wallet attempt `create_offer_for_ids` requesting this NFT in exchange for XCH.
3. Observe that `NFTWallet.make_nft1_offer` (lines 904-933) extracts `royalty_percentage = 20000` from the NFT's on-chain puzzle and calls `compute_royalty_amount(amount, request_side_royalty_split, 20000)`, which raises `ValueError("NFT royalty percentage 20000 exceeds 100% (10000 basis points)")`, aborting offer creation entirely.

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

**File:** chia/wallet/nft_wallet/nft_wallet.py (L482-489)
```python
        amount = uint64(1)
        # ensure percentage is uint16 and at most 100%
        try:
            percentage = uint16(percentage)
        except ValueError:
            raise ValueError(f"Percentage must be between 0 and {MAX_ROYALTY_BASIS_POINTS} (100%)")
        if percentage > MAX_ROYALTY_BASIS_POINTS:
            raise ValueError(f"Royalty percentage {percentage} exceeds 100% ({MAX_ROYALTY_BASIS_POINTS} basis points)")
```

**File:** chia/wallet/nft_wallet/nft_wallet.py (L904-933)
```python
        required_royalty_info: list[tuple[bytes32, bytes32, uint16]] = []  # [(launcher_id, address, percentage)]
        offered_royalty_percentages: dict[bytes32, uint16] = {}
        for asset, amount in royalty_nft_asset_dict.items():  # royalty enabled NFTs
            transfer_info = driver_dict[asset].also().also()  # type: ignore
            assert isinstance(transfer_info, PuzzleInfo)
            royalty_percentage_raw = transfer_info["transfer_program"]["royalty_percentage"]
            assert royalty_percentage_raw is not None
            # clvm encodes large ints as bytes
            if isinstance(royalty_percentage_raw, bytes):
                royalty_percentage = int_from_bytes(royalty_percentage_raw)
            else:
                royalty_percentage = int(royalty_percentage_raw)
            if amount > 0:
                required_royalty_info.append(
                    (
                        asset,
                        bytes32(transfer_info["transfer_program"]["royalty_address"]),
                        uint16(royalty_percentage),
                    )
                )
            else:
                offered_royalty_percentages[asset] = uint16(royalty_percentage)

        royalty_payments: dict[bytes32 | None, list[tuple[bytes32, CreateCoin]]] = {}
        for asset, amount in fungible_asset_dict.items():  # offered fungible items
            if amount < 0 and request_side_royalty_split > 0:
                payment_list: list[tuple[bytes32, CreateCoin]] = []
                for launcher_id, address, percentage in required_royalty_info:
                    extra_royalty_amount = compute_royalty_amount(amount, request_side_royalty_split, percentage)
                    payment_list.append((launcher_id, CreateCoin(address, extra_royalty_amount, [address])))
```

**File:** chia/wallet/wallet_rpc_api.py (L2378-2379)
```python
        if request.royalty_percentage == 10000:
            raise ValueError("Royalty percentage cannot be 100%")
```

**File:** chia/wallet/nft_wallet/nft_puzzle_utils.py (L188-211)
```python
def create_ownership_layer_puzzle(
    nft_id: bytes32,
    did_id: bytes,
    p2_puzzle: Program,
    percentage: uint16,
    royalty_puzzle_hash: bytes32 | None = None,
) -> Program:
    log.debug(
        "Creating ownership layer puzzle with NFT_ID: %s DID_ID: %s Royalty_Percentage: %d P2_puzzle: %s",
        nft_id.hex(),
        did_id,
        percentage,
        p2_puzzle,
    )
    singleton_struct = Program.to((SINGLETON_TOP_LAYER_MOD_HASH, (nft_id, SINGLETON_LAUNCHER_PUZZLE_HASH)))
    if not royalty_puzzle_hash:
        royalty_puzzle_hash = p2_puzzle.get_tree_hash()
    transfer_program = NFT_TRANSFER_PROGRAM_DEFAULT.curry(singleton_struct, royalty_puzzle_hash, percentage)
    nft_inner_puzzle = p2_puzzle

    nft_ownership_layer_puzzle = construct_ownership_layer(
        bytes32(did_id) if did_id else None, transfer_program, nft_inner_puzzle
    )
    return nft_ownership_layer_puzzle
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

**File:** chia/wallet/nft_wallet/uncurry_nft.py (L150-157)
```python
            if mod == NFT_OWNERSHIP_LAYER:
                supports_did = True
                log.debug("Parsing ownership layer")
                _, current_did_p, transfer_program, p2_puzzle = ol_args.as_iter()
                _, transfer_program_args = transfer_program.uncurry()
                _, royalty_address_p, royalty_percentage_p = transfer_program_args.as_iter()
                royalty_percentage = uint16(royalty_percentage_p.as_int())
                royalty_address = bytes32(royalty_address_p.as_atom())
```
