### Title
Unbounded Python recursion when parsing PlotNFT custody memos from any on-chain coin spend - ([File: chia/wallet/puzzles/custody/custody_architecture.py])

### Summary
`PuzzleWithRestrictions.from_memo()` recursively reconstructs nested `MofN` custody structures from an untrusted `Program` memo with no depth limit. It is reachable from `PlotNFT.get_next_from_coin_spend()`, which is invoked during normal wallet syncing (`PlotNFT2Wallet.identify()` in `chia/wallet/plotnft_wallet/plotnft_wallet.py`) whenever a coin matching the singleton/plotnft mod appears on chain, using data taken straight from the `CREATE_COIN` memo of that spend's output.

### Finding Description
`from_memo()` is defined recursively: for every `further_branching` `MofN` node it recurses once per member via `PuzzleWithRestrictions.from_memo(memo)` [1](#0-0) . This mirrors the ASN.1 BER indefinite-length recursion bug (CVE-2020-28196) in that a nested/self-similar encoding drives unbounded Python-stack recursion with no explicit depth cap, unlike the deliberately-iterative `sha256_treehash()` used elsewhere in the codebase specifically "to avoid Python recursion limits on deeply nested CLVM" [2](#0-1) .

The memo content is fully attacker-chosen: any spend that creates a coin can attach an arbitrary `memo_blob` list, and `PlotNFT.get_next_from_coin_spend()` parses `singleton_create_coin.memo_blob.rest()` directly with `PuzzleWithRestrictions.from_memo()` as soon as it fails to match a known/previous plotnft puzzle hash [3](#0-2) . This code path is exercised automatically by wallet sync logic in `PlotNFT2Wallet.identify()`, which calls `get_next_from_coin_spend()` for any coin spend whose puzzle uncurries to the plotnft/singleton mod, before any ownership check is performed [4](#0-3) .

Because a `MofN` node can have `m=1, n=1` and nest another `MofN` as its single member, an attacker can construct, at low CLVM cost, a memo encoding a chain of nested `MofN` structures thousands of levels deep. Deserializing this memo drives `from_memo()` to recurse once per level, exhausting the Python call stack.

### Impact Explanation
A successful trigger crashes (or raises an unhandled `RecursionError` in) the victim's wallet process during normal chain sync, since `get_next_from_coin_spend()`'s only guarded exception type is `GetNextPlotNFTError`/`ValueError` [5](#0-4) ; a `RecursionError` is not a subclass of `ValueError` and would propagate out of the sync handler. This is a spend-triggered processing halt reachable by any party who can construct a coin with a crafted puzzle/output that matches the plotnft singleton mod shape — no privileged access, signing authority over the victim's coins, or malicious-peer/network-layer trust is required, since it is purely a data-parsing issue on attacker-supplied program content once the coin appears in a confirmed block or is observed via legitimate DL/coin-state sync.

### Likelihood Explanation
Likelihood is uncertain/moderate. Constructing a nested nesting depth sufficient to overflow Python's default recursion limit (~1000 frames) requires only linear-cost CLVM cons operations to build the memo program, well within block cost limits, and no special key material — but it does require the newly created coin to actually satisfy the singleton-mod/launcher-hash match checks in `get_next_from_coin_spend()` before `from_memo()` is reached [6](#0-5) . Whether an attacker can trivially get their own crafted singleton coin to reach this exact code path in a victim's live wallet sync (versus only their own wallet's `identify()` call on their own coin) was not fully confirmed within the scope of this investigation — this is the main residual uncertainty.

### Recommendation
Add an explicit maximum-depth/maximum-member-count guard to `PuzzleWithRestrictions.from_memo()` and `MofNHint.from_program()`, rejecting memos that exceed a small bounded nesting depth, and ensure any resulting `RecursionError` is caught and converted into a `GetNextPlotNFTError`/`ValueError` in `get_next_from_coin_spend()` so malformed memos fail safely instead of crashing the process.

### Proof of Concept
Conceptually: construct a `CREATE_COIN` memo (as consumed by `PuzzleWithRestrictions.from_memo()`) representing a `MofN(m=1, members=[MofN(m=1, members=[MofN(...)])])` nested to a depth exceeding Python's recursion limit, matching the `spec_namespace`/tuple shape parsed at `chia/wallet/puzzles/custody/custody_architecture.py:272-296`. Feeding this memo through `PlotNFT.get_next_from_coin_spend()` (as done in `chia/_tests/pools/test_plotnft_v2_drivers.py`) should raise `RecursionError` instead of `GetNextPlotNFTError`. Full exploit reachability (getting a victim's live wallet to process an attacker's crafted coin spend through this exact path) was not independently verified against `identify()`'s caller context due to index/tool limits, and would need to be confirmed with a running wallet-sync test harness.

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

**File:** chia/types/blockchain_format/tree_hash.py (L1-7)
```python
"""
This is an implementation of `sha256_treehash`, used to calculate
puzzle hashes in clvm.

This implementation goes to great pains to be non-recursive so we don't
have to worry about blowing out the python stack.
"""
```

**File:** chia/pools/plotnft_drivers.py (L436-441)
```python
        if singleton.mod != cls.singleton_puzzles.singleton_mod or singleton.curried_args is None:
            raise GetNextPlotNFTError("Invalid singleton puzzle for next PlotNFT")
        if singleton.curried_args[0].at("rr") != cls.singleton_puzzles.singleton_launcher_hash:
            raise GetNextPlotNFTError("Invalid singleton launcher for next PlotNFT")

        launcher_id = bytes32(singleton.curried_args[0].at("rf").as_atom())
```

**File:** chia/pools/plotnft_drivers.py (L476-484)
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
