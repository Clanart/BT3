### Title
PlotNFT ownership transfer omits pending pool-reward claim, stranding accrued rewards for the new owner - ([File: chia/wallet/plotnft_wallet/plotnft_wallet.py])

### Summary
`PlotNFT2Wallet.transfer_plotnft` (`chia/wallet/plotnft_wallet/plotnft_wallet.py:421-482`) reassigns ownership of a PlotNFT singleton (changes `user_config.synthetic_pubkey`) to a different wallet fingerprint, but never requires or performs a claim/forward of any outstanding, unclaimed `pool_reward2s` rows tracked for that `plotnft_id` before the transfer executes. This mirrors the TREC-1 report's root cause: an account-transfer path that moves custody/ownership without first sweeping accrued rewards into the recipient's claimable state, leaving them effectively unreachable by the new owner in practice.

### Finding Description
`transfer_plotnft` builds a spend that only changes the PlotNFT's `user_config` (owner key) via `plotnft.new_user_config(...)`: [1](#0-0) 

It contains no check against `self.wallet_state_manager.plotnft2_store.get_pool_rewards(plotnft_id=self.plotnft_id)` — contrast with `claim_rewards`, which explicitly queries and gates on pending rewards: [2](#0-1) 

The reward-claim destination (`rewards_claim_puzhash`) is derived from a *local, per-node* `PoolingShareState` entry keyed by `p2_singleton_puzzle_hash` (which is stable across ownership transfer since it is derived only from `plotnft_id`, not from the current owner key): [3](#0-2) 

That local config is (re)populated only when a wallet observes the plotnft's `coin_added` event on its own node, at which point it derives a fresh payout puzzle hash for whichever key/root_path is running at that time: [4](#0-3) 

Because reward coins are recorded in `pool_reward2s` keyed only by `singleton_id` (the launcher id), and because `claim_rewards`/`claim_pool_rewards` unconditionally rejects claiming while pooling (`"Cannot claim rewards while pooling. If you're a pool, try forward_pool_rewards"`) as seen in tests: [5](#0-4) [6](#0-5) 

any rewards accrued but not yet claimed/forwarded at transfer time depend entirely on the new owning wallet independently rediscovering and re-tracking the historical `p2_singleton_puzzle_hash` reward coins and rebuilding correct local `PoolingShareState`/`plotnft2_store` records after the ownership key changes. The existing tests explicitly demonstrate rewards being "LOST" when the relevant forward/claim action is not performed before a state transition (leaving pool, entering waiting room, etc.) — the same lifecycle risk applies to `transfer_plotnft`, which has no equivalent safeguard, comment, or reward-claim requirement: [7](#0-6) [8](#0-7) 

### Impact Explanation
If a user transfers a PlotNFT to a different wallet/fingerprint (the RPC explicitly supports arbitrary `target_wallet_fingerprint`, including one on a keychain/node not currently tracking this plotnft's history) while unclaimed self-farming rewards exist in `pool_reward2s`, those rewards are not swept to the outgoing owner nor to the incoming owner as part of the same atomic transfer transaction. The recipient must rely on wallet resync to rediscover the reward coins under the stable `p2_singleton_puzzle_hash`; if that resync does not occur (e.g., partial/height-limited history sync, database not fully backfilled, or wallet only registers interest going forward from the point of tracking), the rewards become practically unclaimable by anyone, since the prior owner's key no longer matches `user_config` and cannot re-sign a valid claim spend for the singleton going forward. This is a fund-availability defect analogous to TREC-1: unswept reward accounting silently orphaned across an ownership/custody transition.

### Likelihood Explanation
This is reachable by any ordinary PlotNFT2 wallet owner using the standard `plotnft_transfer` RPC/CLI flow whenever they have accrued but unclaimed self-pooling rewards — no privileged or malicious actor is required, only normal use of a documented feature. The condition (unclaimed rewards existing at transfer time) is common in practice since rewards accumulate between periodic claim calls.

### Recommendation
Require `PlotNFT2Wallet.transfer_plotnft` to check `plotnft2_store.get_pool_rewards(plotnft_id=self.plotnft_id)` before constructing the transfer spend, and either (a) reject the transfer with a clear error until rewards are claimed/forwarded, or (b) bundle a claim/forward of all outstanding rewards into the same transfer transaction so reward destination and singleton ownership change atomically, mirroring the recommendation adopted for the analogous GMX case (require claiming/injecting rewards before initiating the transfer).

### Proof of Concept
1. Create a self-pooling PlotNFT2 wallet and farm several blocks to `p2_singleton_puzzle_hash`, producing multiple unclaimed `pool_reward2s` rows (as in `test_plotnft_lifecycle`, lines 258-279).
2. Instead of calling `claim_rewards`, call `transfer_plotnft(target_wallet_fingerprint=<new fingerprint>)` (`chia/wallet/plotnft_wallet/plotnft_wallet.py:421`), which only spends the singleton to change `user_config` — no reward claim/forward spend is included.
3. Observe that the unclaimed reward coin records remain in the original node's `pool_reward2s` table keyed by `plotnft_id`, while the singleton's owner key is now the new fingerprint's key; the original owner can no longer produce a valid signed claim (their key no longer matches `user_config`), and the new owner's wallet (potentially a fresh node/keychain) has no guaranteed mechanism in this path to automatically resync and re-populate those historical reward records before being able to claim them.

### Citations

**File:** chia/wallet/plotnft_wallet/plotnft_wallet.py (L91-96)
```python
    @property
    def rewards_claim_puzhash(self) -> bytes32:
        with PoolingShareState.acquire(
            root_path=self.wallet_state_manager.root_path, p2_singleton_puzzle_hash=self.p2_singleton_puzzle_hash
        ) as pool_config:
            return bytes32.from_hexstr(pool_config.payout_instructions)
```

**File:** chia/wallet/plotnft_wallet/plotnft_wallet.py (L149-161)
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
```

**File:** chia/wallet/plotnft_wallet/plotnft_wallet.py (L438-453)
```python
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
```

**File:** chia/wallet/plotnft_wallet/plotnft_wallet.py (L623-641)
```python
            else:
                async with self.wallet_state_manager.new_action_scope(
                    self.wallet_state_manager.tx_config, push=True
                ) as action_scope:
                    payout_puzzle_hash = await action_scope.get_puzzle_hash(self.wallet_state_manager)
                PoolingShareState(
                    launcher_id=coin_data.launcher_id,
                    pool_url=await self.wallet_state_manager.plotnft2_store.get_latest_remark(coin_data.launcher_id)
                    if coin_data.pool_config is not None
                    else "",
                    owner_public_key=coin_data.user_config.synthetic_pubkey,
                    target_puzzle_hash=coin_data.pool_config.pool_puzzle_hash
                    if coin_data.pool_config is not None
                    else payout_puzzle_hash,
                    p2_singleton_puzzle_hash=self.p2_singleton_puzzle_hash,
                    payout_instructions=payout_puzzle_hash.hex(),
                    key_derivation_index=int(index),
                    version=2,
                ).add(root_path=self.wallet_state_manager.root_path)
```

**File:** chia/pools/plotnft_drivers.py (L590-598)
```python
    def claim_pool_rewards(
        self,
        rewards_to_claim: list[PoolReward],
        reward_delegated_puzzles_and_solutions: list[DelegatedPuzzleAndSolution],
    ) -> list[CoinSpend]:
        if self.pooling:
            raise ValueError("Cannot claim rewards while pooling. If you're a pool, try `forward_pool_rewards`")
        if len(rewards_to_claim) != len(reward_delegated_puzzles_and_solutions):
            raise ValueError("Number of rewards and delegated puzzles and solutions must match")
```

**File:** chia/_tests/wallet/plotnft_wallet/test_plotnft_wallet.py (L281-286)
```python
    async with env.wallet_state_manager.new_action_scope(wallet_environments.tx_config, push=True) as action_scope:
        with pytest.raises(
            ValueError,
            match=re.escape("Cannot claim rewards while pooling. If you're a pool, try `forward_pool_rewards`"),
        ):
            await plotnft_wallet.claim_rewards(action_scope=action_scope)
```

**File:** chia/_tests/wallet/plotnft_wallet/test_plotnft_wallet.py (L288-301)
```python
    # LOSE REWARDS (while pooling)
    pool_rewards = await env.wallet_state_manager.plotnft2_store.get_pool_rewards(plotnft_id=plotnft_wallet.plotnft_id)
    plotnft = await plotnft_wallet.get_current_plotnft()
    coin_spends = []
    singleton_coin_spend = None
    for reward in pool_rewards[0:-1]:
        new_coin_spends = plotnft.forward_pool_reward(reward)
        coin_spends += new_coin_spends
        singleton_coin_spend = next(iter(spend for spend in new_coin_spends if spend.coin.amount == 1))
        plotnft = PlotNFT.get_next_from_coin_spend(
            coin_spend=singleton_coin_spend, genesis_challenge=None, pre_uncurry=None, previous_plotnft_puzzle=plotnft
        )

    NUM_CLAIMED = len(pool_rewards) - 1
```

**File:** chia/_tests/wallet/plotnft_wallet/test_plotnft_wallet.py (L451-455)
```python
    # LOSE REWARDS (while leaving)
    plotnft = await plotnft_wallet.get_current_plotnft()
    [pool_reward] = await env.wallet_state_manager.plotnft2_store.get_pool_rewards(plotnft_id=plotnft_wallet.plotnft_id)
    coin_spends = plotnft.forward_pool_reward(pool_reward)
    await env.rpc_client.push_tx(PushTX(spend_bundle=WalletSpendBundle(coin_spends, G2Element())))
```
