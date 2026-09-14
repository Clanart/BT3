## Analysis Result

### Title
Persisted DID Recovery/Backup List Allows Seller to Reclaim a "Sold" DID (and Everything It Owns) After Transfer - (File: `chia/wallet/did_wallet/did_wallet.py`)

### Summary
The Footium bug is a class of "stale privileged actor persists across ownership transfer" — an old owner grants themselves a standing privilege on an escrow/asset, sells the asset, and later exercises that privilege to steal it back. Chia's DID (Decentralized Identifier) singleton has a structurally identical primitive: the `recovery_list` (`backup_ids`) and `num_of_backup_ids_needed` curried into the DID inner puzzle, which act as a set of "recovery operators" that can reclaim control of the DID coin. When a DID is transferred/sold via `transfer_did`, this recovery configuration is carried over unchanged unless the seller explicitly clears it.

### Finding Description
`DIDWallet.transfer_did` builds the new DID inner puzzle hash for the buyer while reusing the seller's existing recovery configuration verbatim: [1](#0-0) 

Note specifically that `backup_ids` and `backup_required` are taken straight from `self.did_info.backup_ids` / `self.did_info.num_of_backup_ids_needed` with no clearing step, and are curried into `new_did_puzhash` that becomes the buyer's new DID puzzle.

This is not accidental — the code and comments elsewhere in the wallet explicitly acknowledge the danger but push the responsibility onto the seller to remember to clean up: [2](#0-1) 

The comment "In a selling case, the seller should clean the recovery list then transfer to the new owner" is the exact analog of the Footium report's issue: a capability that should be revoked before transfer (there, ERC721 operator approval; here, DID recovery/backup authority) is left in place unless the seller manually acts to strip it. If a malicious seller lists their own DID (or a DID/key they control) as a `backup_id` with `num_of_backup_ids_needed = 1`, and never calls `reset_recovery_list`/never clears `backup_ids` before calling `transfer_did`, the resulting DID puzzle handed to the buyer still contains the seller's recovery authority baked into its curried parameters (`DID_INNERPUZ_MOD.curry(p2_puzzle_or_hash, backup_ids_hash, num_of_backup_ids_needed, singleton_struct, metadata)`): [3](#0-2) 

Because the recovery-list hash is committed inside the puzzle hash itself (not something the buyer can silently strip without changing the coin's identity), the buyer receives a DID coin whose puzzle still recognizes the seller's backup identity as an authorized recoverer, exactly mirroring "the operator address remains approved on the escrow after the club (asset) changes hands."

### Impact Explanation
A DID controls not just XCH value on its own coin but also serves as the "owner" for provenant NFTs (ownership layer `current_owner` field) and other assets that check DID identity for authorization (e.g., NFT trades, DataLayer mirrors, credential/VC relationships). If a seller retains recovery rights over a DID they've sold, they can later initiate the puzzle's built-in recovery path (mode-0 spend with backup DID attestation) to reassign the DID's `p2_puzzle` back under their own control, effectively reclaiming the DID coin and, transitively, any assets whose logic trusts "current DID owner" — the same "steal everything after the sale" impact described in the original report.

### Likelihood Explanation
This requires either (a) a malicious DID seller intentionally leaving a self-controlled backup ID in place while selling the DID off-chain/via an offer, or (b) a buyer/wallet failing to verify that a purchased DID's recovery list has been reset to empty before accepting it. Because `transfer_did` does not enforce clearing of `backup_ids`, and the DID puzzle-hash bakes recovery configuration in, a reasonably careful buyer inspecting only the p2/ownership puzzle (and not decoding `backup_ids_hash`) could be misled, and the wallet provides no automatic warning/enforcement at transfer time.

### Recommendation
- In `DIDWallet.transfer_did`, default to clearing `backup_ids`/`num_of_backup_ids_needed` to empty (`reset_recovery_list`-style behavior) unless the caller explicitly opts to preserve recovery info, rather than silently carrying over the seller's recovery configuration.
- Surface a clear warning/verification step to wallets and offer-acceptance code that decodes and displays a DID's `recovery_list_hash`/`num_of_backup_ids_needed` before accepting a DID as part of a trade, so buyers can detect and reject "poisoned" recovery configurations.
- Consider deprecating/disabling the on-chain recovery path entirely for new DIDs (consistent with the `"Recovery options are no longer supported"` validation already added for `CreateNewWallet`), and audit whether existing DID puzzles still honor legacy recovery solutions, closing off this attack surface for both old and newly transferred DIDs.

### Proof of Concept
1. Attacker creates a DID with `backup_ids = [attacker_backup_did]`, `num_of_backup_ids_needed = 1` (a legacy-compatible configuration).
2. Attacker lists/sells this DID (e.g., via an offer) to a buyer. The buyer's wallet only verifies the p2 puzzle hash change and coin amount, not the embedded `backup_ids_hash`.
3. Attacker calls `transfer_did(new_puzhash=buyer_ph, ...)`, which computes `new_did_puzhash` via `get_inner_puzhash_by_p2` while still passing the attacker's original `backup_ids`/`num_of_backup_ids_needed` [4](#0-3) .
4. Buyer receives and confirms the DID coin, believing they now solely control it.
5. Attacker later constructs a recovery/attestation spend using `attacker_backup_did` (satisfying `num_of_backup_ids_needed = 1`) against the DID inner puzzle, reassigning the `p2_puzzle` back to an address they control — reclaiming the DID coin and any assets (e.g., provenant NFTs) that trust this DID as their `owner_did`.

### Citations

**File:** chia/wallet/did_wallet/did_wallet.py (L820-832)
```python
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
