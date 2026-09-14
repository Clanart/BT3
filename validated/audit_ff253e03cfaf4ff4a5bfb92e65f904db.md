### Title
Unbounded recursive puzzle-driver matching over attacker-controlled "also" layers can crash a wallet processing an Offer or NFT/CAT coin - (File: chia/wallet/outer_puzzles.py, chia/wallet/cat_wallet/cat_outer_puzzle.py, chia/wallet/nft_wallet/ownership_outer_puzzle.py, chia/wallet/vc_wallet/cr_outer_puzzle.py, chia/wallet/trading/offer.py)

### Summary
CVE-2018-9262 is a Wireshark VLAN-dissector crash caused by unbounded nesting depth of a self-similar structure (nested VLAN tags), fixed by adding an explicit recursion-depth cap. Chia has a structurally analogous pattern: the "outer puzzle driver" abstraction (`PuzzleInfo.also()`/`match_puzzle`/`construct_puzzle`/`get_inner_puzzle`/`get_inner_solution`) recursively walks nested puzzle "layers" (CAT-in-CAT, singleton/ownership/metadata/CR layers, etc.) with no depth limit, driven directly by the structure of an attacker-supplied `puzzle_reveal` (via a received coin/offer) or an attacker-supplied `driver_dict`/`PuzzleInfo` (via an `Offer`).

### Finding Description
`PuzzleInfo.also()` is explicitly documented as "the supported way to do recursion of PuzzleInfos" [1](#0-0) , and every layer driver (`CATOuterPuzzle`, `MetadataOuterPuzzle`, `OwnershipOuterPuzzle`, `SingletonOuterPuzzle`, `CROuterPuzzle`) implements `match`/`construct`/`get_inner_puzzle`/`get_inner_solution` by recursing into `self._match`/`self._construct`/etc. on the `also` sub-`PuzzleInfo` or on the puzzle's inner layer, with no maximum-depth check anywhere in the call chain: [2](#0-1) [3](#0-2) [4](#0-3) 

`match_puzzle` in `chia/wallet/outer_puzzles.py` is the entry point that starts this unbounded recursive walk over the actual on-chain puzzle structure (`UnknownPuzzle`/uncurry), and it is used both by NFT-wallet driver-info reconstruction and by `Offer` parsing in `chia/wallet/trading/offer.py` when a wallet determines what asset types are involved in requested/offered payments [5](#0-4) .

Separately, `PuzzleInfo.also()` itself performs no validation of nesting depth; a `PuzzleInfo`/`driver_dict` supplied as part of an `Offer` (which a taker parses when displaying/accepting an offer, e.g. in `create_offer_for_ids`/`check_for_special_offer_making`/`check_for_final_modifications` in `chia/wallet/trade_manager.py`) can have an arbitrarily deep "also" chain, and code that walks it — e.g. `check_type()` in `PuzzleInfo` [6](#0-5)  or `check_for_special_offer_making`'s chained `.also().also()[...]` accesses [7](#0-6)  — recurses without any bound. Because `PuzzleInfo` is deserialized from JSON-like dictionaries (`from_json_dict`), an attacker who controls an offer file, RPC payload, or a puzzle reveal sent to the victim wallet can construct a chain deep enough to exhaust the Python call stack (`RecursionError`), which is unhandled in these code paths (they only catch narrow exceptions like `ValueError`).

This is directly analogous to the VLAN dissector bug: a self-referential/nested structure parsed without a depth cap, reachable purely by data sent by an untrusted peer (in this case, a spend-bundle/coin/offer counterparty), leading to a crash of the parsing process.

### Impact Explanation
An unhandled `RecursionError` raised deep inside wallet coin-processing (offer parsing, NFT/CAT driver identification, `check_for_special_offer_making`/`check_for_final_modifications` during trade acceptance) can abort the wallet's transaction-processing/sync task. Depending on where it surfaces (inside `WalletStateManager`'s sync loop or `TradeManager`'s offer-acceptance flow), this can produce a spend-triggered halt of wallet transaction processing for the affected wallet, which is one of the explicitly accepted high-impact categories (spend-triggered transaction-processing halt). It does not, by itself, provide coin theft, inflation, or forged identity — the primary damage is denial of service against a wallet process handling a malicious offer or coin.

### Likelihood Explanation
Likelihood is moderate: constructing a deeply nested CAT/ownership/metadata/CR puzzle reveal or `PuzzleInfo` "also" chain requires only client-side data crafting (no signature, no special privilege) and can be delivered via a standard offer file or a coin sent to the victim's puzzle hash. However, exploitability depends on Python's default recursion limit (~1000) being reachable before other resource limits (e.g., CLVM puzzle size/curry cost for on-chain puzzle nesting, or JSON/offer file size for `PuzzleInfo` "also" nesting) are hit; for the pure `PuzzleInfo`/"also" path (not tied to on-chain puzzle cost), there is no consensus-level cost that bounds nesting depth, making it the more practical vector.

### Recommendation
Add an explicit maximum recursion/nesting depth to `PuzzleInfo.also()` and to each outer-puzzle driver's `match`/`construct`/`get_inner_puzzle`/`get_inner_solution` recursive calls, raising a clean `ValueError`/`ValidationError` once a bounded depth (e.g., 20–50 layers) is exceeded, mirroring the Wireshark fix's approach of capping nesting depth. Additionally, wrap offer-parsing and coin-type-identification code paths (`trade_manager.py`, `offer.py`, `wallet_state_manager.determine_coin_type`) so that `RecursionError` is caught and converted into a normal rejection of the malformed offer/coin rather than propagating and potentially crashing the wallet task.

### Proof of Concept
Conceptual (not verified end-to-end due to lack of a runtime environment):
1. Construct a `PuzzleInfo` dict with a very deep "also" chain, e.g. programmatically build `{"type": "CAT", "tail": "0x00...", "also": {"type": "CAT", "tail": "0x00...", "also": { ... nested thousands of times ... }}}`.
2. Embed this `PuzzleInfo` as an asset's driver in an `Offer`'s `driver_dict` (or serialize an on-chain puzzle reveal with equivalently deep CAT-in-CAT/ownership/metadata nesting via `construct_cat_puzzle`/`puzzle_for_ownership_layer` called repeatedly, as shown feasible in `chia/_tests/wallet/cat_wallet/test_cat_outer_puzzle.py` which already demonstrates 2-level nesting via `construct_cat_puzzle(CAT_MOD, tail, cat_puzzle)`).
3. Send this offer/coin to a victim wallet. When the wallet calls `match_puzzle`/`PuzzleInfo.also()`/`check_type()` while parsing the offer or identifying the coin type, the recursive descent through thousands of "also"/inner-puzzle layers exceeds Python's recursion limit, raising an uncaught `RecursionError` in the wallet's sync/trade-processing task.

**Note on confidence**: I was not able to directly trace a full concrete stack overflow in this environment (no code execution available), and I could not fully confirm whether `determine_coin_type` (the main coin-sync path) calls the deeply-recursive `outer_puzzles.match_puzzle` versus only single-level `match_cat_puzzle`/`match_did_puzzle` matchers — evidence found suggests the sync path largely uses non-recursive single-layer matchers, while the recursive `match_puzzle`/`PuzzleInfo.also()` chain is primarily reachable through **offer parsing and NFT driver-dict reconstruction** (`chia/wallet/trading/offer.py`, `chia/wallet/trade_manager.py`, `chia/wallet/nft_wallet/nft_wallet.py`). This narrows the confirmed impact to offer-processing DoS rather than the general coin-sync path.

### Citations

**File:** chia/wallet/puzzle_drivers.py (L21-26)
```python
class PuzzleInfo:
    """
    There are two 'magic' keys in a PuzzleInfo object:
      - 'type' must be an included key (for easy lookup of drivers)
      - 'also' gets its own method as it's the supported way to do recursion of PuzzleInfos
    """
```

**File:** chia/wallet/puzzle_drivers.py (L66-79)
```python
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
```

**File:** chia/wallet/cat_wallet/cat_outer_puzzle.py (L33-63)
```python
    def match(self, puzzle: UnknownPuzzle) -> PuzzleInfo | None:
        args = match_cat_puzzle(puzzle)
        if args is None:
            return None
        _, tail_hash, inner_puzzle = args
        constructor_dict: dict[str, Any] = {
            "type": "CAT",
            "tail": "0x" + tail_hash.as_atom().hex(),
        }
        next_constructor = self._match(UnknownPuzzle(known_program=inner_puzzle))
        if next_constructor is not None:
            constructor_dict["also"] = next_constructor.info
        return PuzzleInfo(constructor_dict)

    def get_inner_puzzle(
        self, constructor: PuzzleInfo, puzzle_reveal: UnknownPuzzle, solution: Program | None = None
    ) -> Program | None:
        args = match_cat_puzzle(puzzle_reveal)
        if args is None:
            raise ValueError("This driver is not for the specified puzzle reveal")
        _, _, inner_puzzle = args
        also = constructor.also()
        if also is not None:
            deep_inner_puzzle: Program | None = self._get_inner_puzzle(
                also,
                UnknownPuzzle(known_program=inner_puzzle),
                solution.first() if solution is not None else None,
            )
            return deep_inner_puzzle
        else:
            return inner_puzzle
```

**File:** chia/wallet/nft_wallet/ownership_outer_puzzle.py (L39-69)
```python
    def match(self, puzzle: UnknownPuzzle) -> PuzzleInfo | None:
        matched, curried_args = match_ownership_layer_puzzle(puzzle)
        if matched:
            _, current_owner, transfer_program, inner_puzzle = curried_args
            owner_bytes: bytes = current_owner.as_python()
            tp_match: PuzzleInfo | None = self._match(UnknownPuzzle(known_program=transfer_program))
            constructor_dict = {
                "type": "ownership",
                "owner": "()" if owner_bytes == b"" else "0x" + owner_bytes.hex(),
                "transfer_program": (disassemble(transfer_program) if tp_match is None else tp_match.info),
            }
            next_constructor = self._match(UnknownPuzzle(known_program=inner_puzzle))
            if next_constructor is not None:
                constructor_dict["also"] = next_constructor.info
            return PuzzleInfo(constructor_dict)
        else:
            return None

    def asset_id(self, constructor: PuzzleInfo) -> bytes32 | None:
        return None

    def construct(self, constructor: PuzzleInfo, inner_puzzle: Program) -> Program:
        also = constructor.also()
        if also is not None:
            inner_puzzle = self._construct(also, inner_puzzle)
        transfer_program_info: PuzzleInfo | Program = constructor["transfer_program"]
        if isinstance(transfer_program_info, Program):
            transfer_program: Program = transfer_program_info
        else:
            transfer_program = self._construct(transfer_program_info, inner_puzzle)
        return puzzle_for_ownership_layer(constructor["owner"], transfer_program, inner_puzzle)
```

**File:** chia/wallet/vc_wallet/cr_outer_puzzle.py (L26-55)
```python
    def match(self, puzzle: UnknownPuzzle) -> PuzzleInfo | None:
        args: tuple[list[bytes32], Program, Program] | None = match_cr_layer(puzzle)
        if args is None:
            return None
        authorized_providers, proofs_checker, inner_puzzle = args
        constructor_dict: dict[str, Any] = {
            "type": "credential restricted",
            "authorized_providers": ["0x" + ap.hex() for ap in authorized_providers],
            "proofs_checker": disassemble(proofs_checker),
        }
        next_constructor = self._match(UnknownPuzzle(known_program=inner_puzzle))
        if next_constructor is not None:
            constructor_dict["also"] = next_constructor.info
        return PuzzleInfo(constructor_dict)

    def get_inner_puzzle(
        self, constructor: PuzzleInfo, puzzle_reveal: UnknownPuzzle, solution: Program | None = None
    ) -> Program | None:
        args: tuple[list[bytes32], Program, Program] | None = match_cr_layer(puzzle_reveal)
        if args is None:
            raise ValueError("This driver is not for the specified puzzle reveal")  # pragma: no cover
        _, _, inner_puzzle = args
        also = constructor.also()
        if also is not None:
            deep_inner_puzzle: Program | None = self._get_inner_puzzle(
                also, UnknownPuzzle(known_program=inner_puzzle), None
            )
            return deep_inner_puzzle
        else:
            return inner_puzzle
```

**File:** chia/wallet/outer_puzzles.py (L49-54)
```python
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
