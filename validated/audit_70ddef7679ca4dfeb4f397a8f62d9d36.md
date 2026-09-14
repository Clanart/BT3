## Analog Found

### Title
Malicious clawback sender can grief `ClawbackManager.auto_claim_coins()` batch claims and stall honest recipients' funds - (File: `chia/wallet/clawback_manager.py`)

### Summary
`ClawbackManager.auto_claim_coins()` groups multiple, unrelated clawback ("merkle") coins together into a single atomic `WalletSpendBundle` when claiming payments on behalf of the recipient wallet. Because any clawback coin's *sender* can spend (claw back) the coin at any time with no timelock restriction, a malicious sender can race the recipient's batched auto-claim and invalidate the shared coin, causing the entire aggregated spend bundle — including every other, unrelated depositor's coin bundled in the same batch — to be rejected by the mempool.

### Finding Description
`auto_claim_coins` selects up to `auto_claim_batch_size` unspent clawback coins where the local wallet is the recipient and the timelock has elapsed, then calls `spend_clawback_coins` once per batch: [1](#0-0) 

`spend_clawback_coins` builds one `coin_spend` per coin and aggregates them into a single `WalletSpendBundle`, which is a single atomic transaction: [2](#0-1) 

The underlying merkle puzzle (`create_clawback_merkle_tree` / `create_merkle_solution`) permits the *sender* branch to be spent immediately, with no `ASSERT_SECONDS_RELATIVE` timelock condition, whereas the *recipient* branch requires the timelock to have elapsed: [3](#0-2) 

Because the sender side has no time restriction, any counterparty (sender) of a clawback payment to the recipient wallet can, at any moment, spend/claw-back their coin independently. If the recipient's `auto_claim_coins` has already selected that same coin into a batch alongside many unrelated, honest depositors' coins and submits the aggregated `WalletSpendBundle`, the whole bundle becomes an attempted double-spend on that one coin. Since spend bundles are validated and accepted atomically, the entire transaction — and therefore every other honest depositor's claim bundled in it — will be rejected by the mempool (e.g. `DOUBLE_SPEND`), forcing the recipient to retry claiming, while a malicious sender can repeat this race indefinitely.

This mirrors the reported bug class exactly: a single adversarial, unprivileged party (the blacklisted USDC address in the original report; here, a malicious clawback sender) can be batched together with many honest participants in one atomic operation, and by ensuring their own leg fails at execution time, they deny service to everyone else bundled with them — with no fallback (no per-coin retry after a chain-level rejection, only Python-level `try/except` which cannot catch on-chain double-spend failures).

### Impact Explanation
This allows a malicious user who sends a clawback payment to a wallet (e.g. an exchange or service auto-claiming incoming clawback deposits) to repeatedly stall or indefinitely delay other, unrelated depositors' fund claims by racing the batch auto-claim process. Given `auto_claim_batch_size` can group many coins (default up to 50) into a single spend bundle, a single malicious depositor can degrade or deny claim processing for many honest users at once, repeatable at will since the sender path has no time restriction. This is a spend-triggered transaction-processing halt affecting other legitimate wallet users, satisfying the Medium/High-severity DoS class called for.

### Likelihood Explanation
Likelihood is high: no privileged access is needed. Any user who can send a clawback-type payment to the victim wallet can become one of the "batched" senders, and can trivially watch the wallet's mempool/chain activity (or simply race blindly, repeating the attack) to invalidate the shared coin at the moment the recipient submits its aggregated auto-claim transaction. The victim wallet has no built-in defense (no isolation of coins into independent bundles, no retry-and-exclude-failed-coin logic after an on-chain rejection).

### Recommendation
Avoid bundling multiple, independently-owned/sourced clawback coins from different counterparties into a single atomic spend bundle in `spend_clawback_coins`/`auto_claim_coins`. Alternatives:
- Submit each clawback claim as its own independent spend bundle/transaction (accepting higher fee overhead), or
- Detect mempool rejection of a batched bundle (e.g. `DOUBLE_SPEND`), then automatically retry the batch after excluding the already-spent coin(s) and re-attempt with the remaining coins, rather than requiring an external retry.

### Proof of Concept
1. Attacker Sender S sends a clawback payment to Recipient wallet R with any timelock T, creating a merkle coin.
2. After T seconds pass, R's `ClawbackManager.auto_claim_coins()` selects this coin plus N other unrelated, honest depositors' matured clawback coins into the same batch (per `auto_claim_batch_size`), building one aggregated `WalletSpendBundle` via `spend_clawback_coins` (`chia/wallet/clawback_manager.py:207-266`).
3. Before R's bundle is accepted into the mempool/block, S submits their own clawback spend of the same merkle coin (permitted at any time per `create_merkle_solution`'s sender branch, `chia/wallet/puzzles/clawback/drivers.py:106-135`), which is a valid transaction.
4. Whichever of S's or R's spend of the shared coin is included first invalidates the other; if S's spend lands first (or R's aggregated bundle is rejected as a double-spend when it eventually would conflict), the entire aggregated `WalletSpendBundle` from R fails at the mempool, and none of the N other honest depositors' coins in that batch get claimed.
5. S can repeat this indefinitely each time R attempts to auto-claim, denying settlement to unrelated honest depositors bundled alongside S's coin.

### Citations

**File:** chia/wallet/clawback_manager.py (L191-205)
```python
        for coin in unspent_coins.records:
            try:
                metadata = coin.parsed_metadata()
                assert isinstance(metadata, ClawbackMetadata)
                if await metadata.is_recipient(self.puzzle_store):
                    coin_timestamp = await self.timestamp_for_height(coin.confirmed_block_height)
                    if current_timestamp - coin_timestamp >= metadata.time_lock:
                        clawback_coins[coin.coin] = metadata
                        if len(clawback_coins) >= self.auto_claim_batch_size:
                            await self.spend_clawback_coins(clawback_coins, self.auto_claim_tx_fee, action_scope)
                            clawback_coins = {}
            except Exception as e:
                self.log.error(f"Failed to claim clawback coin {coin.coin.name().hex()}: %s", e)
        if len(clawback_coins) > 0:
            await self.spend_clawback_coins(clawback_coins, self.auto_claim_tx_fee, action_scope)
```

**File:** chia/wallet/clawback_manager.py (L216-266)
```python
        coin_spends: list[CoinSpend] = []
        message = std_hash(b"".join([c.name() for c in clawback_coins.keys()]))
        derivation_record = None
        amount = uint64(0)
        for coin, metadata in clawback_coins.items():
            try:
                self.log.info(f"Claiming clawback coin {coin.name().hex()}")
                # Get incoming tx
                incoming_tx = await self.transaction_store.get_transaction_record(coin.name())
                assert incoming_tx is not None, f"Cannot find incoming tx for clawback coin {coin.name().hex()}"
                if incoming_tx.sent > 0 and not force:
                    self.log.error(
                        f"Clawback coin {coin.name().hex()} is already in a pending spend bundle. {incoming_tx}"
                    )
                    continue

                recipient_puzhash = metadata.recipient_puzzle_hash
                sender_puzhash = metadata.sender_puzzle_hash
                is_recipient: bool = await metadata.is_recipient(self.puzzle_store)
                if is_recipient:
                    derivation_record = await self.puzzle_store.get_derivation_record_for_puzzle_hash(recipient_puzhash)
                else:
                    derivation_record = await self.puzzle_store.get_derivation_record_for_puzzle_hash(sender_puzhash)
                assert derivation_record is not None
                amount = uint64(amount + coin.amount)
                # Remove the clawback hint since it is unnecessary for the XCH coin
                memos: list[bytes] = [] if len(incoming_tx.memos) == 0 else next(iter(incoming_tx.memos.items()))[1][1:]
                inner_puzzle = self.xch_wallet.puzzle_for_pk(derivation_record.pubkey)
                inner_solution = self.xch_wallet.make_solution(
                    primaries=[
                        CreateCoin(
                            derivation_record.puzzle_hash,
                            uint64(coin.amount),
                            memos,  # Forward memo of the first coin
                        )
                    ],
                    conditions=(
                        extra_conditions
                        if len(coin_spends) > 0 or fee == 0
                        else (*extra_conditions, CreateCoinAnnouncement(message))
                    ),
                )
                coin_spend: CoinSpend = generate_clawback_spend_bundle(coin, metadata, inner_puzzle, inner_solution)
                coin_spends.append(coin_spend)
                # Update incoming tx to prevent double spend and mark it is pending
                await self.transaction_store.increment_sent(incoming_tx.name, "", MempoolInclusionStatus.PENDING, None)
            except Exception as e:
                self.log.error(f"Failed to create clawback spend bundle for {coin.name().hex()}: {e}")
        if len(coin_spends) == 0:
            return
        spend_bundle = WalletSpendBundle(coin_spends, G2Element())
```

**File:** chia/wallet/puzzles/clawback/drivers.py (L70-135)
```python
def create_clawback_merkle_tree(timelock: uint64, sender_ph: bytes32, recipient_ph: bytes32) -> MerkleTree:
    """
    Returns a merkle tree object
    For clawbacks there are only 2 puzzles in the merkle tree, claim puzzle and clawback puzzle
    """
    if timelock < 1:
        raise ValueError("Timelock must be at least 1 second")
    timelock_condition = [ConditionOpcode.ASSERT_SECONDS_RELATIVE, timelock]
    augmented_cond_puz_hash = create_augmented_cond_puzzle_hash(timelock_condition, recipient_ph)
    merkle_tree = MerkleTree(
        [
            augmented_cond_puz_hash,
            curry_and_treehash(P2_CURRIED_PUZZLE_MOD_HASH_QUOTED, Program.to(sender_ph).get_tree_hash()),
        ]
    )
    return merkle_tree


def create_merkle_proof(merkle_tree: MerkleTree, puzzle_hash: bytes32) -> Program:
    """
    To spend a p2_1_of_n clawback we recreate the full merkle tree
    The required proof is then selected from the merkle tree based on the puzzle_hash of the puzzle we
    want to execute
    Returns a proof: (int, list[bytes32]) which can be provided to the p2_1_of_n solution
    """
    proof = merkle_tree.generate_proof(puzzle_hash)
    program: Program = Program.to((proof[0], proof[1][0]))
    return program


def create_merkle_puzzle(timelock: uint64, sender_ph: bytes32, recipient_ph: bytes32) -> Program:
    merkle_tree = create_clawback_merkle_tree(timelock, sender_ph, recipient_ph)
    puzzle: Program = P2_1_OF_N.curry(merkle_tree.calculate_root())
    return puzzle


def create_merkle_solution(
    timelock: uint64,
    sender_ph: bytes32,
    recipient_ph: bytes32,
    inner_puzzle: Program,
    inner_solution: Program,
) -> Program:
    """
    Recreates the full merkle tree of a p2_1_of_n clawback coin. It uses the timelock and each party's
    puzhash to create the tree.
    The provided inner puzzle must hash to match either the sender or recipient puzhash
    If it's the sender, then create the clawback solution. If it's the recipient then create the claim
    solution.
    Returns a program which is the solution to a p2_1_of_n clawback.
    """
    merkle_tree = create_clawback_merkle_tree(timelock, sender_ph, recipient_ph)
    inner_puzzle_hash = inner_puzzle.get_tree_hash()
    if inner_puzzle_hash == sender_ph:
        cb_inner_puz = create_p2_puzzle_hash_puzzle(sender_ph)
        merkle_proof = create_merkle_proof(merkle_tree, cb_inner_puz.get_tree_hash())
        cb_inner_solution = create_p2_puzzle_hash_solution(inner_puzzle, inner_solution)
    elif inner_puzzle_hash == recipient_ph:
        condition = [80, timelock]
        cb_inner_puz = create_augmented_cond_puzzle(condition, inner_puzzle)
        merkle_proof = create_merkle_proof(merkle_tree, cb_inner_puz.get_tree_hash())
        cb_inner_solution = create_augmented_cond_solution(inner_solution)
    else:
        raise ValueError("Invalid Clawback inner puzzle.")
    solution: Program = Program.to([merkle_proof, cb_inner_puz, cb_inner_solution])
    return solution
```
