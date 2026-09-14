## Analysis

The reported Astaria bug class is: a persisted "payee" pointer is not reset when ownership of an asset changes, so future payments continue to be routed to the stale address instead of the new owner. Chia has a structurally analogous flaw in the PlotNFT-v2 wallet's reward payout path.

### Title
Stale `payout_instructions` in `PoolingShareState` is not cleared on PlotNFT ownership transfer, misdirecting self-pooled reward claims to the previous owner - (File: `chia/wallet/plotnft_wallet/plotnft_wallet.py`)

### Summary
`PlotNFT2Wallet.transfer_plotnft()` changes only the on-chain `UserConfig` (new owner's synthetic pubkey) of a PlotNFT singleton, but never updates the wallet-side `PoolingShareState.payout_instructions` field that is later used to compute the actual coin destination for self-pooled reward claims.

### Finding Description
`PlotNFT2Wallet.rewards_claim_puzhash` derives the real payout puzzle hash used to build `CreateCoin` outputs for reward claims directly from the persisted `payout_instructions` field: [1](#0-0) 

This field lives in a `PoolingShareState` entry keyed by `p2_singleton_puzzle_hash`, which is derived only from `plotnft_id`/`launcher_id` — i.e. it is **stable across ownership transfers** of the same singleton: [2](#0-1) 

`transfer_plotnft()` moves ownership by re-currying the PlotNFT with a new `UserConfig` (new owner pubkey/hint) and creates the new singleton coin, but nowhere touches or clears `payout_instructions`: [3](#0-2) 

When wallet sync later observes the transferred coin and it is in (or returns to) self-pooling state, `coin_added()` explicitly re-derives `target_puzzle_hash` **from the stale `payout_instructions` value** instead of resetting `payout_instructions` to the new owner: [4](#0-3) 

Then `claim_rewards()` builds the actual reward payout coin using `self.rewards_claim_puzhash`, i.e., the stale address: [5](#0-4) 

This is the same root-cause pattern as the Astaria report: a persisted "who gets paid" pointer (`payee` in Astaria, `payout_instructions` here) is not invalidated on ownership transfer (`buyoutLien` in Astaria, `transfer_plotnft` here), so `getPayee()`/`rewards_claim_puzhash` keeps resolving to the old owner and diverts payments away from the current legitimate owner.

### Impact Explanation
After a PlotNFT owner transfers the PlotNFT to a new wallet/owner (e.g. selling it or handing it to another key they control) and the PlotNFT is/returns to self-pooling, self-pooled block rewards claimed via `claim_rewards()` are paid to the **previous owner's** puzzle hash rather than the new owner's. This is a concrete redirection of value away from the rightful current owner of the singleton — funds that should belong to the new owner land in the old owner's wallet.

### Likelihood Explanation
This requires only normal wallet usage reachable by any local wallet user: call the `transfer_plotnft` RPC/CLI path to hand a PlotNFT to another wallet, then have the new owner (or anyone syncing that wallet) call `claim_rewards`. No privileged access or malicious peer is needed; it triggers on the ordinary lifecycle transition path (`transfer_plotnft` → `coin_added` → `claim_rewards`), so it is readily reachable in normal operation whenever a self-pooling PlotNFT changes hands.

### Recommendation
When `transfer_plotnft()` re-curries the PlotNFT with a new owner, also reset the corresponding `PoolingShareState.payout_instructions` (and `owner_public_key`/`key_derivation_index`) for that `p2_singleton_puzzle_hash`, or derive `payout_instructions` fresh from the new owner's own wallet state rather than trusting the previously persisted value in `coin_added()`'s self-pooling branch at [6](#0-5) .

### Proof of Concept
1. Owner A creates a self-pooling PlotNFT via `PlotNFT2Wallet.create_new`; `payout_instructions` is set to Owner A's puzzle hash.
2. Owner A calls `transfer_plotnft(target_wallet_fingerprint=<Owner B's fingerprint>)` [7](#0-6) . Ownership (synthetic pubkey/hint) moves to Owner B; `payout_instructions` in `PoolingShareState` is untouched.
3. Farming continues; the PlotNFT accrues pool rewards to its `p2_singleton_puzzle_hash`.
4. On sync, `coin_added()` sees the transferred, still self-pooling PlotNFT and sets `pool_config.target_puzzle_hash = bytes32.from_hexstr(pool_config.payout_instructions)` — Owner A's address [6](#0-5) .
5. Owner B (now the on-chain owner) calls `claim_rewards()`; the payout `CreateCoin` uses `self.rewards_claim_puzhash`, which resolves to Owner A's stale `payout_instructions` [8](#0-7) , sending the claimed reward funds to Owner A instead of Owner B.

### Citations

**File:** chia/wallet/plotnft_wallet/plotnft_wallet.py (L87-90)
```python
    @property
    def p2_singleton_puzzle_hash(self) -> bytes32:
        return RewardPuzzle(singleton_id=self.plotnft_id).puzzle_hash()

```

**File:** chia/wallet/plotnft_wallet/plotnft_wallet.py (L91-96)
```python
    @property
    def rewards_claim_puzhash(self) -> bytes32:
        with PoolingShareState.acquire(
            root_path=self.wallet_state_manager.root_path, p2_singleton_puzzle_hash=self.p2_singleton_puzzle_hash
        ) as pool_config:
            return bytes32.from_hexstr(pool_config.payout_instructions)
```

**File:** chia/wallet/plotnft_wallet/plotnft_wallet.py (L149-226)
```python
    async def claim_rewards(
        self,
        *,
        action_scope: WalletActionScope,
        fee: uint64 = uint64(0),
        extra_conditions: tuple[Condition, ...] = tuple(),
    ) -> None:
        rewards_to_claim = await self.wallet_state_manager.plotnft2_store.get_pool_rewards(plotnft_id=self.plotnft_id)
        if len(rewards_to_claim) == 0:
            raise ValueError("No rewards to claim")
        total_reward_amount = uint64(sum(reward.coin.amount for reward in rewards_to_claim))
        if fee > total_reward_amount:
            raise ValueError("Fee is greater than the total amount of rewards")

        plotnft = await self.get_current_plotnft()
        coin_spends = plotnft.claim_pool_rewards(
            rewards_to_claim=rewards_to_claim,
            reward_delegated_puzzles_and_solutions=[
                DelegatedPuzzleAndSolution(
                    puzzle=self.xch_wallet.make_solution(
                        primaries=[
                            CreateCoin(
                                puzzle_hash=self.rewards_claim_puzhash,
                                amount=uint64(total_reward_amount - fee),
                            ),
                        ],
                        fee=fee,
                        conditions=(*extra_conditions, CreateCoinAnnouncement(b""))
                        if len(rewards_to_claim) > 1
                        else extra_conditions,
                    ).at("rf"),  # strips away to just the delegated puzzle (bit of a hack)
                    solution=Program.to(None),
                )
                if i == 0
                else DelegatedPuzzleAndSolution(
                    puzzle=Program.to(
                        (
                            1,
                            [
                                AssertCoinAnnouncement(
                                    asserted_id=rewards_to_claim[0].coin.name(), asserted_msg=b""
                                ).to_program()
                            ],
                        )
                    ),
                    solution=Program.to(None),
                )
                for i, reward in enumerate(rewards_to_claim)
            ],
        )

        spend_bundle = WalletSpendBundle(coin_spends, G2Element())

        async with action_scope.use() as interface:
            interface.side_effects.transactions.append(
                self.wallet_state_manager.new_outgoing_transaction(
                    wallet_id=self.id(),
                    puzzle_hash=self.rewards_claim_puzhash,
                    amount=total_reward_amount,
                    fee=fee,
                    spend_bundle=spend_bundle,
                    additions=[
                        Coin(
                            parent_coin_info=rewards_to_claim[0].coin.name(),
                            puzzle_hash=self.rewards_claim_puzhash,
                            amount=uint64(total_reward_amount - fee),
                        ),
                        Coin(
                            parent_coin_info=plotnft.coin.name(),
                            puzzle_hash=plotnft.puzzle_hash(nonce=uint64(0)),
                            amount=uint64(1),
                        ),
                    ],
                    removals=[reward.coin for reward in rewards_to_claim] + [plotnft.coin],
                    name=spend_bundle.name(),
                    extra_conditions=extra_conditions,
                )
            )
```

**File:** chia/wallet/plotnft_wallet/plotnft_wallet.py (L421-481)
```python
    async def transfer_plotnft(
        self,
        *,
        action_scope: WalletActionScope,
        target_wallet_fingerprint: int,
        fee: uint64 = uint64(0),
        extra_conditions: tuple[Condition, ...] = tuple(),
    ) -> None:
        """
        Because the wallet doesn't have broad support for MIPS-style custody, transferring using addresses is
        a bit complicated because transferring to the wrong kind of address could leave the wallet unable to
        sync/spend the PlotNFT. As a guard, we implement this endpoint as a transfer between local keys to make
        sure that we generate the correct kind of inner puzzle.

        This is not necessarily a permanent restriction, but solves the primary use case of transferring between
        a user's own wallets and keeps the implementation relatively simple for now.
        """
        plotnft = await self.get_current_plotnft()
        fee_hook = CreateCoinAnnouncement(msg=b"", coin_id=plotnft.coin.name())
        root_pubkey = await self.wallet_state_manager.wallet_node.keychain_proxy.get_key_for_fingerprint(
            fingerprint=target_wallet_fingerprint, private=False
        )
        if root_pubkey is None:
            raise RuntimeError(f"Error retrieving key for fingerprint {target_wallet_fingerprint}")
        wallet_pubkey = master_pk_to_wallet_pk_unhardened(root_pubkey, index=uint32(0))
        synthetic_pubkey = self.xch_wallet.convert_public_key_to_synthetic(wallet_pubkey)
        hint = self.xch_wallet.puzzle_hash_for_pk(wallet_pubkey)
        new_user_config = UserConfig(synthetic_pubkey=synthetic_pubkey)
        coin_spends = plotnft.new_user_config(
            user_config=new_user_config,
            hint=hint,
            extra_conditions=(fee_hook, *extra_conditions),
        )
        if fee > 0:
            await self.xch_wallet.create_tandem_xch_tx(
                fee=fee,
                action_scope=action_scope,
                extra_conditions=(fee_hook.corresponding_assertion(),),
            )

        spend_bundle = WalletSpendBundle(coin_spends, G2Element())
        async with action_scope.use() as interface:
            interface.side_effects.transactions.append(
                self.wallet_state_manager.new_outgoing_transaction(
                    wallet_id=self.id(),
                    puzzle_hash=hint,
                    amount=uint64(1),
                    fee=fee,
                    spend_bundle=spend_bundle,
                    additions=[
                        Coin(
                            parent_coin_info=plotnft.coin.name(),
                            puzzle_hash=dataclasses.replace(plotnft, user_config=new_user_config).puzzle_hash(nonce=0),
                            amount=uint64(1),
                        )
                    ],
                    removals=[plotnft.coin],
                    name=spend_bundle.name(),
                    extra_conditions=extra_conditions,
                )
            )
```

**File:** chia/wallet/plotnft_wallet/plotnft_wallet.py (L616-622)
```python
                        if coin_data.pool_config.pool_puzzle_hash != pool_config.target_puzzle_hash:
                            pool_config.pool_url = await self.wallet_state_manager.plotnft2_store.get_latest_remark(
                                coin_data.launcher_id
                            )
                            pool_config.target_puzzle_hash = coin_data.pool_config.pool_puzzle_hash
                    else:
                        pool_config.target_puzzle_hash = bytes32.from_hexstr(pool_config.payout_instructions)
```
