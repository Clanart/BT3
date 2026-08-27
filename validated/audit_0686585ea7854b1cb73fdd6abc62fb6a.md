## Analog Analysis Result

I found a strong, unprivileged-wallet-reachable analog of the "single failing transfer reverts the whole batch" bug class in `WombatBribeManager.castVotes()`.

### Title
Blacklisted/malicious-hook `msg.sender` in `castVotes()` can block bribe reward distribution for all voters via reverting `_forwardRewards` - (File: wombat/WombatBribeManager.sol)

### Summary
`castVotes()` is a public, unprivileged function that anyone can call to cast pending Wombat votes and harvest bribes for **all** registered pools in a single transaction. As part of this call, it distributes caller fees across every bribe reward token via `_forwardRewards()`, which loops over all reward tokens and transfers a fee directly to `msg.sender`. If any single token transfer in that loop reverts (e.g., a USDC-style blacklist rejecting `msg.sender`, or an ERC777/hook-based token that reverts on receive), the entire `castVotes()` transaction reverts — including the vote casting and bribe harvesting for every other pool and every other reward token, none of which are related to the failing transfer.

### Finding Description
`castVotes()` [1](#0-0)  iterates over **all** pools to compute vote deltas, calls `wombatStaking.vote(...)` to harvest bribes, and then calls `_forwardRewards(rewardTokens, feeAmounts)` to pay the caller's fee for every reward token collected.

`_forwardRewards` performs an unguarded `safeTransfer` inside a nested loop over every bribe reward token collected from every pool: [2](#0-1) 

Any ordinary user can call `castVotes(false)` — it is a public, unprivileged function. If that caller is blacklisted by even one of the many bribe reward tokens accumulated across pools (a plausible scenario since bribe tokens are arbitrary, project-specified tokens including stablecoins like USDC), or if one bribe reward token is an ERC777-style token with a `tokensReceived` hook that reverts for that specific caller, the `safeTransfer` at line 393 reverts. Because there is no isolation (no try/catch) around this call, the **entire** `castVotes()` transaction reverts — undoing the vote casting and bribe harvest/queuing for every pool and every voter, not just the caller.

This mirrors the reported `quexCallback()` bug class exactly: a batch-processing function that aggregates unrelated operations (harvesting/distributing rewards for many pools/tokens on behalf of many users) in a single transaction, where one blacklisted/malicious-hook recipient can DoS the whole batch.

### Impact Explanation
Since `castVotes()` is the mechanism that pushes votes to Wombat and queues harvested bribe rewards into each pool's `BribeRewardPool` via `wombatStaking.vote(...)`, a revert here means:
- Votes cannot be cast/re-balanced across pools.
- Bribe rewards harvested from Wombat cannot be queued into the reward pools for distribution to all voters.

This can be repeated indefinitely by an attacker who deliberately becomes blacklisted (or deploys a malicious ERC777-style token that a bribe pool later includes) and repeatedly calls `castVotes()`, or simply calling it once when a legitimate blacklist event occurs for any past caller — since `castVotes` can be called by anyone, the attacker doesn't even need special permission, just to call the function themselves while blacklisted. This results in a freeze of unclaimed bribe yield for all `vlMGP` voters/depositors of the affected pools for as long as the vulnerable token remains a bribe reward token, exceeding a 24-hour freeze in practice (bribe harvesting/queuing is generally the ongoing mechanism for yield distribution, and could be blocked indefinitely, i.e. until the token or the fee logic is manually removed by governance).

### Likelihood Explanation
Likelihood is moderate-to-high: the attacker only needs to (1) get themselves blacklisted by an issuer of any token that later becomes a bribe reward token for any pool (a low-cost, self-inflicted, permissionless action for tokens like USDC), or (2) rely on an already-blacklisted address, then call the fully public `castVotes()` function. No special role or governance access is required — this is directly reachable from an ordinary wallet's transaction.

### Recommendation
Wrap the `IERC20(...).safeTransfer(msg.sender, feeAmounts[i][j])` call inside `_forwardRewards` in a try/catch (or use a low-level `call` and check success without reverting the outer transaction), and skip/accrue the failed fee rather than reverting the whole `castVotes()` batch. This isolates a single failing transfer from blocking vote casting and bribe distribution for all other pools and users.

### Proof of Concept
1. A bribe reward token list for the registered pools includes a USDC-like blacklistable token (or a token with a malicious/ERC777-style receive hook) among the reward tokens tracked in `poolInfos[...].rewarder`.
2. Attacker (or any address) becomes blacklisted by the issuer of that token (self-inflicted, permissionless for the attacker's own address), or deploys/controls a token later added as a bribe reward with a reverting `tokensReceived` hook.
3. Attacker calls `castVotes(false)` as `msg.sender`.
4. Inside `castVotes` → `_forwardRewards`, the loop reaches the blacklisted/malicious token and `IERC20(rewardTokens[i][j]).safeTransfer(msg.sender, feeAmounts[i][j])` at [3](#0-2)  reverts.
5. The entire `castVotes()` call reverts, so vote rebalancing and bribe reward queuing for every other pool and reward token also fail in that transaction — freezing distribution of already-harvested bribe yield to all voters for as long as this condition persists (any caller hitting this path blocks the batch, so it can be effectively perpetually griefed by the attacker calling `castVotes` themselves while blacklisted).

### Citations

**File:** wombat/WombatBribeManager.sol (L241-296)
```text
    function castVotes(bool swapForBnb)
        override public
        returns (address[][] memory finalRewardTokens, uint256[][] memory finalFeeAmounts)
    {
        lastCastTime = block.timestamp;
        uint256 length = pools.length;
        address[] memory _pools = new address[](length);
        int256[] memory votes = new int256[](length);
        address[] memory rewarders = new address[](length);

        for (uint256 i; i < length; i++) {
            Pool storage pool = poolInfos[pools[i]];
            _pools[i] = pool.poolAddress;
            rewarders[i] = pool.rewarder;

            uint256 currentVote = getVoteForLp(pool.poolAddress);
            uint256 targetVoteInLMGP = pool.totalVoteInVlmgp;
            uint256 targetVote = 0;

            if (totalVlMgpInVote != 0) {
                targetVote = targetVoteInLMGP * totalVotes() / totalVlMgpInVote;
            }

            if (targetVote >= currentVote) {
                votes[i] = int256(targetVote - currentVote);
            } else {
                votes[i] = int256(targetVote) - int256(currentVote);
            }
        }

        (address[][] memory rewardTokens, uint256[][] memory feeAmounts) = wombatStaking.vote(
            _pools,
            votes,
            rewarders,
            msg.sender
        );

        // comment outs for now since chainlink fails sometimes
        // if (swapForBnb) {
        //     finalFeeAmounts = new uint256[][](1);
        //     finalFeeAmounts[0] = new uint256[](1);
        //     finalFeeAmounts[0][0] = _swapFeesForBnb(rewardTokens, feeAmounts);
        //     finalRewardTokens = new address[][](1);
        //     finalRewardTokens[0] = new address[](1);
        //     finalRewardTokens[0][0] = address(0);
        // } else {
            _forwardRewards(rewardTokens, feeAmounts);
            finalRewardTokens = rewardTokens;
            finalFeeAmounts = feeAmounts;
        // }

        // send rewards to the delegate pool
        if (delegatedPool != address(0)) IDelegateVoteRewardPool(delegatedPool).harvestAll();

        emit VoteCasted(msg.sender, lastCastTime);
    }
```

**File:** wombat/WombatBribeManager.sol (L387-397)
```text
    function _forwardRewards(address[][] memory rewardTokens, uint256[][] memory feeAmounts) internal {
        uint256 bribeLength = rewardTokens.length;
        for (uint256 i; i < bribeLength; i++) {
            uint256 TokenLength = rewardTokens[i].length;
            for(uint256 j; j < TokenLength; j++) {
                if (rewardTokens[i][j] != address(0) && feeAmounts[i][j] > 0) {
                    IERC20(rewardTokens[i][j]).safeTransfer(msg.sender, feeAmounts[i][j]);
                }
            }
        }
    }
```
