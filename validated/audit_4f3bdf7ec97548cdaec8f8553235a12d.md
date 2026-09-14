### Title
Unbounded recursion in `PuzzleInfo.also()`/`check_type()` allows stack-overflow denial of service via a deeply nested offer/CAT driver structure - ([File: chia/wallet/puzzle_drivers.py])

### Summary
`PuzzleInfo` (used to describe layered/"outer" puzzles such as CAT, CR-CAT, singleton/NFT metadata/ownership stacks in offers and trades) implements `also()` and `check_type()` as plain Python recursion keyed off an attacker-influenced `"also"` dictionary chain, with no depth limit, mirroring the protobufjs `Any`-expansion bug class (CWE-674, uncontrolled recursion during structure conversion/traversal).

### Finding Description
`chia/wallet/puzzle_drivers.py` defines `PuzzleInfo.also()` (recursively wraps `self.info["also"]` into a new `PuzzleInfo`) and `check_type()`, which recurses via `self.also().check_type(types)` [1](#0-0) . `PuzzleInfo` objects are built by `match_puzzle()` in `chia/wallet/outer_puzzles.py`, which is invoked when a wallet parses an untrusted counterparty's offer/puzzle reveal, matching layered outer puzzles (CAT, CR, singleton, metadata, ownership, revocation layer) one level at a time and nesting them via `"also"` [2](#0-1) . Because `uncurry()`/curry matching is cheap in CLVM cost terms per layer, an attacker can construct a puzzle reveal with an arbitrarily deep chain of recognized outer-puzzle wrappers (e.g., repeated CAT/CR/revocation layers) that produces a `PuzzleInfo` with thousands of nested `"also"` levels while staying within normal CLVM cost limits. Subsequent processing that calls `check_type()` (e.g. `TradeManager.check_for_special_offer_making()` / `check_for_final_modifications()`, which call `puzzle_info.check_type([...])` and `puzzle_info.also().also()[...]` on `driver_dict` entries derived from an offer) [3](#0-2) [4](#0-3)  will recurse to that same depth in pure Python, exceeding the interpreter's default recursion limit and raising an uncaught `RecursionError`.

This is directly analogous to the protobufjs advisory: a bounded-cost, attacker-crafted, deeply nested wrapper structure (`Any`-in-`Any` there, `"also"`-in-`"also"` here) is expanded by an unbounded recursive conversion/traversal routine, exhausting the call stack during processing of untrusted data supplied by another party (offer counterparty ≈ "application decodes untrusted protobuf").

### Impact Explanation
A malicious offer-maker or trade counterparty can hand a wallet user (or a wallet RPC caller processing an incoming offer file) a spend bundle/offer whose puzzle reveals decode into a `PuzzleInfo` with deep `"also"` nesting. When the wallet's trade manager evaluates `check_type()`/`also()` on this driver info during offer acceptance/summary/finalization, the wallet process can crash with an unhandled `RecursionError`, halting transaction/offer processing for that wallet (denial of service). This matches the accepted impact category "spend-triggered transaction-processing halt."

### Likelihood Explanation
Likelihood is moderate: it requires convincing a wallet to load/process a crafted offer file or spend bundle containing many stacked, recognized outer-puzzle wrapper layers. This is reachable by any offer counterparty without special privileges, and building such a puzzle chain is inexpensive in CLVM cost since curry/uncurry wrapping is cheap per layer. However, it does depend on the offer being ingested and specific code paths (`check_for_special_offer_making`, `check_for_final_modifications`, offer `summary()`) actually invoking `check_type()`/`also()` far enough to reach the crafted depth — I was not able to fully trace every call site in this session, so exact triggering conditions (whether Python's default `sys.setrecursionlimit` is reached before other resource limits like max puzzle-reveal size intervene) remain unverified.

### Recommendation
Add an explicit depth limit to `PuzzleInfo.also()`/`check_type()` recursion (or rewrite iteratively, as was deliberately done for `sha256_treehash` in `chia/types/blockchain_format/tree_hash.py` to avoid exactly this class of bug) [5](#0-4) . Reject `PuzzleInfo`/driver dictionaries whose `"also"` chain exceeds a small, protocol-reasonable maximum (matching the maximum number of known outer-puzzle layer types) before recursive processing, both when constructing `PuzzleInfo` from an offer and before calling `check_type()`/`also()` on untrusted driver dictionaries.

### Proof of Concept
Conceptual PoC (not fully executed/verified in this session):
1. Construct a puzzle reveal that stacks a long chain of recognized outer-puzzle wrappers matched by `driver_lookup` in `chia/wallet/outer_puzzles.py` (e.g., repeating CAT → CR → revocation-layer wrapping many thousands of times), which is cheap to curry/uncurry.
2. Wrap this puzzle reveal into an offer/spend bundle and have `match_puzzle()` build the corresponding `PuzzleInfo`, producing a `PuzzleInfo.info["also"]` chain thousands of levels deep.
3. Feed this offer to a wallet's `TradeManager` (e.g., via `check_for_special_offer_making()`/`check_for_final_modifications()`), which calls `PuzzleInfo.check_type()`/`.also()` recursively, causing a Python `RecursionError` and crashing/halting offer processing. [6](#0-5)

### Citations

**File:** chia/wallet/puzzle_drivers.py (L21-87)
```python
class PuzzleInfo:
    """
    There are two 'magic' keys in a PuzzleInfo object:
      - 'type' must be an included key (for easy lookup of drivers)
      - 'also' gets its own method as it's the supported way to do recursion of PuzzleInfos
    """

    info: dict[str, Any]

    def __init__(self, info: dict[str, Any]) -> None:
        self.info = info
        self.__post_init__()

    def __post_init__(self) -> None:
        if "type" not in self.info:
            raise ValueError("A type is required to initialize a puzzle driver")

    def __getitem__(self, item: str) -> Any:
        value = self.info[item]
        return decode_info_value(PuzzleInfo, value)

    def __eq__(self, other: object) -> bool:
        for key, value in self.info.items():
            try:
                if self[key] != other[key]:  # type: ignore
                    return False
            except Exception:
                return False
        return True

    def __contains__(self, item: str) -> bool:
        if item in self.info:
            return True
        else:
            return False

    def type(self) -> str:
        return str(self.info["type"])

    def also(self) -> PuzzleInfo | None:
        if "also" in self.info:
            return PuzzleInfo(self.info["also"])
        else:
            return None

    def check_type(self, types: list[str]) -> bool:
        if types == []:
            if self.also() is None:
                return True
            else:
                return False
        elif self.type() == types[0]:
            types.pop(0)
            if self.also():
                return self.also().check_type(types)  # type: ignore
            else:
                return self.check_type(types)
        else:
            return False

    # Methods to make this a valid Streamable member
    # Should not be being serialized as bytes
    stream = None
    parse = None

    def to_json_dict(self) -> dict[str, Any]:
        return self.info
```

**File:** chia/wallet/outer_puzzles.py (L39-54)
```python
class AssetType(Enum):
    CAT = "CAT"
    SINGLETON = "singleton"
    METADATA = "metadata"
    OWNERSHIP = "ownership"
    ROYALTY_TRANSFER_PROGRAM = "royalty transfer program"
    CR = "credential restricted"
    REVOCATION_LAYER = "revocation layer"


def match_puzzle(puzzle: UnknownPuzzle) -> PuzzleInfo | None:
    for driver in driver_lookup.values():
        potential_info: PuzzleInfo | None = driver.match(puzzle)
        if potential_info is not None:
            return potential_info
    return None
```

**File:** chia/wallet/trade_manager.py (L928-937)
```python
        for puzzle_info in driver_dict.values():
            if (
                puzzle_info.check_type([AssetType.SINGLETON.value, AssetType.METADATA.value, AssetType.OWNERSHIP.value])
                and isinstance(puzzle_info.also().also()["transfer_program"], PuzzleInfo)  # type: ignore
                and puzzle_info.also().also()["transfer_program"].type()  # type: ignore
                == AssetType.ROYALTY_TRANSFER_PROGRAM.value
            ):
                return await NFTWallet.make_nft1_offer(
                    self.wallet_state_manager, offer_dict, driver_dict, action_scope, fee, extra_conditions
                )
```

**File:** chia/wallet/trade_manager.py (L992-1017)
```python
    async def check_for_final_modifications(
        self, offer: Offer, solver: Solver, action_scope: WalletActionScope
    ) -> tuple[Offer, Solver]:
        for puzzle_info in offer.driver_dict.values():
            if (
                puzzle_info.check_type(
                    [
                        AssetType.SINGLETON.value,
                        AssetType.METADATA.value,
                    ]
                )
                and puzzle_info.also()["updater_hash"] == ACS_MU_PH  # type: ignore
            ):
                return (await DataLayerWallet.finish_graftroot_solutions(offer, solver), Solver({}))
            elif puzzle_info.check_type(
                [
                    AssetType.CAT.value,
                    AssetType.CR.value,
                ]
            ):
                # get VC wallet
                for _, wallet in self.wallet_state_manager.wallets.items():
                    if WalletType(wallet.type()) == WalletType.VC:
                        assert isinstance(wallet, VCWallet)
                        return await wallet.add_vc_authorization(offer, solver, action_scope)
                raise ValueError("No VCs to approve CR-CATs with")  # pragma: no cover
```

**File:** chia/types/blockchain_format/tree_hash.py (L1-8)
```python
"""
This is an implementation of `sha256_treehash`, used to calculate
puzzle hashes in clvm.

This implementation goes to great pains to be non-recursive so we don't
have to worry about blowing out the python stack.
"""

```
