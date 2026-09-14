### Title
DID recovery list (backup IDs) is not cleared on transfer, letting a previous owner recover/steal a DID (and its NFT ownership/royalty control) after selling it - ([File: chia/wallet/did_wallet/did_wallet.py])

### Summary
The reported Footium bug is a class of "stale approval" issue: a previous owner of a transferable asset can pre-register a persistent authorization (an `approve()` on an escrow contract) before selling the asset, and that authorization silently survives the ownership transfer, letting the seller later steal assets from the buyer. Chia has no ERC20/ERC721-style `approve()` mechanism, but the DID (Decentralized Identifier) singleton has a structurally identical primitive: the **recovery list / backup IDs**, which is a set of pre-authorized identities that can co-sign a "recovery" spend to redirect a DID's inner puzzle (and thus its p2 ownership) to a new address, without the current owner's key. This list is curried into the DID puzzle and is **not reset automatically when the DID is transferred** to a new owner.

### Finding Description
A DID's inner puzzle is curried with `recovery_list_hash`/`backup_ids` and `num_of_backup_ids_needed` [1](#0-0) . These backup IDs are other DIDs that, once they collectively provide `num_of_backup_ids_needed` attestations (via `create_recovery_message_puzzle`/`create_spend_for_message`, which uses `AGG_SIG_UNSAFE` announcements) [2](#0-1) , can force the DID's inner puzzle hash to change to point at a puzzle hash the recovering party controls, i.e., regain effective ownership of the DID coin without the current p2 key's signature.

When a DID is sold/transferred via `DIDWallet.transfer_did()`, the code explicitly re-curries the new owner's p2 puzzle hash but **keeps `self.did_info.backup_ids` and `num_of_backup_ids_needed` unchanged** unless the caller separately resets them: [3](#0-2) 

`get_innerpuz_for_new_innerhash()` (used for producing a new-owner inner puzzle) contains an explicit acknowledgment of this exact bug class:
```
# Note: the recovery list will be kept.
# In a selling case, the seller should clean the recovery list then transfer to the new owner.
``` [4](#0-3) 

This is the direct analog of the Footium report: clearing the "approval" (here, the recovery list) is a manual, opt-in step left to the seller rather than an automatic invariant enforced by the transfer path. A malicious seller can:
1. Set `backup_ids` to include an identity they control (or set `num_of_backup_ids_needed` to a threshold achievable by colluding parties they control) while they still own the DID.
2. Sell/transfer the DID to a buyer via `transfer_did()` (or off-chain trade/offer), which does **not** clear the recovery list by default.
3. After the sale, as one of the pre-registered backup IDs, initiate the DID recovery protocol (`create_recovery_message_puzzle`/`create_spend_for_message` + the corresponding recovery spend on the DID inner puzzle) to redirect the DID's ownership (inner puzzle hash) to an address they control — with no signature or consent required from the new (legitimate) owner.

Because DID ownership is used to control NFT ownership/royalty transfer permissions in the NFT ownership layer (`unft.owner_did`, `set_nft_did`, etc.) [5](#0-4) , and because `PuzzleInfo`/driver logic treats DID identity as the asset-control anchor across DID/NFT/offer flows [6](#0-5) , stealing back a sold DID lets the former owner also reclaim control over any NFTs or DID-authorized operations gated by that DID identity — directly mirroring the Footium scenario where the seller reclaims control of assets held by the "escrow"/identity contract after selling the controlling token.

### Impact Explanation
A buyer who purchases a DID (directly, or as part of an NFT/offer flow that assumes DID ownership transfer is final) can have that DID silently recovered/stolen by the previous owner using a pre-planted backup ID, without needing the buyer's private key or consent. Since DID ownership is used as an authorization anchor for NFT DID-set operations and other DID-gated actions, this can cascade into loss of control over NFTs and other assets tied to that DID identity. This is a concrete unauthorized-ownership-transfer / coin-control-theft scenario reachable purely from a spend a previous owner is entitled to construct (they retain their own key and the pre-registered backup key), matching the "Accept" criteria for unsigned/unauthorized coin/asset-control movement.

### Likelihood Explanation
Likelihood is **medium**: it requires the buyer (or their wallet/tooling) to not notice or not proactively reset the recovery list/backup IDs after acquiring a DID — a step that is not enforced anywhere in the transfer/receive code path and is only flagged in an inline code comment, not surfaced to users or wallet UI as a mandatory action. Because `did_recovery_is_nil`/legacy recovery-list handling is explicitly called out as "deprecated" in several places in the code [7](#0-6) , it is plausible that recovery lists are rarely used by mainstream tooling today, which lowers real-world likelihood, but the puzzle-level capability and the unresolved "seller should clean the recovery list" TODO remain live in the current codebase and are exploitable by any seller who chooses to weaponize it.

### Recommendation
- Make `transfer_did()` (and any DID-transferring RPC/CLI path, including offer/trade flows involving DIDs) clear the recovery list (`backup_ids = []`, `num_of_backup_ids_needed = 0`, and reset `recovery_list_hash` to nil) by default unless the new owner explicitly opts to keep it (e.g., a `keep_recovery_list` flag defaulting to `False`).
- Surface a hard warning/blocking check in wallet RPC/CLI (`did_transfer_did`, offer acceptance for DIDs) whenever a DID being received/created has a non-nil recovery list, requiring explicit user confirmation before treating the DID as fully owned.
- Consider deprecating/removing support for creating new non-nil recovery lists in current DID creation flows, since the feature is already flagged as deprecated internally, to shrink this attack surface for new DIDs while leaving backwards compatibility for existing ones behind an explicit warning path.

### Proof of Concept
1. Attacker (seller) creates or already owns a DID and sets `backup_ids = [attacker_controlled_did]`, `num_of_backup_ids_needed = 1` (or curries a puzzle with a recovery list they/colluders control) via the standard DID creation/`did_update_recovery_ids`-style path.
2. Attacker calls `DIDWallet.transfer_did(new_puzhash=buyer_p2_puzzle_hash, ...)` [8](#0-7)  to sell the DID to the buyer (e.g., via a marketplace offer). The resulting inner puzzle re-curries `p2_puzzle_or_hash=new_puzhash` but keeps `recovery_list=backup_ids` unchanged, per `get_innerpuz_for_new_innerhash()` [4](#0-3) .
3. Buyer receives/confirms the DID coin, believing they now solely control it, and may use it to set ownership on NFTs (`set_nft_did`) [5](#0-4)  or other DID-gated actions.
4. Attacker, still holding the pre-registered backup identity key, constructs a recovery message spend (`create_recovery_message_puzzle`/`create_spend_for_message`) [2](#0-1)  satisfying `num_of_backup_ids_needed`, and spends the DID's current coin through the DID inner puzzle's recovery mode to re-curry the p2 puzzle to an address the attacker controls — without any signature from the buyer.
5. The attacker now controls the DID coin's ownership, which can be leveraged to reclaim DID-gated NFT ownership/royalty permissions previously "sold" to the buyer, mirroring the Footium escrow-approval theft exactly.

Note: Producing a fully executable end-to-end CLVM test (curried recovery puzzle hashes, exact solution shapes for `DID_INNERPUZ_MOD` recovery mode) was not completed here due to tool/iteration limits; the control-flow and puzzle-level primitives cited above are sufficient to establish root cause, but a concrete simulator-based PoC spend bundle would need to be built and run in a follow-up session to fully confirm end-to-end mempool/consensus acceptance of the malicious recovery spend against a `DID_INNERPUZ_MOD` puzzle with `num_of_backup_ids_needed` satisfied.

### Citations

**File:** chia/wallet/did_wallet/did_wallet_puzzles.py (L121-136)
```python
def uncurry_innerpuz(puzzle: Program) -> tuple[Program, Program, Program, Program, Program] | None:
    """
    Uncurry a DID inner puzzle
    :param puzzle: DID puzzle
    :return: Curried parameters
    """
    r = puzzle.uncurry()
    if r is None:
        return r
    inner_f, args = r
    if not is_did_innerpuz(inner_f):
        return None

    p2_puzzle, id_list, num_of_backup_ids_needed, singleton_struct, metadata = list(args.as_iter())
    return p2_puzzle, id_list, num_of_backup_ids_needed, singleton_struct, metadata

```

**File:** chia/wallet/did_wallet/did_wallet_puzzles.py (L138-172)
```python
def create_recovery_message_puzzle(recovering_coin_id: bytes32, newpuz: bytes32, pubkey: G1Element) -> Program:
    """
    Create attestment message puzzle
    :param recovering_coin_id: ID of the DID coin needs to recover
    :param newpuz: New wallet puzzle hash
    :param pubkey: New wallet pubkey
    :return: Message puzzle
    """
    puzzle = Program.to(
        (
            1,
            [
                [ConditionOpcode.CREATE_COIN_ANNOUNCEMENT, recovering_coin_id],
                [ConditionOpcode.AGG_SIG_UNSAFE, bytes(pubkey), newpuz],
            ],
        )
    )
    return puzzle


def create_spend_for_message(
    parent_of_message: bytes32, recovering_coin: bytes32, newpuz: bytes32, pubkey: G1Element
) -> CoinSpend:
    """
    Create a CoinSpend for a atestment
    :param parent_of_message: Parent coin ID
    :param recovering_coin: ID of the DID coin needs to recover
    :param newpuz: New wallet puzzle hash
    :param pubkey: New wallet pubkey
    :return: CoinSpend
    """
    puzzle = create_recovery_message_puzzle(recovering_coin, newpuz, pubkey)
    coin = Coin(parent_of_message, puzzle.get_tree_hash(), uint64(0))
    solution = Program.to([])
    return make_spend(coin, puzzle, solution)
```

**File:** chia/wallet/did_wallet/did_wallet.py (L218-227)
```python
        if not did_recovery_is_nil(recovery_list_hash):
            self.log.warning(f"DID {launch_coin.name().hex()} has a recovery list hash which has been deprecated.")
            try:
                for did in inner_solution.rest().rest().rest().rest().rest().as_python():
                    recovery_list.append(bytes32(did[0]))
            except Exception:
                self.log.warning(
                    f"DID {launch_coin.name().hex()} has a recovery list hash but missing a reveal,"
                    " you may need to reset the recovery info."
                )
```

**File:** chia/wallet/did_wallet/did_wallet.py (L805-856)
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
        # full solution is (corehash parent_info my_amount innerpuz_reveal solution)

        full_puzzle: Program = create_singleton_puzzle(
            self.did_info.current_inner,
            self.did_info.origin_coin.name(),
        )
        parent_info = self.get_parent_for_coin(coin)
        assert parent_info is not None
        fullsol = Program.to(
            [
                [
                    parent_info.parent_name,
                    parent_info.inner_puzzle_hash,
                    parent_info.amount,
                ],
                coin.amount,
                innersol,
            ]
        )
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

**File:** chia/wallet/nft_wallet/nft_wallet.py (L1224-1250)
```python
    async def set_nft_did(
        self,
        nft_coin_info: NFTCoinInfo,
        did_id: bytes,
        action_scope: WalletActionScope,
        fee: uint64 = uint64(0),
        extra_conditions: tuple[Condition, ...] = tuple(),
    ) -> None:
        self.log.debug("Setting NFT DID with parameters: nft=%s did=%s", nft_coin_info, did_id)
        unft = UncurriedNFT.uncurry(*nft_coin_info.full_puzzle.uncurry())
        assert unft is not None
        nft_id = unft.singleton_launcher_id
        puzzle_hashes_to_sign = [unft.p2_puzzle.get_tree_hash()]
        did_inner_hash = b""
        if did_id != b"":
            did_inner_hash = await self.get_did_approval_info([nft_id], action_scope, bytes32(did_id))

        await self.generate_signed_transaction(
            [uint64(nft_coin_info.coin.amount)],
            puzzle_hashes_to_sign,
            action_scope,
            fee,
            {nft_coin_info.coin},
            new_owner=did_id,
            new_did_inner_hash=did_inner_hash,
            extra_conditions=extra_conditions,
        )
```

**File:** chia/wallet/trade_manager.py (L958-976)
```python
    def check_for_owner_change_in_drivers(self, puzzle_info: PuzzleInfo, driver_info: PuzzleInfo) -> bool:
        if puzzle_info.check_type(
            [
                AssetType.SINGLETON.value,
                AssetType.METADATA.value,
                AssetType.OWNERSHIP.value,
            ]
        ) and driver_info.check_type(
            [
                AssetType.SINGLETON.value,
                AssetType.METADATA.value,
                AssetType.OWNERSHIP.value,
            ]
        ):
            old_owner = driver_info.also().also().info["owner"]  # type: ignore
            puzzle_info.also().also().info["owner"] = old_owner  # type: ignore
            if driver_info == puzzle_info:
                return True
        return False
```
