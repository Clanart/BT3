### Title
Insufficient validation of attacker-controlled PlotNFT memo data causes uncaught exception during wallet sync - ([File: chia/pools/plotnft_drivers.py])

### Summary
`PlotNFT.get_next_from_coin_spend()` deserializes a `PuzzleWithRestrictions` memo blob taken directly from a singleton coin's `CREATE_COIN` memo — data fully controlled by whoever creates the singleton spend — and feeds fields from it into `G1Element.from_bytes()`, `bytes32(...)`, and `uint32(...)` without validating lengths/shapes first. Only `GetNextPlotNFTError` is caught by callers; other exception types (`ValueError`, `AssertionError`, etc.) raised by malformed memo bytes propagate uncaught.

### Finding Description
`PuzzleWithRestrictions.from_memo()` [1](#0-0)  parses an attacker-supplied CLVM memo program with only a namespace check (`memo.first() != Program.to(cls.spec_namespace)`), then blindly calls `.at(...)` path traversals and constructs `RestrictionHint`/`MofNHint`/`MemberHint` from arbitrary sub-atoms.

`PlotNFT.get_next_from_coin_spend()` [2](#0-1)  calls this memo parser and then does:
```
pubkey = G1Element.from_bytes(unknown_inner_puzzle.additional_memos.at("f").as_atom())
...
pool_puzzle_hash = bytes32(unknown_inner_puzzle.additional_memos.at("rf").as_atom())
timelock = uint32(unknown_inner_puzzle.additional_memos.at("rrf").as_int())
```
None of these conversions are guarded — `G1Element.from_bytes` on a wrong-length atom, `bytes32()` on a non-32-byte atom, or `uint32()` on an out-of-range/negative int can raise `ValueError` (not `GetNextPlotNFTError`).

The only caller that guards this path, `PlotNFT2Wallet.identify()` (invoked during wallet sync when a new coin's parent matches the singleton-launcher pattern) [3](#0-2) , wraps the call in `try/except GetNextPlotNFTError: pass` only — it does not catch the raw `ValueError`/`AssertionError` that a malformed memo can trigger deeper inside `from_memo()` or the pubkey/hash/timelock conversions.

### Impact Explanation
Any coin owner can create a singleton-launcher-style spend whose child coin carries a `CreateCoin` memo blob shaped to pass the loose namespace check in `PuzzleWithRestrictions.from_memo()` but contain an invalid-length pubkey/hash atom or an out-of-range integer for the timelock field. When any observing wallet (its own or another user's, since sync scans singleton-launcher children broadly for PlotNFT detection) processes this coin during sync, `PlotNFT2Wallet.identify()` raises an uncaught exception instead of being caught as `GetNextPlotNFTError`, propagating up through the wallet's coin-processing pipeline. This mirrors the EFI Boot Guard issue class: untrusted, attacker-crafted "environment"-like data (memo blob) is deserialized and used to build objects without validating shape/length first, resulting in a crash reachable purely by presenting the malformed data (here, via an on-chain spend) to the parsing code — a spend-triggered halt of wallet transaction/coin processing (denial of service against the wallet sync loop), not asset theft or supply inflation.

### Likelihood Explanation
Crafting a spend with a `CreateCoin` output that mimics the launcher-parent/singleton pattern used for PlotNFT detection and includes a memo blob with the correct `CHIP-0043` namespace tag but a malformed pubkey/hash/timelock atom requires only standard CLVM spend construction — no special privileges, and the malicious data need not even belong to a "real" PlotNFT. Likelihood is moderate: it requires understanding of the exact memo-decoding path (namespace tag, `at()` offsets) but no signature forgery or consensus-level access, and it is reachable by any wallet user or spend-bundle submitter.

### Recommendation
Wrap `G1Element.from_bytes`, `bytes32(...)`, `uint32(...)`, and all other type conversions inside `PlotNFT.get_next_from_coin_spend()` and `PuzzleWithRestrictions.from_memo()`/`RestrictionHint.from_program()`/`MofNHint.from_program()`/`MemberHint.from_program()` in defensive `try/except` blocks that re-raise as `GetNextPlotNFTError` (or a similarly narrow, always-caught exception type), matching the existing pattern already used for `ValueError` around `PuzzleWithRestrictions.from_memo` at line 481-483. Additionally, broaden the `except` clause in `PlotNFT2Wallet.identify()` to catch generic parsing failures (`ValueError`, `AssertionError`, `EvalError`) from untrusted memo data, not just `GetNextPlotNFTError`, so malformed on-chain data can never crash wallet sync.

### Proof of Concept
Conceptual PoC (cannot be executed in this ask-only environment, but the code path is directly traceable):
1. Construct a coin spend whose puzzle reveal matches the singleton/launcher pattern checked by `get_next_from_coin_spend` (`singleton.mod == singleton_mod`, `curried_args[0].at("rr") == singleton_launcher_hash`).
2. Have its inner-puzzle solution create a `CREATE_COIN` output whose `memo_blob` is:
```
Program.to((PuzzleWithRestrictions.spec_namespace,
            [0, [], 0, MemberHint(puzhash=bytes32.zeros, memo=Program.to(b"\x01\x02"[:1])).to_program()]))
```
where the "pubkey" memo atom is deliberately not 48 bytes.
3. Broadcast the spend bundle; once confirmed, any wallet syncing and encountering this coin as a potential PlotNFT (via `PlotNFT2Wallet.identify`) calls `PlotNFT.get_next_from_coin_spend`, which calls `G1Element.from_bytes()` on the malformed atom, raising `ValueError` uncaught by the `except GetNextPlotNFTError` clause, propagating out of `identify()` into the wallet sync coroutine.

Existing unit tests already demonstrate the fragile boundary of this exact parsing surface, confirming exactly which malformed inputs are and are not currently guarded: [4](#0-3)  shows only the memo-namespace `ValueError` path is defended, not the downstream pubkey/hash/timelock conversions.

### Citations

**File:** chia/wallet/puzzles/custody/custody_architecture.py (L271-296)
```python
    @classmethod
    def from_memo(cls, memo: Program) -> PuzzleWithRestrictions:
        if memo.atom is not None or memo.first() != Program.to(cls.spec_namespace):
            raise ValueError("Attempting to parse a memo that does not belong to this spec")
        nonce = memo.at("rf")
        restriction_hints_prog = memo.at("rrf")
        further_branching_prog = memo.at("rrrf")
        puzzle_hint_prog = memo.at("rrrrf")
        additional_memos = memo.at("rrrrrf") if memo.at("rrrrr").atom is None else None
        restriction_hints = [RestrictionHint.from_program(hint) for hint in restriction_hints_prog.as_iter()]
        further_branching = further_branching_prog != Program.to(None)
        if further_branching:
            m_of_n_hint = MofNHint.from_program(puzzle_hint_prog)
            puzzle: MIPSComponent = MofN(
                m=m_of_n_hint.m, members=[PuzzleWithRestrictions.from_memo(memo) for memo in m_of_n_hint.member_memos]
            )
        else:
            puzzle_hint = MemberHint.from_program(puzzle_hint_prog)
            puzzle = UnknownMember(puzzle_hint)

        return PuzzleWithRestrictions(
            nonce=nonce.as_int(),
            restrictions=[UnknownRestriction(hint) for hint in restriction_hints],
            puzzle=puzzle,
            additional_memos=additional_memos,
        )
```

**File:** chia/pools/plotnft_drivers.py (L476-502)
```python
        # Finally, we try to look for the memos
        if plotnft_puzzle is None:
            if singleton_create_coin.memo_blob is None:
                raise GetNextPlotNFTError("Invalid memoization of PlotNFT")
            try:
                unknown_inner_puzzle = PuzzleWithRestrictions.from_memo(singleton_create_coin.memo_blob.rest())
            except ValueError:
                raise GetNextPlotNFTError("Invalid memoization of PlotNFT")
            if unknown_inner_puzzle.additional_memos is None:
                raise GetNextPlotNFTError("Invalid memoization of PlotNFT")
            pubkey = G1Element.from_bytes(unknown_inner_puzzle.additional_memos.at("f").as_atom())
            if isinstance(unknown_inner_puzzle.puzzle, MofN):
                pool_puzzle_hash = bytes32(unknown_inner_puzzle.additional_memos.at("rf").as_atom())
                timelock = uint32(unknown_inner_puzzle.additional_memos.at("rrf").as_int())
                pool_memoization = unknown_inner_puzzle.additional_memos.at("rrrf")
                pool_config = PoolConfig(
                    pool_puzzle_hash=pool_puzzle_hash, heightlock=timelock, pool_memoization=pool_memoization
                )
                exiting = (
                    ValidatorStackRestriction(
                        required_wrappers=[Heightlock(timelock), SendMessageBanned()]
                    ).puzzle_hash(nonce=0)
                    in unknown_inner_puzzle.unknown_puzzles
                )
            else:
                pool_config = None
                exiting = False
```

**File:** chia/wallet/plotnft_wallet/plotnft_wallet.py (L526-592)
```python
        try:
            try:
                previous_plotnft = (
                    await wallet_state_manager.plotnft2_store.get_plotnfts(coin_ids=[coin_spend.coin.name()])
                )[0]
            except ValueError:
                try:
                    assert uncurried.curried_args is not None
                    previous_plotnft = await wallet_state_manager.plotnft2_store.get_latest_plotnft(
                        launcher_id=bytes32(uncurried.curried_args[0].at("rf").as_atom())
                    )
                except RuntimeError:
                    previous_plotnft = None
            next_plot_nft = PlotNFT.get_next_from_coin_spend(
                coin_spend=coin_spend,
                genesis_challenge=wallet_state_manager.constants.GENESIS_CHALLENGE,
                pre_uncurry=uncurried,
                previous_plotnft_puzzle=previous_plotnft,
            )
            for id, wallet in wallet_state_manager.wallets.items():
                if isinstance(wallet, PlotNFT2Wallet) and wallet.plotnft_id == next_plot_nft.launcher_id:
                    matched_plotnft_wallet_id = id
                    break
            else:
                matched_plotnft_wallet_id = None
            user_key_is_owned = (
                await wallet_state_manager.puzzle_store.index_for_puzzle_hash(
                    puzzle_hash_for_synthetic_public_key(next_plot_nft.user_config.synthetic_pubkey)
                )
                is not None
            )
            if matched_plotnft_wallet_id is None and (
                coin_spend.coin.parent_coin_info == next_plot_nft.launcher_id or user_key_is_owned
            ):
                matched_plotnft_wallet_id = uint32(max(wallet_state_manager.wallets.keys()) + 1)
                wallet_state_manager.wallets[matched_plotnft_wallet_id] = await PlotNFT2Wallet.create(
                    wallet_state_manager=wallet_state_manager,
                    xch_wallet=wallet_state_manager.main_wallet,
                    wallet_info=WalletInfo(
                        id=matched_plotnft_wallet_id,
                        name=next_plot_nft.launcher_id.hex(),
                        type=uint8(WalletType.PLOTNFT_2),
                        data=next_plot_nft.launcher_id.hex(),
                    ),
                )
            if matched_plotnft_wallet_id is None or not user_key_is_owned:
                wallet_state_manager.log.warning(
                    f"PlotNFT id {next_plot_nft.launcher_id} hinted to but not keyed to wallet"
                )
                if matched_plotnft_wallet_id is not None:
                    plotnft_wallet = wallet_state_manager.wallets[matched_plotnft_wallet_id]
                    assert isinstance(plotnft_wallet, PlotNFT2Wallet)
                    current_plotnft = await plotnft_wallet.get_current_plotnft()
                    current_plotnft_created_height = (
                        await wallet_state_manager.plotnft2_store.get_plotnft_created_height(
                            coin_id=current_plotnft.coin.name()
                        )
                    )
                    if created_height is not None and current_plotnft_created_height < created_height:
                        await plotnft_wallet.delete_self(deleted_at_height=created_height, sync_scope=sync_scope)
            else:
                return (
                    WalletIdentifier(id=matched_plotnft_wallet_id, type=WalletType.PLOTNFT_2),
                    next_plot_nft,
                )
        except GetNextPlotNFTError:
            pass
```

**File:** chia/_tests/pools/test_plotnft_v2_drivers.py (L481-504)
```python
    with pytest.raises(GetNextPlotNFTError, match=re.escape("Invalid memoization of PlotNFT")):
        PlotNFT.get_next_from_coin_spend(
            coin_spend=FAUX_SPEND,
            pre_uncurry=wrap_inner_puz(
                Program.to(
                    (
                        1,
                        [
                            CreateCoin(
                                puzzle_hash=bytes32.zeros,
                                amount=uint64(1),
                                memo_blob=Program.to(
                                    (
                                        bytes32.zeros,
                                        ("not the namespace", [None, [[None, bytes32.zeros, None]], None, None]),
                                    )
                                ),
                            ).to_program()
                        ],
                    )
                )
            ),
            genesis_challenge=bytes32.zeros,
        )
```
