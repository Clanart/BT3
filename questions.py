import json
import os

from decouple import config

# todo: if scope_files is: 500 > 50, 300 > 30 , 100 > 10
MAX_REPO = 10
# todo: the GitLab namespace/project path, for example group/project
SOURCE_REPO = 'magpiexyz/contracts'
# todo: the name of the repository
REPO_NAME = 'contracts'

run_number = os.environ.get('GITHUB_RUN_NUMBER', '0')


def get_cyclic_index(run_number, max_index=100):
    """Convert run number to a cyclic index between 1 and max_index"""
    return (int(run_number) - 1) % max_index + 1


def load_repository_urls():
    """Load repository URLs from repositories.json."""
    repo_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "repositories.json")
    if not os.path.exists(repo_file):
        return []

    try:
        with open(repo_file, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return []

    if not isinstance(data, list):
        return []

    return [url for url in data if isinstance(url, str) and url.strip()]


if run_number == "0":
    BASE_URL = f"https://deepwiki.com/{SOURCE_REPO}"
else:
    repository_urls = load_repository_urls()
    if repository_urls:
        run_index = get_cyclic_index(run_number, len(repository_urls))
        BASE_URL = repository_urls[run_index - 1]
    else:
        BASE_URL = f"https://deepwiki.com/{SOURCE_REPO}"

scope_files = [
    # =================================================================================
    # Core staking and MGP emission accounting reachable by any staker
    # =================================================================================
    "rewards/MasterMagpie.sol",
    "rewards/BaseRewardPool.sol",
    "rewards/BaseRewardPoolV2.sol",
    "rewards/BribeRewardPool.sol",
    "rewards/DelegateVoteRewardPool.sol",

    # =================================================================================
    # Lockers and vote weight: lock/unlock slots, penalties, vested reward forfeits
    # =================================================================================
    "VLMGP.sol",
    "wombat/mWomSV.sol",
    "rewards/vlMGPBaseRewarder.sol",
    "rewards/mWOMSVBaseRewarder.sol",

    # =================================================================================
    # Wombat custody layer: LP deposits, veWOM locking, WOM conversion, receipt tokens
    # =================================================================================
    "wombat/WombatStaking.sol",
    "wombat/mWOM.sol",
    "wombat/SmartWomConvert.sol",

    # =================================================================================
    # Pool helpers and compounders: the public deposit/withdraw/harvest entry points
    # =================================================================================
    "wombat/WombatPoolHelper.sol",
    "wombat/WombatPoolHelperV2.sol",
    "wombat/AnkrBNBPoolHelper.sol",
    "wombat/SimplePoolHelper.sol",
    "rewards/ManualCompound.sol",
    "rewards/BNBZapper.sol",

    # =================================================================================
    # Governance: bribe voting, vote weight allocation and bribe harvesting
    # =================================================================================
    "wombat/WombatBribeManager.sol",

    # =================================================================================
    # Distribution: airdrops, vesting, releases and referral emission share
    # =================================================================================
    "rewards/Airdrop.sol",
    "rewards/Airdrop2.sol",
    "rewards/ArbitrumMWomAirdrop.sol",
    "rewards/TokenVesting.sol",
    "rewards/MGPRelease.sol",
    "rewards/ReferralStorage.sol",

    # =================================================================================
    # Campaign converters with tier/bonus accounting
    # =================================================================================
    "wombat/WomUp.sol",
    "wombat/ArbWomUp.sol",
    "wombat/ArbWomUp2.sol",
    "wombat/ArbWomUp3.sol",

    # =================================================================================
    # Token and math libraries used inside value-bearing accounting paths
    # =================================================================================
    "Mgp.sol",
    "libraries/DSMath.sol",
    "libraries/LogExpMath.sol",
    "libraries/SignedSafeMath.sol",
    "libraries/MintableERC20.sol",
    "libraries/ERC20FactoryLib.sol",
    "libraries/PoolHelperFactoryLib.sol",
]


target_scopes = [
    "Critical. An unprivileged wallet claims MGP or bonus rewards belonging to another staker by abusing MasterMagpie multiclaimFor, multiclaimOnBehalf, multiclaimSpec, or _multiClaim receiver/_rewardTokens handling, so rewards accrued to a victim are sent to the attacker: direct theft of user funds.",
    "Critical. An unprivileged staker drains a BaseRewardPool, BaseRewardPoolV2, or BribeRewardPool beyond their earned share by manipulating queueNewRewards, donateRewards, updateFor, or the rewardPerToken/userRewardPerTokenPaid index with same-block stake-claim-unstake, dust totalStaked, or a rounding edge: direct theft of user funds.",
    "Critical. An unprivileged depositor withdraws more Wombat LP or underlying than they own, or mints receipt tokens without depositing, via a mismatch between WombatStaking deposit/depositLP/withdraw/burnReceiptToken and WombatPoolHelper, WombatPoolHelperV2, AnkrBNBPoolHelper depositFor/batchDepositLPFor/depositNative accounting: direct theft of user funds.",
    "Critical. An unprivileged voter manipulates the Wombat gauge outcome through WombatBribeManager vote, unvote, castVotes, getUserVotable, or usedVote so votes exceed the vlMGP weight they actually own, are counted twice, or wipe another voter's allocation: governance voting result manipulation.",
    "Critical. An unprivileged locker permanently freezes another user's locked MGP or mWOM, or exits with no penalty and no cooldown, via VLMGP or mWomSV startUnlock, unlock, cancelUnlock, forceUnLock, getNextAvailableUnlockSlot, or userUnlocks slot bookkeeping: permanent freezing of funds or direct theft.",
    "Critical. An unprivileged user mints mWOM that is not backed by WOM locked into veWOM, or withdraws the WOM backing, via mWOM convert/convertAndStake/deposit, WombatStaking convertWOM/convertAllWom, or SmartWomConvert convert/smartConvert ratio, mode, and _minRec handling: protocol insolvency.",
    "Critical. An unprivileged claimer takes tokens allocated to someone else or claims more than their allocation through Airdrop claim/updateEndRemainingAllocation, Airdrop2 or ArbitrumMWomAirdrop verifyProof/getClaimable/claimed accounting, MGPRelease claim, or TokenVesting release/vestedAmount: direct theft of user funds.",
    "High. An unprivileged caller redirects bribe rewards or the harvest caller fee owed to voters through WombatBribeManager harvestSinglePool, claimBribe, claimBribeFor, castVotesAndClaimBribes, or DelegateVoteRewardPool harvestAll: theft of unclaimed yield.",
    "High. An unprivileged account claims vesting MGP that should have been forfeited, or the forfeited share of other lockers, by exploiting vlMGPBaseRewarder or mWOMSVBaseRewarder calExpireForfeit, queueMGP, updateFor, or the rewardablePercentWAD used from VLMGP/mWomSV: theft of unclaimed yield.",
    "High. An unprivileged account inflates its MGP emission share or campaign reward without the qualifying stake, via ReferralStorage useCode/registerCode/trigger/updateTotalFactor self-referral and tier factors, or WomUp/ArbWomUp/ArbWomUp2/ArbWomUp3 stake, migrate, getRewardAmount, calDoubledCounted, and getUserTier accounting: theft of unclaimed yield.",
]


scope_scan = [
]


def question_generator(target_file: str) -> str:
    """
    Generate exploit-focused audit and fuzzing questions for one contracts target.

    ```
    target_file format:
    "'File Name: rewards/MasterMagpie.sol -> Scope: Critical. ...'"
    """

    prompt = f"""
    ```

    Generate exploit-focused security audit questions for this exact contracts target:

    {target_file}

    Project focus:
    MagpieXYZ is a veTokenomics yield booster on BNB Chain and Arbitrum. Focus on value-bearing paths any wallet can call: MasterMagpie staking and MGP emission accounting, BaseRewardPool reward-index math, VLMGP and mWomSV lock/unlock slots and penalties, WombatStaking LP custody and veWOM locking, mWOM/SmartWomConvert minting, pool helpers and ManualCompound, WombatBribeManager vote weight and bribe harvesting, airdrops/vesting/referral distribution.

    Rules:
    * Treat `File Name:` as the exact contract file.
    * Treat `Scope:` as the ONLY impact to target.
    * Assume full repo context is accessible.
    * Do not ask for code or say anything is missing.
    * Use exact Solidity symbols (function, modifier, state variable, struct field) when possible.
    * Attacker is unprivileged only: any EOA or contract they deploy, holding only tokens they bought. They can call public/external functions, stake, lock, vote, claim, deposit and withdraw LP, use flash loans, reenter, and front-run in the same block.
    * Attacker is NOT the owner, poolManager, allowedOperator, rewardManager, compounder, ankrOperator, ProxyAdmin, or any whitelisted helper. Ignore malicious-admin, malicious-governance, compromised-key, upgrade, oracle-manipulation, external-protocol-bug, misconfiguration, and social-engineering assumptions.
    * Ignore test files, mocks, reader/view-only contracts, docs, config, and dependency-only issues.
    * Ignore pure gas, unbounded-loop, storage-growth, code-style, and best-practice findings with no fund impact.
    * Every question must end in a concrete Immunefi impact: direct theft of user funds, permanent freezing of funds, protocol insolvency, governance voting result manipulation, theft or permanent freezing of unclaimed yield, or funds frozen at least 24 hours.
    * Generate 30 to 40 high-signal questions.
    * At least 70% must target reward/emission accounting theft, principal or receipt-token mismatch, unbacked minting, lock/unlock slot abuse, vote weight inflation, or claim/allocation double-spend.
    * Every question must be testable with a Hardhat/Foundry unit, fork, or invariant test.
    * Avoid generic checklist questions and repeated root causes.

    Core invariants:
    * Conservation: a user can never withdraw or claim more principal, MGP, or bonus reward than their own recorded stake and accrual entitle them to.
    * Backing: every receipt token and every mWOM in circulation is backed one-to-one by LP or WOM actually held or locked by the protocol.
    * Custody: only the account itself (or a contract it authorized) can move, claim to a new receiver, unlock, or reassign its stake, locks, and rewards.
    * Vote integrity: total and per-user votes never exceed the vlMGP weight actually locked, and no user can alter another user's vote.
    * Exit safety: a locked or staked position always remains withdrawable under the documented cooldown and penalty rules, and cannot be bricked by another user.

    Each question must include:
    1. target function/modifier;
    2. attacker action (a concrete transaction);
    3. preconditions (tokens or stake held);
    4. call sequence;
    5. invariant tested;
    6. scoped impact;
    7. proof idea.

    Output only valid Python. No markdown. No explanations.

    questions = [
    "[File: {target_file}] [Function: symbol_or_method] Can an unprivileged ATTACKER_ACTION under PRECONDITIONS trigger CALL_SEQUENCE, violating INVARIANT, causing scoped impact: SCOPE_IMPACT? Proof idea: Hardhat/Foundry test PARAMETERS and assert CONSERVATION, BACKING, CUSTODY, VOTE_INTEGRITY, or EXIT_SAFETY.",
    ]
    """
    return prompt


def audit_format(security_question: str) -> str:
    """
    Generate a focused contracts exploit-validation prompt.
    """

    prompt = f"""# SECURITY AUDIT PROMPT

## Question
{security_question}

## Rules
- Use existing repo context only. Analyze only this question and scoped impact.
- Attacker is unprivileged only: any EOA or contract they deploy, holding only tokens they bought, calling public/external functions, flash-loaning, reentering, or front-running. No owner, poolManager, allowedOperator, rewardManager, compounder, ankrOperator, or ProxyAdmin rights; no leaked keys, no social engineering.
- Reject malicious-admin, malicious-governance, upgrade-path, oracle-manipulation, external-protocol-bug, and misconfiguration-only paths.
- Reject anything depending only on test/mock/reader/docs/config files, dependency bugs alone, gas or unbounded-loop concerns, or best-practice cleanup without fund impact.
- Focus on real economic loss: direct theft of user funds, permanent freezing of funds, protocol insolvency, governance voting result manipulation, theft or permanent freezing of unclaimed yield, or funds frozen at least 24 hours.

## Validate
- Trace the exact reachable path from the attacker's transaction (stake, lock, vote, claim, deposit, withdraw, convert) into the affected function.
- Check whether existing modifiers, nonReentrant, whenNotPaused, receipt-token accounting, or reward-index updates already stop it.
- Accept only a concrete, quantifiable loss of principal, unclaimed yield, backing, or vote outcome.
- Require exact file/function support and a reproducible Hardhat/Foundry PoC.

## Output
If valid, output exactly:

### Title
[Bug statement] - ([File: file_path])

### Summary
[2-3 sentences]

### Finding Description
[Code path, root cause, attacker inputs, exploit flow, and why existing checks fail]

### Impact Explanation
[Concrete scoped impact and matching Immunefi impact class]

### Likelihood Explanation
[Preconditions, capital needed, feasibility, repeatability]

### Recommendation
[Specific fix]

### Proof of Concept
[Hardhat/Foundry test plan with expected assertions]

If invalid, output exactly:
#NoVulnerability found for this question.

No extra text.
"""
    return prompt


def validation_format(report: str) -> str:
    """
    Generate a strict bounty-style validation prompt for contracts security claims.
    """
    prompt = f"""# VALIDATION PROMPT

## Security Claim
{report}

## Rules
- Validate only the submitted claim.
- Check SECURITY.md and Researcher.Md for scope, exclusions, and valid impact classes.
- Do not create a new vulnerability if the submitted claim is weak or invalid.
- Do not upgrade severity unless the provided evidence proves the higher impact.
- Reject malicious-admin, privileged-address, governance-attack, upgrade, oracle, external-protocol, stablecoin-depeg, misconfiguration, leaked-key, dependency-only, docs/style, gas/unbounded-loop, and test/mock/reader-only issues.
- Reject pure griefing with no attacker profit and no user loss, and reject claims that only rely on the protocol not being topped up with reward tokens.
- Reject if the exploit needs owner, poolManager, allowedOperator, rewardManager, compounder, or ankrOperator privileges, victim social engineering, or an impossible setup.
- Reject if the bug was fixed, acknowledged, or publicly disclosed already, per the eligibility rules.
- A valid report must be triggerable by an unprivileged wallet using only public/external functions and tokens it can obtain on market.
- The final impact must map to an in-scope Magpie impact: direct theft of user funds, permanent freezing of funds, protocol insolvency, governance voting result manipulation, theft of unclaimed yield, permanent freezing of unclaimed yield, or temporary freezing of funds for at least 24 hours.
- Prefer #NoVulnerability over speculative reports.

## Required Validation Checks
All must pass:
1. Exact in-scope file, function, and line/code references.
2. Clear root cause and broken security assumption.
3. Reachable exploit path: preconditions -> attacker transaction -> trigger -> loss.
4. Existing modifiers, reentrancy guards, receipt-token and reward-index accounting reviewed and shown insufficient.
5. Concrete in-scope impact with quantified funds or vote outcome affected, and realistic likelihood.
6. Reproducible proof path: Hardhat/Foundry unit, fork, or invariant test with the exact call sequence.
7. No obvious rejection reason from SECURITY.md, known issues, privilege assumptions, or scope exclusions.

## Silent Triage Questions
Before output, internally answer:
- Can an ordinary wallet trigger this with public functions and no privileged role?
- Does the code actually behave as claimed on the deployed configuration?
- Is the loss caused by this code, not by an admin action, an oracle, or an external protocol?
- Is the theft, freeze, insolvency, or vote manipulation concrete and quantifiable, not hypothetical?
- Would an Immunefi triager accept the PoC as end-effect proof on an asset in scope?
- What exact test would prove it?

## Output
If valid, output exactly:

Audit Report

## Title
[Clear vulnerability statement] - ([File: file_path])

## Summary
[2-3 sentence summary of the bug and impact]

## Finding Description
[Exact code path, root cause, exploit flow, and why existing checks fail]

## Impact Explanation
[Concrete in-scope impact, severity rationale, and Immunefi impact category]

## Likelihood Explanation
[Attacker capability, capital required, feasibility, repeatability]

## Recommendation
[Specific fix guidance]

## Proof of Concept
[Minimal reproducible Hardhat/Foundry test plan or exact call sequence]

If invalid, output exactly:
#NoVulnerability found for this question.

Output only one of the two outcomes above. No extra text.
"""
    return prompt


def scan_format(report: str) -> str:
    """
    Generate a short cross-project analog scan prompt for contracts.
    """
    prompt = f"""# ANALOG SCAN PROMPT

## External Report
{report}

## Rules
- Use in-scope production repo context only. Do not ask for code or claim missing files.
- Use the external report only as a bug-class hint, not as proof.
- Keep only unprivileged-wallet analogs in MasterMagpie emission accounting, BaseRewardPool reward math, VLMGP/mWomSV lock slots and forfeits, WombatStaking LP custody and receipt tokens, mWOM/SmartWomConvert minting, pool helpers and ManualCompound, WombatBribeManager voting and bribes, or airdrop/vesting/referral distribution.
- Reject malicious-admin, privileged-role, governance, oracle, external-protocol, mocked-only, dependency-only, gas-only, and no-impact analogs.

## Validate
- Map the bug class to the strongest reachable contracts path from an ordinary wallet's transaction.
- Prove root cause with exact file/function support.
- Accept only concrete direct theft of user funds, permanent freezing of funds, protocol insolvency, governance voting result manipulation, theft or permanent freezing of unclaimed yield, or a 24-hour-plus freeze.

## Output (Strict)
If valid analog exists, output:

### Title
[Clear vulnerability statement] - ([File: file_path])

### Summary
### Finding Description
### Impact Explanation
### Likelihood Explanation
### Recommendation
### Proof of Concept

If not, output exactly:
#NoVulnerability found for this question.

No extra text.
"""
    return prompt
