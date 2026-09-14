### Title
DID recovery list (backup_ids) persists across ownership transfer, letting a seller-controlled backup DID reclaim a sold DID from its new owner - ([File: chia/wallet/did_wallet/did_wallet.py])

### Summary
The Cooler report describes a bug class where a delegated authority set by the current holder of an asset (`delegateVoting`) is not reset when the asset changes hands, letting the previous holder retain influence over an asset the new owner believes they fully control. Chia's DID (Decentralized Identifier) puzzle has a structurally analogous mechanism: a DID has a `recovery_list` (`backup_ids`) of "backup DIDs" that, via an N-of-M attestation, can rewrite the DID's owning p2 puzzle hash independent of the current owner's private key. When a DID is transferred/sold via `DIDWallet.transfer_did()`, this recovery list is carried over to the new owner's inner puzzle unless the seller explicitly clears it.

### Finding Description
`DIDWallet.transfer_did()` builds the new DID inner puzzle for the buyer using `did_wallet_puzzles.get_inner_puzhash_by_p2()`, passing `backup_ids=self.did_info.backup_ids` and `recovery_list_hash=self.reset_recovery_list()`: [1](#0-0) 

`reset_recovery_list()` only returns a value that clears the recovery list when the list is already empty and `num_of_backup_ids_needed == 0`; otherwise the existing `backup_ids` are preserved into the new owner's inner puzzle: [2](#0-1) 

The same pattern - preserving `backup_ids`/`num_of_backup_ids_needed` unless explicitly reset - is used in `get_innerpuz_for_new_innerhash()`, whose own docstring flags the intended-but-optional mitigation: [3](#0-2) 

The recovery mechanism is designed so that if the current owner's key is lost, a sufficient number of "backup DID" holders can jointly attest to a new p2 puzzle hash and reclaim spend authority over the DID coin, without needing a signature from the current owner. This is by design for lost-key recovery, but it means the `backup_ids` a seller previously configured (which can be DIDs the seller themselves controls) remain valid authorities on the DID puzzle after a sale unless the seller (voluntarily and honestly) clears them before transferring. A malicious or careless seller can:
1. Configure `backup_ids` to point at DIDs they control before selling the DID.
2. Sell/transfer the DID to a buyer via `transfer_did()`, which by default preserves the recovery list.
3. After the sale, use the attestation/recovery flow (backup DID signatures) to rewrite the DID's owning p2 puzzle hash back to themselves, taking the DID coin away from the buyer without the buyer's consent or private key.

This mirrors the Cooler finding's root cause exactly: a delegated authority configured by the party who held the asset before transfer is not tied to, and does not automatically follow, the transfer of the asset itself, and persists to affect/harm the new legitimate owner.

### Impact Explanation
If exploited, a previous DID owner (acting as a malicious seller, e.g., in an NFT/offer marketplace context where DID ownership backs profile/minting authority) can regain unauthorized control of a DID coin sold to a buyer, effectively stealing it back after the sale settles. This is a concrete unauthorized coin-movement / theft scenario reachable purely through a normal spend-bundle-based DID transfer plus a subsequent recovery spend — no privileged network position or leaked key is required, only the seller's own retained (and undisclosed) backup key material.

### Likelihood Explanation
This requires the buyer to trust an on-chain DID transfer without independently verifying (or having tooling that automatically resets/warns about) a non-empty `recovery_list_hash`/`num_of_backup_ids_needed` on the received DID. Since `transfer_did()` does not force clearing the recovery list and the wallet only resets it opportunistically when it is already empty, an unsophisticated buyer or a marketplace/offer flow that does not manually invoke a recovery-list reset before completing a purchase is exposed. The wallet CLI does expose a `-rr/--reset_recovery` flag on `did transfer`, but it defaults to `False` (i.e., recovery info is kept by default): [4](#0-3) 
making the vulnerable (non-resetting) path the default behavior rather than an opt-in mistake.

### Recommendation
- Make resetting the recovery list the default behavior of `transfer_did()`/`DidTransferDidCMD`, requiring an explicit opt-in (e.g., `--keep-recovery-info`) to preserve backup IDs across a sale.
- Have wallets/marketplaces surface a clear warning to buyers whenever a DID being purchased/received still has a non-empty `recovery_list_hash` or non-zero `num_of_backup_ids_needed`, since those backup DIDs can reclaim the asset independent of the new owner's key.
- Consider adding validation in offer/DID-transfer flows that requires the recovery list to be empty (or explicitly acknowledged) before a DID is accepted as trade collateral.

### Proof of Concept
1. Attacker creates a DID wallet and sets `backup_ids` to one or more DID IDs they control, with `num_of_backup_ids_needed = 1`.
2. Attacker calls `DidTransferDidCMD`/`transfer_did()` to sell/transfer the DID to a victim, without passing `--reset_recovery` (the default), so `backup_ids` are preserved in the new inner puzzle as shown in `did_wallet.py:820-832`.
3. Victim receives the DID and now controls it via their own p2 puzzle hash, unaware that the attacker's backup DID(s) are still valid recovery authorities.
4. Attacker uses the DID recovery/attestation flow with their still-registered backup DID to produce the N-of-M attestation and spend the victim's DID coin, rewriting the p2 puzzle hash back to an attacker-controlled key — recovering ownership without the victim's signature.

Note: I was unable to locate the compiled DID inner puzzle CLVM source (`did_innerpuz.clsp`) in the indexed codebase to cite the exact on-chain recovery-mode condition logic (e.g., the `AGG_SIG_UNSAFE`/attestation aggregation check) — this may be excluded from the index due to size limits. The wallet-side driver code confirming the recovery-list carry-over and the intent of the mechanism (`create_recovery_message_puzzle`, `create_spend_for_message`) is available: [5](#0-4) 
If exact confirmation of the on-chain CLVM recovery-spend authorization logic is needed, a Devin session with full repository access could locate and inspect the raw `.clsp` puzzle source.

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

**File:** chia/wallet/did_wallet/did_wallet.py (L1017-1032)
```python
    def reset_recovery_list(self) -> Program | None:
        if self.did_info.current_inner is None:
            return None

        uncurried_args = uncurry_innerpuz(self.did_info.current_inner)
        if uncurried_args is None:
            return None

        _, og_recovery_list_hash, _, _, _ = uncurried_args
        if self.did_info.num_of_backup_ids_needed == 0 and not did_recovery_is_nil(og_recovery_list_hash):
            return None

        if len(self.did_info.backup_ids) > 0:
            return None

        return og_recovery_list_hash
```

**File:** chia/cmds/wallet.py (L856-865)
```python
class DidTransferDidCMD(TransactionEndpointWithTimelocks):
    wallet_id: int = option("-i", "--id", help="Id of the DID wallet to use", type=int, required=True)
    # TODO: Change RPC to use puzzlehash instead of address
    target_address: CliAddress = option(
        "-ta", "--target-address", help="Target recipient wallet address", type=AddressParamType(), required=True
    )
    reset_recovery: bool = option(
        "-rr", "--reset_recovery", help="If you want to reset the recovery DID settings.", is_flag=True, default=False
    )

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
