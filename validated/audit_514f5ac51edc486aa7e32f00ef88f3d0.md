## Finding

### Title
Unvalidated DID Puzzle Argument Count Causes Unhandled Unpacking Exception During Wallet Sync - (File: `chia/wallet/did_wallet/did_wallet_puzzles.py`)

### Summary
The CVE describes `gif_out_code` in `libsixel` indexing into a fixed-size array using an attacker-influenced index/count that is never validated against the array bounds, causing an out-of-bounds access. The analogous bug class in this Python codebase is code that assumes a *fixed number* of curried CLVM arguments recovered from an attacker-controlled puzzle reveal, and destructures them with an unchecked positional unpack. When the count doesn't match, Python raises an unhandled `ValueError` ("not enough/too many values to unpack"), crashing the code path that processes the coin — the functional equivalent of an out-of-bounds/invalid-index access causing a fault.

### Finding Description
`match_did_puzzle()` uncurries a puzzle, checks only that the inner mod equals `DID_INNERPUZ_MOD`, and then returns the *raw* argument iterator without validating that exactly 5 arguments are present: [1](#0-0) 

Every caller then destructures this iterator into exactly 5 names with no bounds/length check, e.g. in `WalletStateManager.determine_coin_type()` (invoked while syncing hinted/interesting coin states from the network — a fully unprivileged, attacker-reachable trigger): [2](#0-1) 

The same unguarded 5-way unpack pattern recurs in `DIDWallet.coin_added()`: [3](#0-2) 

and in `manual_did_search()` / `find_lost_did()`: [4](#0-3) [5](#0-4) 

Because `mod == DID_INNERPUZ_MOD` only checks byte-identity of the innermost CLVM bytecode after `uncurry()`, and CLVM `curry()`/`uncurry()` place no constraint linking the *number* of curried arguments to what the mod's chialisp source happens to reference at runtime, an attacker can construct and broadcast a coin whose puzzle reveal is `(a (q . DID_INNERPUZ_MOD) <N-args-curried>)` with `N != 5` (e.g., curry only 2 or 6 arguments). As long as the specific solution/branch exercised by that coin's spend doesn't dereference the missing/extra curried slots, the spend can validate on-chain normally (CLVM only errors when a value is actually accessed, e.g., taking `first`/`rest` of a premature `nil`). Once this coin (or its child) shows up as a hinted/interesting coin during any wallet's sync, the unguarded unpack in `determine_coin_type`/`coin_added`/`manual_did_search`/`find_lost_did` raises `ValueError: not enough values to unpack` (or "too many values to unpack"), an exception that is not caught anywhere in these call paths.

### Impact Explanation
An unprivileged party can craft a spend that is valid at consensus level yet is deliberately shaped to break the wallet's fixed-arity assumption about DID inner puzzles. Any wallet (victim, offer counterparty, or observer) that receives/syncs this coin as a hint or interesting coin hits the unhandled exception inside core sync/receive logic (`determine_coin_type`, `coin_added`), which can abort processing of that coin batch, disrupt wallet sync, and halt further transaction processing for the affected wallet — matching the accepted "spend-triggered transaction-processing halt" impact category. This is a Medium-severity denial-of-service against wallet processing, directly analogous in root cause (unvalidated count/index assumption on attacker data) to the CVE's OOB array access.

### Likelihood Explanation
Likelihood is moderate: constructing a coin whose puzzle mod matches `DID_INNERPUZ_MOD` exactly while currying a different argument count, and getting it spent validly on-chain by choosing a solution path that avoids touching the "wrong" argument, requires CLVM crafting skill but no special privilege, key material, or node access — only a broadcastable spend bundle. No signature bypass is needed since the attacker fully controls the coin/puzzle they create.

### Recommendation
- In `match_did_puzzle()`, validate that `curried_args` contains exactly 5 elements before returning; return `None` (not-a-DID) otherwise, mirroring the defensive `try/except` pattern already used in `UncurriedNFT.uncurry()`.
- Wrap the destructuring unpacks in `determine_coin_type`, `coin_added`, `manual_did_search`, and `find_lost_did` in `try/except (ValueError, TypeError)` and treat malformed puzzles as "not a DID coin" rather than propagating the exception.
- Apply the same audit to other uncurry-and-unpack call sites (`uncurry_innerpuz`, `UncurriedNFT.uncurry`, pool puzzle uncurry helpers) to ensure argument-count mismatches are handled as parsing failures, not crashes.

### Proof of Concept
1. Build a puzzle `P = curry(DID_INNERPUZ_MOD, arg1, arg2)` (only 2 args instead of the expected 5: `p2_puzzle_or_hash, backup_ids_hash, num_of_backup_ids_needed, singleton_struct, metadata`).
2. Wrap `P` as a singleton inner puzzle exactly as `create_singleton_puzzle` does, and construct a solution whose executed branch never dereferences the missing curried slots (achievable because CLVM only faults on actual access of a `nil` value).
3. Spend an odd-amount coin with this puzzle so it appears as a "singleton-shaped, odd amount" coin on chain.
4. Any wallet that syncs and hints on this coin invokes `WalletStateManager.determine_coin_type` → `match_did_puzzle(...)` → `p2_puzzle, recovery_list_hash, num_verification, _, metadata = did_curried_args`, which raises `ValueError: not enough values to unpack (expected 5, got 2)`, propagating out of the sync path uncaught.

### Citations

**File:** chia/wallet/did_wallet/did_wallet_puzzles.py (L175-190)
```python
def match_did_puzzle(mod: Program, curried_args: Program) -> Iterator[Program] | None:
    """
    Given a puzzle test if it's a DID, if it is, return the curried arguments
    :param puzzle: Puzzle
    :return: Curried parameters
    """
    try:
        if mod == SINGLETON_TOP_LAYER_MOD:
            mod, curried_args = curried_args.rest().first().uncurry()
            if mod == DID_INNERPUZ_MOD:
                return curried_args.as_iter()
    except Exception:
        import traceback

        print(f"exception: {traceback.format_exc()}")
    return None
```

**File:** chia/wallet/wallet_state_manager.py (L977-994)
```python
        # Check if the coin is a DID
        did_curried_args = (
            match_did_puzzle(uncurried.mod, Program.to(uncurried.curried_args))
            if uncurried.mod is not None and uncurried.curried_args is not None
            else None
        )
        if did_curried_args is not None and coin_state.coin.amount % 2 == 1:
            assert uncurried.curried_args is not None
            p2_puzzle, recovery_list_hash, num_verification, _, metadata = did_curried_args
            did_data: DIDCoinData = DIDCoinData(
                p2_puzzle,
                bytes32(recovery_list_hash.as_atom()) if recovery_list_hash != Program.NIL else None,
                uint16(num_verification.as_int()),
                uncurried.curried_args[0],
                metadata,
                get_inner_puzzle_from_singleton(coin_spend.puzzle_reveal),
                parent_coin_state,
            )
```

**File:** chia/wallet/wallet_state_manager.py (L2314-2325)
```python
    async def manual_did_search(self, coin_id: bytes32, latest: bool = True) -> ManualDIDSearchResults:
        peer = self.wallet_node.get_full_node_peer()
        coin_spend, coin_state = await self.get_latest_singleton_coin_spend(peer, coin_id, latest)
        uncurried = UnknownPuzzle(known_program=coin_spend.puzzle_reveal)
        curried_args = (
            match_did_puzzle(uncurried.mod, Program.to(uncurried.curried_args))
            if uncurried.mod is not None and uncurried.curried_args is not None
            else None
        )
        if curried_args is None:
            raise ValueError("The coin is not a DID.")
        p2_puzzle, recovery_list_hash, num_verification, _, metadata = curried_args
```

**File:** chia/wallet/wallet_state_manager.py (L2405-2425)
```python
    async def find_lost_did(
        self,
        *,
        coin_id: bytes32,
        sync_scope: WalletSyncScope,
        override_recovery_list_hash: bytes32 | None = None,
        override_num_verification: uint16 | None = None,
        override_metadata: dict[str, str] | None = None,
    ) -> None:
        # Get coin state
        peer = self.wallet_node.get_full_node_peer()
        coin_spend, coin_state = await self.get_latest_singleton_coin_spend(peer, coin_id)
        uncurried = UnknownPuzzle(known_program=coin_spend.puzzle_reveal)
        assert uncurried.curried_args is not None
        singleton_struct = uncurried.curried_args[0]
        curried_args = (
            match_did_puzzle(uncurried.mod, Program.to(uncurried.curried_args)) if uncurried.mod is not None else None
        )
        if curried_args is None:
            raise ValueError("The coin is not a DID.")
        p2_puzzle, recovery_list_hash, num_verification, _, metadata = curried_args
```

**File:** chia/wallet/did_wallet/did_wallet.py (L372-385)
```python
            uncurried = UnknownPuzzle(known_program=coin_spend.puzzle_reveal)
            assert uncurried.mod is not None and uncurried.curried_args is not None
            did_curried_args = match_did_puzzle(uncurried.mod, Program.to(uncurried.curried_args))
            assert did_curried_args is not None
            p2_puzzle, recovery_list_hash, num_verification, _, metadata = did_curried_args
            did_data = DIDCoinData(
                p2_puzzle=p2_puzzle,
                recovery_list_hash=bytes32(recovery_list_hash.as_atom()) if recovery_list_hash != Program.NIL else None,
                num_verification=uint16(num_verification.as_int()),
                singleton_struct=uncurried.curried_args[0],
                metadata=metadata,
                inner_puzzle=get_inner_puzzle_from_singleton(coin_spend.puzzle_reveal),
                coin_state=parent_state,
            )
```
