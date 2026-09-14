### Title
DID recovery/backup identities are not cleared on transfer, letting former owners retain resurrection capability over a transferred DID - (File: `chia/wallet/did_wallet/did_wallet.py`)

### Summary
`DIDWallet.transfer_did()` and the DID inner-puzzle derivation helpers (`get_innerpuz_for_new_innerhash`, `get_did_innerpuz`, `puzzle_for_pk`) carry the existing `backup_ids` / `num_of_backup_ids_needed` recovery configuration forward into the new owner's inner puzzle unless the sender explicitly clears it first. This mirrors the reported bug class: an approval/permission structure tied to the asset (here, DID recovery authority) is not reset on ownership transfer, so parties who should lose all rights over the asset after a transfer retain latent control.

### Finding Description
`transfer_did()` builds the new DID inner puzzle hash while re-using the *current* `self.did_info.backup_ids` and `self.did_info.num_of_backup_ids_needed`: [1](#0-0) 

The code comment in `get_innerpuz_for_new_innerhash()` explicitly documents this as a known, opt-in-cleanup design: the recovery list is preserved across transfer, and it is left to the *seller* to remember to clear it before transferring: [2](#0-1) 

The CLI transfer command exposes a `--reset_recovery` flag that defaults to `False` (i.e., recovery info is kept unless the user remembers to pass the flag): [3](#0-2) 

and the recipient-side logic in `WalletStateManager` even logs "DID recovery list was reset by the previous owner" as a special, non-default case, again indicating that keeping the recovery list is the default/expected path rather than an anomaly: [4](#0-3) 

Because DID recovery is designed so that a threshold (`num_of_backup_ids_needed`) of the *backup DIDs* — not the current inner-puzzle p2 key holder — can co-sign a recovery message that re-assigns the DID's owning p2 puzzle, any backup identities configured by a previous owner remain capable of exercising that same recovery authority against the DID after it has been sold/transferred, unless the new owner (or a diligent seller) proactively resets the list. This is functionally identical to the `ApprovalFollowModule` bug: a permission/approval structure bound to the identity of the asset persists across an ownership-changing transfer and is not automatically invalidated by the transfer itself.

### Impact Explanation
If a DID with a non-empty backup/recovery list and `num_of_backup_ids_needed > 0` is sold or transferred to a new owner via `did_transfer_did` (RPC/CLI) without the seller explicitly resetting recovery (which is the default state for both the RPC's `with_recovery_info` semantics and the CLI's `--reset_recovery` default of `False`), the previous owner's designated backup identities retain the ability to trigger a DID recovery spend and redirect the DID singleton's controlling puzzle away from the new legitimate owner. This is a coin/asset-identity takeover path reachable purely through wallet-level actions (a standard DID transfer followed by a standard DID recovery spend), not requiring any malicious peer or protocol-level exploit.

### Likelihood Explanation
Likelihood is conditioned on the DID actually having a non-trivial recovery configuration (`num_of_backup_ids_needed > 0`) at the time of transfer, and on the seller/new owner not explicitly resetting it — both of which are plausible given the documented default behavior (`--reset_recovery` defaults to `False`, and the code comment acknowledges this must be handled manually "in a selling case"). This makes it a realistic, self-inflicted-by-default risk for any DID marketplace/transfer flow, rather than a purely theoretical one, but it is not universal since many DIDs are minted with zero backups.

### Recommendation
Default DID transfers to reset the recovery list (`backup_ids = []`, `num_of_backup_ids_needed = 0`) unless the caller explicitly opts to preserve recovery info, inverting the current default in both `did_transfer_did` RPC and the `DidTransferDidCMD` CLI. Additionally, surface a clear warning/confirmation to the transferring user when a DID being transferred has a non-empty backup list, so recovery authority is not silently carried over to a new owner's benefit (or detriment).

### Proof of Concept
Conceptual sequence (matches the code paths cited above):
1. Owner A mints/holds a DID with `backup_ids = [B]`, `num_of_backup_ids_needed = 1`.
2. Owner A calls `did_transfer_did` (RPC) or `chia wallet did transfer` (CLI) to send the DID to Owner B, without passing `reset_recovery` / without clearing recovery — this is the default path per `chia/cmds/wallet.py:862-864` and `chia/wallet/did_wallet/did_wallet.py:805-837`.
3. The new DID inner puzzle created for Owner B still curries in backup DID `B` and `num_of_backup_ids_needed = 1`, per `did_wallet.py:825-832` and `did_wallet.py:982-992`.
4. Backup identity `B` (controlled by/colluding with former Owner A) later initiates a DID recovery spend against the DID now held by Owner B, using the still-valid backup authority, and can reassign the DID's owning p2 puzzle away from Owner B.

I was not able to fully trace the exact CLVM recovery-message verification logic (`chia/wallet/puzzles/did_innerpuz.clsp` was not found in the indexed content) within the available iterations to confirm the precise on-chain conditions required for a successful recovery spend; this should be verified directly against the DID inner puzzle CLVM source before finalizing severity.

### Citations

**File:** chia/wallet/did_wallet/did_wallet.py (L522-531)
```python
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
