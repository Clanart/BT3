## Finding

### Title
Unbounded recursive puzzle-driver matching in offer parsing enables stack-exhaustion DoS - (File: `chia/wallet/outer_puzzles.py`, `chia/wallet/nft_wallet/singleton_outer_puzzle.py`, `chia/wallet/nft_wallet/metadata_outer_puzzle.py`, `chia/wallet/nft_wallet/ownership_outer_puzzle.py`, `chia/wallet/vc_wallet/cr_outer_puzzle.py`, `chia/wallet/puzzle_drivers.py`)

### Summary
CVE-2023-4233 is a stack-overflow bug class caused by decoding an externally supplied, attacker-controlled field with unbounded/insufficiently-bounded recursive structure. The analogous pattern in this codebase is `match_puzzle()` and the family of "outer puzzle" driver `.match()` implementations, which recursively descend into a CLVM puzzle's curried inner puzzle with no depth limit, driven entirely by the attacker-supplied `puzzle_reveal` of a `CoinSpend` inside an untrusted offer file or spend bundle.

### Finding Description
When a wallet parses an offer (`Offer.from_bech32()` / `Offer.from_bytes()`), it calls `Offer.from_spend_bundle()`, which for every `CoinSpend` invokes: [1](#0-0) 

`match_puzzle()` tries each registered outer-puzzle driver: [2](#0-1) 

Each driver's `.match()` — `SingletonOuterPuzzle`, `MetadataOuterPuzzle`, `OwnershipOuterPuzzle`, `CROuterPuzzle` — uncurries one puzzle layer and then recursively calls `self._match(...)` on the puzzle's inner puzzle, with no maximum-depth guard: [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5) 

Since a `puzzle_reveal` is a fully attacker-controlled CLVM program, an attacker can curry one of the recognized mod hashes (e.g. `NFT_STATE_LAYER_MOD`) around itself thousands of times, producing a puzzle whose `.uncurry()`'d inner puzzle is again a matching layer, again and again. This construction requires no valid signature, VDF, or proof-of-space — only a syntactically well-formed `CoinSpend`/offer blob — because `match_puzzle()` and `.match()` are invoked purely for informational/UI parsing (`offer.summary()`, `get_offer_summary`, `take_offer` preview) before any consensus, cost, or signature validation occurs. `PuzzleInfo.also()`/`check_type()` compound the same recursive pattern once the (attacker-shaped) `info` dict is built: [7](#0-6) 

Once the recursion depth exceeds CPython's default recursion limit, Python raises `RecursionError`, unhandled by any of these call paths, crashing/aborting the wallet operation that is processing the offer (RPC `get_offer_summary`, CLI `take_offer`/`GetOffersCMD`, `check_for_special_offer_making`, `Offer.from_bech32` used broadly across trade flows).

### Impact Explanation
A single maliciously crafted offer/spend-bundle blob, distributed off-chain (as offers normally are — pasted in chat, shared as a file), causes any wallet or `wallet_rpc_api` process that inspects it (`get_offer_summary`, `take_offer`, `create_offer_for_ids` aggregation, `GetOffersCMD`) to crash with an unhandled `RecursionError` during puzzle-driver matching, before any signature or mempool validation takes place. This is a spend/offer-triggered processing halt of the wallet's trade-handling code path, matching the "spend-triggered transaction-processing halt" impact class accepted for this scan. Because offers are exchanged directly between counterparties without any pre-vetting, any wallet user examining an offer is exposed.

### Likelihood Explanation
High likelihood: constructing a deeply, self-similarly curried CLVM puzzle (e.g., nested `NFT_STATE_LAYER_MOD` layers) is straightforward with existing CLVM tooling and requires no cryptographic material, private keys, or network position — only crafting an `Offer`/`CoinSpend` blob. The vulnerable code path (`Offer.from_bech32`/`from_bytes` → `from_spend_bundle` → `match_puzzle` → recursive `.match()`) is reached the moment a wallet user or RPC caller inspects an untrusted offer, which is the normal, expected workflow for trading.

### Recommendation
Add an explicit recursion/nesting depth limit to `match_puzzle()`/driver `.match()` (and to `PuzzleInfo.also()`/`check_type()`), converting excess nesting into a handled `ValueError`/`None` result rather than allowing unbounded Python recursion. Alternatively, rewrite the matching traversal to be iterative (mirroring the non-recursive design already used in `sha256_treehash`) so depth is bounded only by explicit, enforced limits rather than the process call stack.

### Proof of Concept
1. Build a CLVM puzzle `P0` matching `NFT_STATE_LAYER_MOD`'s curried shape (mod hash, metadata, updater hash, inner puzzle).
2. Recursively curry `P0` around itself `N` times (e.g., `N = 5000`), each time using the previous result as the "inner puzzle" argument, so `uncurry()` on any layer yields another matching layer.
3. Embed the resulting puzzle as the `puzzle_reveal` of a dummy `CoinSpend` inside an `Offer`/`WalletSpendBundle`, matching the settlement-payment convention used by `Offer.from_spend_bundle()` (`parent_coin_info == bytes32.zeros`).
4. Serialize with `Offer.to_bech32()` and send the resulting offer string to a victim.
5. Victim calls `take_offer`, `get_offer_summary`, or simply loads the offer (`Offer.from_bech32`) — `match_puzzle()`'s recursive descent through `N` layers raises an unhandled `RecursionError`, crashing the wallet CLI/RPC call that was processing the offer.

### Citations

**File:** chia/wallet/trading/offer.py (L634-645)
```python
    @classmethod
    def from_spend_bundle(cls, bundle: WalletSpendBundle) -> Offer:
        # Because of the `to_spend_bundle` method, we need to parse the dummy CoinSpends as `requested_payments`
        requested_payments: dict[bytes32 | None, list[NotarizedPayment]] = {}
        driver_dict: dict[bytes32, PuzzleInfo] = {}
        leftover_coin_spends: list[CoinSpend] = []
        for coin_spend in bundle.coin_spends:
            driver = match_puzzle(UnknownPuzzle(known_program=coin_spend.puzzle_reveal))
            if driver is not None:
                asset_id = create_asset_id(driver)
                assert asset_id is not None
                driver_dict[asset_id] = driver
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

**File:** chia/wallet/nft_wallet/singleton_outer_puzzle.py (L32-54)
```python
    def match(self, puzzle: UnknownPuzzle) -> PuzzleInfo | None:
        matched, curried_args = match_singleton_puzzle(puzzle)
        if matched:
            singleton_struct, inner_puzzle = curried_args
            pair = singleton_struct.pair
            assert pair is not None
            launcher_struct = pair[1].pair
            assert launcher_struct is not None
            launcher_id = launcher_struct[0].atom
            assert launcher_id is not None
            launcher_ph = launcher_struct[1].atom
            assert launcher_ph is not None
            constructor_dict: dict[str, Any] = {
                "type": "singleton",
                "launcher_id": "0x" + launcher_id.hex(),
                "launcher_ph": "0x" + launcher_ph.hex(),
            }
            next_constructor = self._match(UnknownPuzzle(known_program=inner_puzzle))
            if next_constructor is not None:
                constructor_dict["also"] = next_constructor.info
            return PuzzleInfo(constructor_dict)
        else:
            return None
```

**File:** chia/wallet/nft_wallet/metadata_outer_puzzle.py (L39-53)
```python
    def match(self, puzzle: UnknownPuzzle) -> PuzzleInfo | None:
        matched, curried_args = match_metadata_layer_puzzle(puzzle)
        if matched:
            _, metadata, updater_hash, inner_puzzle = curried_args
            constructor_dict = {
                "type": "metadata",
                "metadata": metadata,
                "updater_hash": "0x" + updater_hash.as_atom().hex(),
            }
            next_constructor = self._match(UnknownPuzzle(known_program=inner_puzzle))
            if next_constructor is not None:
                constructor_dict["also"] = next_constructor.info
            return PuzzleInfo(constructor_dict)
        else:
            return None
```

**File:** chia/wallet/nft_wallet/ownership_outer_puzzle.py (L39-55)
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
```

**File:** chia/wallet/vc_wallet/cr_outer_puzzle.py (L26-39)
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
```

**File:** chia/wallet/puzzle_drivers.py (L60-79)
```python
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
```
