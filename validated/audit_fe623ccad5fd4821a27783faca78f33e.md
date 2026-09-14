### Title
Unhandled `UnicodeDecodeError` on non-UTF8 CR-CAT `ProofsChecker` flags crashes wallet coin-spend processing - ([File: chia/wallet/vc_wallet/cr_cat_drivers.py])

### Summary
`ProofsChecker.from_program` decodes attacker-controlled puzzle-curry bytes as UTF-8 with no error handling, mirroring the CVE-2017-5196 bug class (crash on non-UTF8 input). Since this parser runs on data taken directly from a coin's puzzle reveal during wallet sync of CR-CAT/VC coin spends, a crafted spend can trigger an unhandled exception in wallet processing.

### Finding Description
`ProofsChecker.from_program` decodes each curried flag atom directly with no exception handling: [1](#0-0) 

Compare this to the deliberately defensive handling used for CAT memo decoding elsewhere in the codebase, which wraps `.decode("utf-8", errors="strict")` in a `try/except UnicodeError`: [2](#0-1) 

`ProofsChecker.from_program` is reached from `CRCAT.get_current_from_coin_spend`/`CRCATSpend.from_coin_spend`, which uncurry a `CoinSpend`'s `puzzle_reveal` (an on-chain/mempool-submitted puzzle) to reconstruct the CR-CAT/VC wallet state: [3](#0-2) [4](#0-3) 

The `flags` list is curried into the `PROOF_FLAGS_CHECKER` puzzle by `ProofsChecker.as_program`, and any counterparty who constructs a CR-CAT/VC puzzle (e.g., in an offer, a CAT wallet interaction, or when syncing coin spends observed on-chain) fully controls the bytes of each flag atom: [5](#0-4) 

Because CR-CAT puzzle programs (curried arguments) are arbitrary CLVM atoms, there is no consensus-level requirement that a "flag" atom be valid UTF-8. When a wallet observes a coin spend whose CR-CAT restriction puzzle curries a flag containing invalid UTF-8 bytes and calls `ProofsChecker.from_program` (via `CRCAT.get_current_from_coin_spend`/`CRCATSpend.from_coin_spend`, used across `cr_cat_wallet.py`, `vc_wallet.py`, and `cat_wallet.py` per the reachability search) that call raises an unhandled `UnicodeDecodeError`, which is functionally the same "non-UTF8 causes crash" bug class as CVE-2017-5196.

### Impact Explanation
An unhandled exception in wallet coin-spend / offer-processing code causes a denial of service in the affected wallet process when it encounters or is asked to process a coin spend, offer, or synced CAT/CR-CAT/VC state containing a maliciously crafted `ProofsChecker` puzzle with non-UTF8 flag bytes. This is a spend-triggered transaction-processing halt reachable by any offer counterparty or by any coin visible to the wallet during sync, fitting the "spend-triggered transaction-processing halt" impact criterion.

### Likelihood Explanation
Likelihood is high for any wallet that enables CR-CAT/VC support and processes third-party coin spends (offers, syncing observed coins, trade manager flows) as identified in `chia/wallet/trade_manager.py`, `chia/wallet/vc_wallet/cr_cat_wallet.py`, `chia/wallet/vc_wallet/vc_wallet.py`, and `chia/wallet/cat_wallet/cat_wallet.py`. Constructing the malformed puzzle only requires curry-ing an arbitrary non-UTF8 bytes atom in place of a flag string — a trivial CLVM construction, not requiring any special privilege.

### Recommendation
Wrap the `.decode("utf8")` call in `ProofsChecker.from_program` in a `try/except UnicodeDecodeError` (or equivalent), mirroring the defensive pattern already used elsewhere in the codebase (e.g., in memo decoding), and either skip/ignore malformed flags or raise a caught, non-fatal parsing error that the caller can gracefully reject instead of crashing.

### Proof of Concept
1. Construct a CR-CAT puzzle whose `PROOF_FLAGS_CHECKER` curry argument contains a flag atom with invalid UTF-8 bytes (e.g. a lone continuation byte such as `b"\x80"`), using `PROOF_FLAGS_CHECKER.curry([Program.to((b"\x80", "1"))])` in place of the normal `Program.to((flag, "1"))` in `ProofsChecker.as_program` (see [6](#0-5) ).
2. Embed this puzzle as the CR-layer's `proofs_checker` in a CR-CAT coin (via `construct_cr_layer`) and either offer it to a counterparty wallet or have it appear as a coin spend that a wallet syncs.
3. When the receiving wallet calls `CRCAT.get_current_from_coin_spend` → `ProofsChecker.from_program` on the offending spend, `bytes(...).decode("utf8")` raises an uncaught `UnicodeDecodeError`, crashing/halting that wallet's processing of the spend/offer.

### Citations

**File:** chia/wallet/vc_wallet/cr_cat_drivers.py (L316-335)
```python
    def get_current_from_coin_spend(cls, spend: CoinSpend) -> CRCAT:  # pragma: no cover
        unknown_puzzle: UnknownPuzzle = UnknownPuzzle(known_program=spend.puzzle_reveal)
        assert unknown_puzzle.curried_args is not None
        first_unknown_cr_layer: UnknownPuzzle = UnknownPuzzle(known_program=unknown_puzzle.curried_args[2])
        assert first_unknown_cr_layer.mod is not None
        assert first_unknown_cr_layer.curried_args is not None
        second_unknown_cr_layer: UnknownPuzzle = UnknownPuzzle(known_program=first_unknown_cr_layer.mod)
        assert second_unknown_cr_layer.curried_args is not None
        lineage_proof = LineageProof.from_program(
            Program.from_serialized(spend.solution).at("rf"),
            [LineageProofField.PARENT_NAME, LineageProofField.INNER_PUZZLE_HASH, LineageProofField.AMOUNT],
        )
        return CRCAT(
            spend.coin,
            bytes32(unknown_puzzle.curried_args[1].as_atom()),
            lineage_proof,
            [bytes32(ap.as_atom()) for ap in second_unknown_cr_layer.curried_args[1].as_iter()],
            second_unknown_cr_layer.curried_args[2],
            first_unknown_cr_layer.curried_args[1].get_tree_hash(),
        )
```

**File:** chia/wallet/vc_wallet/cr_cat_drivers.py (L619-632)
```python
    @classmethod
    def from_coin_spend(cls, spend: CoinSpend) -> CRCATSpend:  # pragma: no cover
        inner_puzzle: Program = CRCAT.get_inner_puzzle(UnknownPuzzle(known_program=spend.puzzle_reveal))
        inner_solution: Program = CRCAT.get_inner_solution(Program.from_serialized(spend.solution))
        inner_conditions: Program = inner_puzzle.run(inner_solution)
        return cls(
            CRCAT.get_current_from_coin_spend(spend),
            inner_puzzle,
            inner_solution,
            CRCAT.get_next_from_coin_spend(spend, conditions=inner_conditions),
            Program.from_serialized(spend.solution).at("f").at("rrrrf") == Program.NIL,
            list(inner_conditions.as_iter()),
            Program.from_serialized(spend.solution).at("f").at("f"),
        )
```

**File:** chia/wallet/vc_wallet/cr_cat_drivers.py (L636-659)
```python
@dataclass(frozen=True)
class ProofsChecker(Streamable):
    flags: list[str]

    def as_program(self) -> Program:
        def byte_sort_flags(f1: str, f2: str) -> int:
            return 1 if Program.to([10, (1, f1), (1, f2)]).run([]) == Program.NIL else -1

        return PROOF_FLAGS_CHECKER.curry(
            [
                Program.to((flag, "1"))
                for flag in sorted(
                    self.flags,
                    key=functools.cmp_to_key(byte_sort_flags),
                )
            ]
        )

    @classmethod
    def from_program(cls, unknown_puzzle: UnknownPuzzle) -> ProofsChecker:
        if unknown_puzzle.mod != PROOF_FLAGS_CHECKER or unknown_puzzle.curried_args is None:
            raise ValueError("Puzzle was not a proof checker")  # pragma: no cover

        return cls([flag.at("f").as_atom().decode("utf8") for flag in unknown_puzzle.curried_args[0].as_iter()])
```

**File:** chia/_tests/util/run_block.py (L104-108)
```python
            if len(condition[3]) >= 2:
                try:
                    memo = condition[3][1].decode("utf-8", errors="strict")
                except UnicodeError:
                    pass  # ignore this error which should leave memo as empty string
```
