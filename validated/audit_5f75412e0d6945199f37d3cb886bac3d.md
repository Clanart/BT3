### Title
DID recovery list ("backup_ids") silently persists across DID transfer, letting a seller who never clears it later recover full control of the DID coin from the buyer - (File: chia/wallet/did_wallet/did_wallet.py)

### Summary
Chia's `DIDWallet.transfer_did` and `DIDWallet.get_innerpuz_for_new_innerhash` transfer a DID by re-currying `DID_INNERPUZ_MOD` with the new owner's `p2_puzzle`, but they carry forward `self.did_info.backup_ids` and `num_of_backup_ids_needed` unchanged unless the caller explicitly resets them. [1](#0-0) [2](#0-1)  The code contains an explicit acknowledgment of this: "Note: the recovery list will be kept. In a selling case, the seller should clean the recovery list then transfer to the new owner." [3](#0-2)  Nothing in the transfer path enforces this cleanup, and the buyer has no reliable, surfaced signal that a recovery backdoor was left in place — analogous to the OnChainLab report's persistent fallback-module backdoor surviving an NFT ownership change.

### Finding Description
The DID inner puzzle is curried with `p2_puzzle_or_hash`, `backup_ids_hash`, `num_of_backup_ids_needed`, `singleton_struct`, and `metadata`. [4](#0-3)  When a DID is sold/transferred via `transfer_did`, the new inner puzzle hash is derived with `get_inner_puzhash_by_p2` using `self.did_info.backup_ids` and `self.did_info.num_of_backup_ids_needed` taken straight from the current (seller's) `DIDInfo`, with no clearing step. [1](#0-0)  The same pattern exists in `get_innerpuz_for_new_innerhash`, used for building an inner puzzle for a new owner pubkey, which again preserves `backup_ids`. [2](#0-1) 

This mirrors the report's bug class precisely: a piece of privileged, persistent authorization state (the report's `SelectorConfig`/installed fallback module; here, the DID's `backup_ids`/recovery quorum) is embedded in the on-chain puzzle at install/mint time by the current owner, is never cleared as part of the ownership-transfer code path, and the new owner inherits it without an explicit integrity signal. If a malicious seller lists their own DID (or a colluding party's DID) as a backup ID with `num_of_backup_ids_needed = 1` before selling the DID (directly, or as the DID controlling an NFT via the ownership layer, or as the DID controlling a wallet-observer/authority relationship), the seller retains a `DID_INNERPUZ` "recovery" execution path baked into the puzzle that survives the sale. Buyers inspecting the DID normally only look at "who owns it now" (the current `p2_puzzle`) and have no straightforward way to see that `backup_ids`/`num_of_backup_ids_needed` still authorize a third party to force a puzzle-hash rewrite of the DID coin.

Full recovery mechanics (the CLVM opcode/condition semantics of the DID inner puzzle's alternate spend path that lets listed backup DIDs replace the current `p2_puzzle`) could not be located in the indexed portion of the codebase — searches for a `recovery_spend`/`create_attestment` implementation returned no matches, and the DID_INNERPUZ CLVM source itself is not present in the index. This is a limitation of the indexed context, not evidence the mechanism doesn't exist: the puzzle hash comparisons in `DIDWallet.identify` and `WalletStateManager.find_lost_did` explicitly branch on "DID recovery list was reset by the previous owner" vs. not, confirming the recovery/backup-list state is a first-class, puzzle-level concept that must be actively reset, and is not automatically cleared on transfer. [5](#0-4) [6](#0-5) 

### Impact Explanation
If exploitable via the (unverified-in-index) recovery spend path, a seller could regain unauthorized control of a "sold" DID coin — and, transitively, any NFTs whose ownership layer is bound to that DID, or any protocol that treats DID ownership as an authorization credential (e.g., VC issuance/provider roles) — without needing the buyer's private key. This would constitute unauthorized/unsigned coin (DID singleton) puzzle-hash rewrite and asset takeover, which the scope explicitly calls out as in-scope ("concrete unsigned or unauthorized coin movement ... forged asset identity"). Because I could not confirm the exact CLVM opcode conditions that let a quorum of `backup_ids` unilaterally replace the DID's `p2_puzzle` without any signature from the current owner, I can state the *existence* and *persistence* of the backdoor state with high confidence from the Python driver code, but cannot fully confirm the on-chain enforcement mechanics from the indexed files alone.

### Likelihood Explanation
Exploitation requires: (1) a seller to configure `backup_ids` to include a DID they control with a low `num_of_backup_ids_needed` (fully within the seller's control at mint/setup time, no privilege needed), (2) the seller to sell/transfer the DID via the standard `transfer_did` flow without clearing the recovery list (the default code path does not force clearing), and (3) the buyer not noticing/checking `backup_ids` before or after purchase. This requires no compromise of any node, peer, or key — it's purely a spend-bundle/wallet-flow issue reachable by any DID seller and buyer pair, matching the "unprivileged spend-bundle submitter / wallet user / offer counterparty" reachability bar in scope.

### Recommendation
- Make `transfer_did` (and `get_innerpuz_for_new_innerhash`) clear `backup_ids`/`num_of_backup_ids_needed` (reset the recovery list to empty/nil) by default on ownership transfer, requiring an explicit opt-in flag to preserve them.
- Surface the current `backup_ids`/`num_of_backup_ids_needed` prominently in `did_get_info`/wallet UI/CLI output and in offer-acceptance flows so buyers can verify no recovery backdoor exists before finalizing a purchase.
- Consider having the DID puzzle itself increment a nonce/counter on any recovery-list-preserving inner-puzzle update, so downstream integrations (NFT ownership layer holders, VC issuers) can detect tampering analogous to the OnChainLab report's recommended `state` increment on module install.

### Proof of Concept
A concrete CLVM-level PoC could not be constructed from the indexed files because the DID inner puzzle's recovery-spend CLVM logic (the code path invoked when `backup_ids` members supply attestations) is not present in the retrieved index content. The evidence assembled above is at the Python wallet-driver level:
1. `create_innerpuz`/`get_inner_puzhash_by_p2` show `backup_ids`/`num_of_backup_ids_needed` are curried directly into the DID's on-chain puzzle hash. [4](#0-3) 
2. `DIDWallet.transfer_did` and `get_innerpuz_for_new_innerhash` reuse `self.did_info.backup_ids` unmodified when constructing the new owner's inner puzzle. [1](#0-0) [2](#0-1) 
3. A code comment explicitly documents the risk and places the burden of mitigation on the seller manually clearing the list, with no automatic enforcement. [3](#0-2) 

Given the inability to confirm the exact on-chain recovery-authorization semantics from the available index, I recommend starting a full Devin session with complete repository access (including the DID CLVM source, e.g. `did_innerpuz.clsp`) to trace the recovery-spend condition path and build a working PoC bundle before treating this as fully confirmed exploitable.

### Citations

**File:** chia/wallet/did_wallet/did_wallet.py (L520-531)
```python
            full_puzzle_empty_recovery = create_singleton_puzzle(did_puzzle_empty_recovery, launch_id)
            alt_full_puzzle_empty_recovery = create_singleton_puzzle(alt_did_puzzle_empty_recovery, launch_id)
            if full_puzzle.get_tree_hash() != coin_state.coin.puzzle_hash:
                if full_puzzle_empty_recovery.get_tree_hash() == coin_state.coin.puzzle_hash:
                    did_puzzle = did_puzzle_empty_recovery
                    wallet_state_manager.log.info("DID recovery list was reset by the previous owner.")
                elif alt_full_puzzle_empty_recovery.get_tree_hash() == coin_state.coin.puzzle_hash:
                    did_puzzle = alt_did_puzzle_empty_recovery
                    wallet_state_manager.log.info("DID recovery list was reset by the previous owner.")
                else:
                    wallet_state_manager.log.error("DID puzzle hash doesn't match, please check curried parameters.")
                    return None
```

**File:** chia/wallet/did_wallet/did_wallet.py (L805-837)
```python
    async def transfer_did(
        self,
        new_puzhash: bytes32,
        fee: uint64,
        action_scope: WalletActionScope,
        extra_conditions: tuple[Condition, ...] = tuple(),
    ) -> None:
        """
        Transfer the current DID to another owner
        :param new_puzhash: New owner's p2_puzzle
        :param fee: Transaction fee
        :return: Spend bundle
        """
        assert self.did_info.current_inner is not None
        assert self.did_info.origin_coin is not None
        coin = await self.get_coin()
        backup_ids = []
        backup_required = uint64(0)
        backup_ids = self.did_info.backup_ids
        backup_required = self.did_info.num_of_backup_ids_needed
        new_did_puzhash = did_wallet_puzzles.get_inner_puzhash_by_p2(
            p2_puzhash=new_puzhash,
            recovery_list=backup_ids,
            num_of_backup_ids_needed=backup_required,
            launcher_id=self.did_info.origin_coin.name(),
            metadata=did_wallet_puzzles.metadata_to_program(json.loads(self.did_info.metadata)),
            recovery_list_hash=self.reset_recovery_list(),
        )
        p2_solution = self.standard_wallet.make_solution(
            primaries=[CreateCoin(new_did_puzhash, uint64(coin.amount), [new_puzhash])],
            conditions=(*extra_conditions, CreateCoinAnnouncement(coin.name())),
        )
        innersol = Program.to([2, p2_solution, [], [], [], self.did_info.backup_ids])
```

**File:** chia/wallet/did_wallet/did_wallet.py (L976-992)
```python
    async def get_innerpuz_for_new_innerhash(self, pubkey: G1Element) -> Program:
        """
        Get the inner puzzle for a new owner
        :param pubkey: Pubkey
        :return: Inner puzzle
        """
        # Note: the recovery list will be kept.
        # In a selling case, the seller should clean the recovery list then transfer to the new owner.
        assert self.did_info.origin_coin is not None
        return did_wallet_puzzles.create_innerpuz(
            p2_puzzle_or_hash=puzzle_for_pk(pubkey),
            recovery_list=self.did_info.backup_ids,
            num_of_backup_ids_needed=uint64(self.did_info.num_of_backup_ids_needed),
            launcher_id=self.did_info.origin_coin.name(),
            metadata=did_wallet_puzzles.metadata_to_program(json.loads(self.did_info.metadata)),
            recovery_list_hash=self.reset_recovery_list(),
        )
```

**File:** chia/wallet/did_wallet/did_wallet_puzzles.py (L37-63)
```python
def create_innerpuz(
    p2_puzzle_or_hash: Program | bytes32,
    recovery_list: list[bytes32],
    num_of_backup_ids_needed: uint64,
    launcher_id: bytes32,
    metadata: Program = Program.to([]),
    recovery_list_hash: Program | None = None,
) -> Program:
    """
    Create DID inner puzzle
    :param p2_puzzle_or_hash: Standard P2 puzzle or hash
    :param recovery_list: A list of DIDs used for the recovery
    :param num_of_backup_ids_needed: Need how many DIDs for the recovery
    :param launcher_id: ID of the launch coin
    :param metadata: DID customized metadata
    :param recovery_list_hash: Recovery list hash
    :return: DID inner puzzle
    Note: Receiving a standard P2 puzzle hash wouldn't calculate a valid puzzle, but
    that can be useful if calling `.get_tree_hash_precalc()` on it.
    """
    backup_ids_hash: Program | bytes32 = Program.to(recovery_list).get_tree_hash()
    if recovery_list_hash is not None:
        backup_ids_hash = recovery_list_hash
    singleton_struct = Program.to((SINGLETON_TOP_LAYER_MOD_HASH, (launcher_id, SINGLETON_LAUNCHER_PUZZLE_HASH)))
    return DID_INNERPUZ_MOD.curry(
        p2_puzzle_or_hash, backup_ids_hash, num_of_backup_ids_needed, singleton_struct, metadata
    )
```

**File:** chia/wallet/wallet_state_manager.py (L2472-2496)
```python
            full_puzzle_empty_recovery = create_singleton_puzzle(did_puzzle_empty_recovery, launcher_id)
            if full_puzzle.get_tree_hash() != coin_state.coin.puzzle_hash:
                # It's unclear whether this path is ever reached, and there is no coverage in the DID wallet tests
                if full_puzzle_empty_recovery.get_tree_hash() == coin_state.coin.puzzle_hash:
                    did_puzzle = did_puzzle_empty_recovery
                elif (
                    did_wallet is not None
                    and did_wallet.did_info.current_inner is not None
                    and create_singleton_puzzle(did_wallet.did_info.current_inner, launcher_id).get_tree_hash()
                    == coin_state.coin.puzzle_hash
                ):
                    # Check if the old wallet has the inner puzzle
                    did_puzzle = did_wallet.did_info.current_inner
                else:
                    # Try override
                    if override_recovery_list_hash is not None:
                        recovery_list_hash = Program.from_bytes(override_recovery_list_hash)
                    if override_num_verification is not None:
                        num_verification_int = override_num_verification
                    if override_metadata is not None:
                        metadata = metadata_to_program(override_metadata)
                    did_puzzle = DID_INNERPUZ_MOD.curry(
                        our_inner_puzzle, recovery_list_hash, num_verification, singleton_struct, metadata
                    )
                    full_puzzle = create_singleton_puzzle(did_puzzle, launcher_id)
```
