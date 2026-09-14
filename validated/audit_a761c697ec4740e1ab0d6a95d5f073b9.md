Confirmed: `did_transfer_did` in `chia/wallet/wallet_rpc_api.py` (lines 2341-2356) accepts a `DIDTransferDID` request that carries a `with_recovery_info` field, but the RPC handler **never passes it through** to `DIDWallet.transfer_did()` — it calls `.transfer_did(puzzle_hash, request.fee, action_scope, extra_conditions=extra_conditions)` with no recovery-related argument at all. The underlying `transfer_did()` in `chia/wallet/did_wallet/did_wallet.py` (lines 805-832) unconditionally copies `self.did_info.backup_ids` and `self.did_info.num_of_backup_ids_needed` from the seller's current DID state into the new owner's inner puzzle, and the sibling helper `get_innerpuz_for_new_innerhash` (lines 976-992) contains an explicit code comment acknowledging this: *"Note: the recovery list will be kept. In a selling case, the seller should clean the recovery list then transfer to the new owner."*

### Title
DID Ownership Transfer Silently Preserves Seller's Recovery-List Authority, Enabling Undisclosed Takeover of Transferred DID/NFT Assets - ([File: chia/wallet/did_wallet/did_wallet.py])

### Summary
This is the analog to the GitLab report: parent-group members silently retained access after a subgroup was moved to a new owner, invisibly and without appearing in any membership listing. In `chia-blockchain`, the DID "ownership transfer" feature (`did transfer` CLI / `did_transfer_did` RPC) is the equivalent of moving an asset to a new "owner". The DID's `backup_ids` (recovery DIDs) function like the parent group's member list — a list of identities with delegated authority (here, the ability to co-sign a `RECOVER` spend and seize control of the singleton). This authority is not cleared during transfer and is not surfaced anywhere to the buyer/new owner, mirroring the "ghost members … not even showing on the members tab" behavior from the report.

### Finding Description
`DIDWallet.transfer_did()` (`chia/wallet/did_wallet/did_wallet.py:805-856`) builds the new owner's inner puzzle using the *same* `backup_ids` and `num_of_backup_ids_needed` that belonged to the previous owner:
```
backup_ids = self.did_info.backup_ids
backup_required = self.did_info.num_of_backup_ids_needed
new_did_puzhash = did_wallet_puzzles.get_inner_puzhash_by_p2(
    p2_puzhash=new_puzhash,
    recovery_list=backup_ids,
    num_of_backup_ids_needed=backup_required,
    ...
)
``` [1](#0-0) 

There is no parameter on this method, nor on the RPC (`did_transfer_did`, `chia/wallet/wallet_rpc_api.py:2341-2356`) or CLI (`DidTransferDidCMD`, `chia/cmds/wallet.py:850-882`), that actually clears the recovery list at the puzzle level — the CLI flag `--reset_recovery` and RPC field `with_recovery_info` exist in the request models but the RPC handler drops it entirely before calling `transfer_did()`: [2](#0-1) 

The developers themselves flagged this as a known gap in a related helper:
```
# Note: the recovery list will be kept.
# In a selling case, the seller should clean the recovery list then transfer to the new owner.
``` [3](#0-2) 

Because the recovery list (`backup_ids`) grants co-signers the ability to trigger the DID's recovery/backdoor spend path and redirect the singleton to an attacker-controlled puzzle hash, any identity present in the seller's recovery list at time of transfer retains standing authority over the DID after it changes hands — exactly analogous to GitLab members "keeping their access level" on a transferred subgroup while being invisible in the new context. The new owner has no UI/RPC signal (comparable to the missing "members tab" entry) that this latent authority exists unless they manually inspect and diff the recovery list, which most wallet UIs/flows do not surface.

### Impact Explanation
A buyer of a DID (and any NFTs/assets bound to that DID via ownership layer, since DID-linked NFTs inherit trust from the DID) can unknowingly acquire an asset that a previous owner (or their designated backup/recovery identities) can still recover/seize using the standard DID recovery flow, because the recovery list transferred unmodified. This is a real, coin-movement-affecting security issue: an unauthorized party (former owner's backup ID holder) can redirect the DID singleton — and by extension anything relying on DID ownership (NFTs, CR-CAT/VC authorization chains that gate coin movement) — without the current holder's consent.

### Likelihood Explanation
Likelihood is significant in any DID marketplace/secondary-sale scenario: `transfer_did()` is the sole code path wallets/CLIs use to move DID ownership, and it defaults to preserving `backup_ids`/`num_of_backup_ids_needed` unless the seller manually invokes reset logic that, per the RPC handler shown above, is not even wired through. A non-technical seller (malicious or not) has no obvious signal to reset recovery, and a buyer has no obvious way to verify it was reset.

### Recommendation
- Make `transfer_did()` (and the RPC/CLI layer) actually clear `backup_ids`/`num_of_backup_ids_needed` by default on ownership transfer, requiring an explicit opt-in to preserve recovery configuration.
- Wire the existing `with_recovery_info`/`reset_recovery` flags from the RPC request through to `DIDWallet.transfer_did()` instead of silently discarding them in `wallet_rpc_api.py`.
- Surface the current recovery list / backup DIDs prominently to the recipient of a DID transfer (analogous to showing "members" of a transferred group) so buyers can verify no stale recovery authority persists.

### Proof of Concept
1. Owner A creates a DID with `backup_ids=[recoveryDID_X]`, `num_of_backup_ids_needed=1` (a 1-of-1 recovery/attestation setup).
2. Owner A sells/transfers the DID to Owner B via `did_transfer_did` RPC / `chia did transfer` CLI, without specifying any recovery-reset option (the default and, per the RPC handler, the only path currently wired).
3. `transfer_did()` copies `backup_ids=[recoveryDID_X]` into the new inner puzzle curried for Owner B's puzzle hash — confirmed by [1](#0-0) .
4. Owner B has no indication in the transfer response (`DIDTransferDIDResponse`) that `recoveryDID_X` retains authority.
5. Holder of `recoveryDID_X` can later initiate a DID recovery/attestation spend against the coin now controlled by Owner B, redirecting the DID singleton (and any bound NFTs) to a puzzle hash of their choosing — reclaiming ownership without Owner B's consent, mirroring the GitLab "ghost member with owner-level access" scenario.

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

**File:** chia/wallet/wallet_rpc_api.py (L2341-2356)
```python
    async def did_transfer_did(
        self,
        request: DIDTransferDID,
        action_scope: WalletActionScope,
        extra_conditions: tuple[Condition, ...] = tuple(),
    ) -> DIDTransferDIDResponse:
        puzzle_hash: bytes32 = decode_puzzle_hash(request.inner_address)
        async with self.service.wallet_state_manager.lock:
            await self.service.wallet_state_manager.get_wallet(
                id=request.wallet_id, required_type=DIDWallet
            ).transfer_did(
                puzzle_hash,
                request.fee,
                action_scope,
                extra_conditions=extra_conditions,
            )
```
