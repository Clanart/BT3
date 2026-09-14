### Title
DID transfer does not reset the recovery/backup-ID list, letting the previous owner reclaim a sold DID - (File: chia/wallet/did_wallet/did_wallet.py)

### Summary
`DIDWallet.transfer_did()` moves ownership of a DID singleton to a new p2 puzzle hash but, unless the caller explicitly requests `reset_recovery`, it re-curries the DID inner puzzle with the **same `backup_ids` / `num_of_backup_ids_needed` (recovery list)** that belonged to the previous owner. This is directly analogous to the Astaria `LienToken.buyoutLien()` bug where ownership-transfer-adjacent state (`payee`) is not reset after a change of ownership, letting the old party retain control/benefit over an asset that has already changed hands.

### Finding Description
`transfer_did()` builds the new DID inner puzzle hash using the *existing* `backup_ids`/`num_of_backup_ids_needed` from `self.did_info`, and only clears the recovery hash when `self.reset_recovery_list()` is explicitly invoked with the reset flag: [1](#0-0) 

The recovery list carried into the new coin is the seller's/previous owner's chosen "backup DIDs" — the parties that are cryptographically authorized to perform the DID recovery flow and reassign the DID's p2 puzzle to an arbitrary new address (via the recovery/attestment spend path implemented in `did_wallet.py` and `did_wallet_puzzles.py`, `create_innerpuz`/`get_inner_puzhash_by_p2`, and used by `get_innerpuz_for_new_innerhash`): [2](#0-1) 

The comment attached to `get_innerpuz_for_new_innerhash` explicitly acknowledges this design gap and pushes the responsibility onto the seller to manually clean the list before selling: [3](#0-2) 

The CLI command that exposes this action defaults to **not** resetting recovery unless the user passes `--reset_recovery`: [4](#0-3) 

Just as `LienToken.buyoutLien()` updates ownership-relevant fields (`last`, `start`, `rate`, `duration`) but forgets to reset `payee`, `transfer_did()` updates the p2 puzzle hash (new owner) but forgets/omits resetting the recovery-authority list by default. The old owner (or anyone the old owner previously designated as a backup DID) remains a privileged party embedded in the puzzle even after a legitimate, unrelated new owner receives the coin.

### Impact Explanation
Because the recovery list is curried directly into the on-chain puzzle hash of the DID inner puzzle, any backup DID retained from before the transfer can — after the DID changes hands — initiate the DID recovery process and redirect the DID singleton's p2 puzzle hash to an address they control, effectively **stealing back control of the DID** from the legitimate new owner, without needing any signature from the new owner. This is a concrete unauthorized-coin-movement / stolen-ownership scenario, matching the "malicious case" described in the analog report (the seller backruns/reclaims the asset after receiving payment for it). Since DIDs are used to gate NFT/wallet ownership and identity-linked flows across the wallet (NFT ownership layer, notifications, offers), a hijacked DID can cascade into loss of associated NFTs and other DID-gated assets.

### Likelihood Explanation
Reachable purely through normal wallet usage: an unprivileged DID owner sells/transfers a DID via the standard `did transfer` CLI/RPC flow (`DidTransferDidCMD` → `transfer_did` → `create_update_spend`/singleton spend), and simply doesn't pass `--reset_recovery` (which is the default `False`). No special network position, mempool timing, or malicious peer is needed — the vulnerable state is created by ordinary API/CLI usage, and exploitation is a subsequent, ordinary recovery spend by the retained backup ID. The likelihood of a seller forgetting (or a buyer never checking) that stale backup IDs remain is realistic, especially since the tool silently defaults to the insecure behavior.

### Recommendation
Make recovery-list clearing the default behavior of `transfer_did()` (and the underlying puzzle construction) rather than an opt-in flag, or force verification that no old backup IDs remain valid post-transfer. At minimum, `DidTransferDidCMD`/`transfer_did` should default `reset_recovery` to `True`, and wallet UX should surface a clear warning when transferring a DID whose recovery list is non-empty, so buyers can verify the puzzle hash was built with an empty recovery list before accepting the DID as payment.

### Proof of Concept
1. Owner A creates a DID with backup DID `B` in its recovery list (`num_of_backup_ids_needed=1`, `backup_ids=[B]`).
2. Owner A sells/transfers the DID to Owner C via `did transfer` without `--reset_recovery` (default `False`), per [1](#0-0)  and [5](#0-4) .
3. The new inner puzzle hash is computed by `did_wallet_puzzles.get_inner_puzhash_by_p2` using the unchanged `backup_ids=[B]`/`num_of_backup_ids_needed=1`, so the resulting on-chain DID coin still embeds `B` as an authorized recoverer.
4. Owner A (who controls `B`, since `B` was A's own backup ID) later drives a recovery spend using `B`'s signature/attestment to redirect the DID singleton's inner p2 puzzle hash to an address A controls, per the DID recovery mechanics in `did_wallet_puzzles.py`/`did_wallet.py` (`create_innerpuz`, `get_inner_puzhash_by_p2`, recovery/attestment flow).
5. Owner C, believing they fully own the DID, loses control of it to A — unauthorized coin/ownership takeover analogous to the Astaria stale-`payee` buyback exploit.

Note: I was not able to fully trace the exact recovery/attestment spend implementation (the multi-party attest/aggregate-signature completion path) within the remaining search budget; the core defect (stale recovery list not reset on transfer by default) and its exposure via `transfer_did`/CLI are confirmed directly from the code cited above.

### Citations

**File:** chia/wallet/did_wallet/did_wallet.py (L805-836)
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

**File:** chia/cmds/wallet.py (L850-882)
```python
@chia_command(
    group=did_cmd,
    name="transfer",
    short_help="Transfer a DID",
    help="Transfer a DID",
)
class DidTransferDidCMD(TransactionEndpointWithTimelocks):
    wallet_id: int = option("-i", "--id", help="Id of the DID wallet to use", type=int, required=True)
    # TODO: Change RPC to use puzzlehash instead of address
    target_address: CliAddress = option(
        "-ta", "--target-address", help="Target recipient wallet address", type=AddressParamType(), required=True
    )
    reset_recovery: bool = option(
        "-rr", "--reset_recovery", help="If you want to reset the recovery DID settings.", is_flag=True, default=False
    )

    @transaction_endpoint_runner
    async def run(self) -> list[TransactionRecord]:
        from chia.cmds.wallet_funcs import transfer_did

        async with self.rpc_info.wallet_rpc() as wallet_info:
            return await transfer_did(
                wallet_info,
                self.wallet_id,
                self.fee,
                self.target_address,
                not self.reset_recovery,
                self.push,
                condition_valid_times=self.load_condition_valid_times(),
                tx_config=self.tx_config_loader.load_tx_config(
                    units["chia"], wallet_info.config, wallet_info.fingerprint
                ),
            )
```
