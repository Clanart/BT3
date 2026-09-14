### Title
Unhandled `ValueError` in `PlotNFT2Wallet.coin_added` on receipt of a non-reward coin at the p2-singleton address halts wallet sync/reward-claim processing - (File: chia/wallet/plotnft_wallet/plotnft_wallet.py)

### Summary
`PlotNFT2Wallet.coin_added()` unconditionally raises a `ValueError` whenever a coin lands on the plot-NFT's publicly-known `p2_singleton_puzzle_hash` that does not have a parent-coin-info prefix matching the genesis-challenge (i.e. is not a genuine farming/pool reward coin). This mirrors the reported `FeeDistributor` bug class: a single "receiver" that cannot accept an incoming payment blocks processing for the whole contract/wallet, and there is no recovery path once the disqualifying payment lands on-chain.

### Finding Description
`PlotNFT2Wallet.coin_added()`, invoked from `WalletStateManager._add_coin_state()` during coin-state sync, distinguishes genuine reward coins from arbitrary payments by checking the coin's parent-coin-info prefix against `GENESIS_CHALLENGE`: [1](#0-0) 

If any coin (of any origin - anyone can send any XCH amount to this hinted, publicly derivable p2-singleton address, just as anyone can send ether to a known `FeeDistributor` receiver) is created with a puzzle hash equal to `self.p2_singleton_puzzle_hash` but whose parent-coin-info prefix does not match the genesis challenge, `coin_added` raises `ValueError` instead of ignoring the coin or storing it for later handling.

This handler is called from the wallet-sync coin-state processing path: [2](#0-1) 

The historical CHANGELOG shows this exact class of bug ("coins sent by accident to the pool contract address") was previously identified and explicitly fixed for the original `PoolWallet` implementation by making the wallet *ignore* such coins rather than fail: [3](#0-2) [4](#0-3) 

The new `PlotNFT2Wallet` driver reintroduces the unhandled-exception behavior instead of the "ignore" behavior that the old code was fixed to use.

### Impact Explanation
Because the p2-singleton puzzle hash is a stable, publicly known/derivable address (it is exposed to the pool, to plotting configuration, and visible on-chain from prior farming activity), any third party can send an arbitrary amount of XCH to it. Once such a coin is confirmed on-chain, every wallet node that syncs this coin state will invoke `coin_added()` and raise an unhandled `ValueError` for that PlotNFT's coin-state processing. Depending on how far up the call stack this propagates (whether caught by an outer `except Exception` in the sync loop or not), this can repeatedly break coin-state processing for the affected wallet/coin, preventing legitimate reward claiming (`claim_rewards` / `pw_absorb_rewards`) and PlotNFT lifecycle transitions from being correctly tracked, effectively "stranding" the user's ability to observe or claim funds tied to that plot NFT — directly analogous to ether being stuck in `FeeDistributor` because one receiver blocks withdrawal for everyone. There is no code path to recover or "skip" the offending coin; the condition is permanent until code/config changes.

### Likelihood Explanation
Likelihood is Medium-to-High: no special privilege is needed — any wallet user or bystander who knows/derives the plot-NFT's p2-singleton puzzle hash (which is exposed by design, e.g., during pooling setup, in RPC status responses, and in on-chain history) can trigger the condition by sending a single, ordinary XCH payment to that address. This requires no malicious peer/node, no farmer/harvester access, and no protocol-level exploit — just a standard spend bundle creating a coin with that puzzle hash and a non-matching parent id. Given that the CHANGELOG already documents users accidentally sending money to pool contract addresses in the wild, hitting this condition is realistic even without malicious intent.

### Recommendation
Change `PlotNFT2Wallet.coin_added()` to not raise on unexpected/foreign coins landing on `p2_singleton_puzzle_hash`. Instead, log a warning and skip/ignore the coin (mirroring the historical fix applied to the legacy `PoolWallet`), or store it separately so it can be swept/reclaimed without blocking normal reward-claim and sync processing. Additionally, audit the call site in `WalletStateManager._add_coin_state`/wallet sync loop to ensure any wallet-specific `coin_added` exception is caught and isolated per-coin rather than aborting broader sync processing.

### Proof of Concept
1. Create/observe a `PlotNFT2Wallet` and note its `p2_singleton_puzzle_hash` (obtainable via `pw_status` RPC or on-chain history).
2. From any wallet (attacker or well-meaning third party), submit a standard spend bundle that creates a coin with `puzzle_hash == p2_singleton_puzzle_hash` and an amount/parent that is not a genuine pool/farm reward (i.e., `parent_coin_info[0:16] != GENESIS_CHALLENGE[0:16]`).
3. Once the block confirms, any full node/wallet syncing this coin state calls `WalletStateManager._add_coin_state()` → `PlotNFT2Wallet.coin_added()`.
4. `coin_added()` hits the `else` branch at [5](#0-4)  and raises `ValueError("A non-pooling reward coin was paid to PlotNFT with id: ...")`, with no code path to catch, ignore, or clear this state, unlike the previously fixed behavior for the legacy pool wallet described in the CHANGELOG.

### Citations

**File:** chia/wallet/plotnft_wallet/plotnft_wallet.py (L651-657)
```python
        elif coin_data is None and coin.puzzle_hash == self.p2_singleton_puzzle_hash:
            if coin.parent_coin_info[0:16] == self.wallet_state_manager.constants.GENESIS_CHALLENGE[0:16]:
                await self.wallet_state_manager.plotnft2_store.add_pool_reward(
                    pool_reward=PoolReward(singleton_id=self.plotnft_id, coin=coin)
                )
            else:
                raise ValueError(f"A non-pooling reward coin was paid to PlotNFT with id: {self.plotnft_id}")
```

**File:** chia/wallet/wallet_state_manager.py (L1216-1229)
```python
        elif coin_state.spent_height is None:
            if local_record is None:
                await self.coin_added(
                    coin_state.coin,
                    uint32(coin_state.created_height),
                    all_unconfirmed,
                    wallet_identifier.id,
                    wallet_identifier.type,
                    peer,
                    coin_name,
                    coin_data,
                    sync_scope,
                )
                await self.add_interested_coin_ids([coin_name])
```

**File:** CHANGELOG.md (L2516-2517)
```markdown
- Added a warning to user to not send money to the pool contract address.
- Added capability to enable use of a backup key in future, to claim funds that were sent to p2_singleton_puzzle_hash, which today are just ignored.
```

**File:** CHANGELOG.md (L2543-2544)
```markdown
- Thanks @felixbrucker for helping fix invalid content-type header issues in pool API requests.
- The wallet ignores coins sent by accident to the pool contract address and allows self pooling rewards to be claimed in this case.
```
