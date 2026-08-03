import json
import os

MAX_REPO = 40
SOURCE_REPO = 'polytope-labs/hyperbridge'
REPO_NAME = 'hyperbridge'
run_number = os.environ.get("GITHUB_RUN_NUMBER") or os.environ.get(
    "CI_PIPELINE_IID", "0"
)


def get_cyclic_index(run_number, max_index=100):
    """Convert run number to a cyclic index between 1 and max_index."""
    return (int(run_number) - 1) % max_index + 1


def load_repository_urls():
    """Load repository URLs from repositories.json."""
    repo_file = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "repositories.json"
    )
    if not os.path.exists(repo_file):
        return []

    try:
        with open(repo_file, "r", encoding="utf-8") as f:
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
    'evm/src/apps/BandwidthManager.sol',
    'evm/src/apps/IntentGatewayV2.sol',
    'evm/src/apps/intentsv2/ExtrinsicIntents.sol',
    'evm/src/apps/intentsv2/IntentsBase.sol',
    'evm/src/apps/intentsv2/IntrinsicIntents.sol',
    'evm/src/apps/intentsv2/SolverAccount.sol',
    'evm/src/consensus/Codec.sol',
    'evm/src/consensus/ConsensusRouter.sol',
    'evm/src/consensus/EcdsaBeefy.sol',
    'evm/src/consensus/SP1Beefy.sol',
    'evm/src/consensus/Types.sol',
    'evm/src/core/EvmHost.sol',
    'evm/src/core/HandlerV2.sol',
    'evm/src/core/HostManager.sol',
    'evm/src/utils/CallDispatcher.sol',
    'evm/src/utils/HyperFungibleTokenImpl.sol',
    'evm/src/utils/SimplexPaymaster.sol',
    'evm/src/utils/VWAPOracle.sol',
    'evm/src/utils/uniswapv2/GnosisUniswapV2Wrapper.sol',
    'evm/src/utils/uniswapv2/UniV3UniswapV2Wrapper.sol',
    'evm/src/utils/uniswapv2/UniV4UniswapV2Wrapper.sol',
    'modules/consensus/beefy/primitives/src/lib.rs',
    'modules/consensus/beefy/verifier/src/error.rs',
    'modules/consensus/beefy/verifier/src/lib.rs',
    'modules/consensus/beefy/verifier/src/sp1.rs',
    'modules/consensus/bsc/verifier/src/error.rs',
    'modules/consensus/bsc/verifier/src/lib.rs',
    'modules/consensus/bsc/verifier/src/primitives.rs',
    'modules/consensus/geth-primitives/src/lib.rs',
    'modules/consensus/grandpa/primitives/src/justification.rs',
    'modules/consensus/grandpa/primitives/src/lib.rs',
    'modules/consensus/grandpa/verifier/src/error.rs',
    'modules/consensus/grandpa/verifier/src/lib.rs',
    'modules/consensus/pharos/primitives/src/constants.rs',
    'modules/consensus/pharos/primitives/src/lib.rs',
    'modules/consensus/pharos/primitives/src/spv.rs',
    'modules/consensus/pharos/primitives/src/types.rs',
    'modules/consensus/pharos/verifier/src/error.rs',
    'modules/consensus/pharos/verifier/src/lib.rs',
    'modules/consensus/pharos/verifier/src/state_proof.rs',
    'modules/consensus/sync-committee/primitives/src/consensus_types.rs',
    'modules/consensus/sync-committee/primitives/src/constants.rs',
    'modules/consensus/sync-committee/primitives/src/deneb.rs',
    'modules/consensus/sync-committee/primitives/src/domains.rs',
    'modules/consensus/sync-committee/primitives/src/electra.rs',
    'modules/consensus/sync-committee/primitives/src/error.rs',
    'modules/consensus/sync-committee/primitives/src/lib.rs',
    'modules/consensus/sync-committee/primitives/src/ssz/byte_list.rs',
    'modules/consensus/sync-committee/primitives/src/ssz/mod.rs',
    'modules/consensus/sync-committee/primitives/src/types.rs',
    'modules/consensus/sync-committee/primitives/src/util.rs',
    'modules/consensus/sync-committee/verifier/src/crypto.rs',
    'modules/consensus/sync-committee/verifier/src/error.rs',
    'modules/consensus/sync-committee/verifier/src/lib.rs',
    'modules/consensus/tendermint/primitives/src/address.rs',
    'modules/consensus/tendermint/primitives/src/keys.rs',
    'modules/consensus/tendermint/primitives/src/lib.rs',
    'modules/consensus/tendermint/primitives/src/prover.rs',
    'modules/consensus/tendermint/primitives/src/verifier.rs',
    'modules/consensus/tendermint/verifier/src/hashing.rs',
    'modules/consensus/tendermint/verifier/src/lib.rs',
    'modules/consensus/tendermint/verifier/src/sp_io_verifier.rs',
    'modules/consensus/tendermint/verifier/src/verifier.rs',
    'modules/ismp/clients/arbitrum/src/error.rs',
    'modules/ismp/clients/arbitrum/src/lib.rs',
    'modules/ismp/clients/beefy/src/consensus.rs',
    'modules/ismp/clients/beefy/src/lib.rs',
    'modules/ismp/clients/bsc/src/lib.rs',
    'modules/ismp/clients/bsc/src/pallet.rs',
    'modules/ismp/clients/casper-ffg/src/lib.rs',
    'modules/ismp/clients/grandpa/src/consensus.rs',
    'modules/ismp/clients/grandpa/src/lib.rs',
    'modules/ismp/clients/grandpa/src/messages.rs',
    'modules/ismp/clients/ismp-arbitrum/src/lib.rs',
    'modules/ismp/clients/ismp-arbitrum/src/pallet.rs',
    'modules/ismp/clients/ismp-optimism/src/lib.rs',
    'modules/ismp/clients/ismp-optimism/src/pallet.rs',
    'modules/ismp/clients/optimism/src/error.rs',
    'modules/ismp/clients/optimism/src/lib.rs',
    'modules/ismp/clients/parachain/client/src/consensus.rs',
    'modules/ismp/clients/parachain/client/src/lib.rs',
    'modules/ismp/clients/pharos/src/lib.rs',
    'modules/ismp/clients/polygon/src/error.rs',
    'modules/ismp/clients/polygon/src/lib.rs',
    'modules/ismp/clients/sync-committee/src/beacon_client.rs',
    'modules/ismp/clients/sync-committee/src/lib.rs',
    'modules/ismp/clients/sync-committee/src/pallet.rs',
    'modules/ismp/clients/sync-committee/src/types.rs',
    'modules/ismp/clients/tendermint/src/lib.rs',
    'modules/ismp/clients/tendermint/src/pallet.rs',
    'modules/ismp/core/src/abi.rs',
    'modules/ismp/core/src/consensus.rs',
    'modules/ismp/core/src/dispatcher.rs',
    'modules/ismp/core/src/error.rs',
    'modules/ismp/core/src/events.rs',
    'modules/ismp/core/src/handlers.rs',
    'modules/ismp/core/src/handlers/consensus.rs',
    'modules/ismp/core/src/handlers/request.rs',
    'modules/ismp/core/src/handlers/response.rs',
    'modules/ismp/core/src/handlers/timeout.rs',
    'modules/ismp/core/src/host.rs',
    'modules/ismp/core/src/lib.rs',
    'modules/ismp/core/src/messaging.rs',
    'modules/ismp/core/src/module.rs',
    'modules/ismp/core/src/router.rs',
    'modules/ismp/state-machines/evm/src/lib.rs',
    'modules/ismp/state-machines/evm/src/presets.rs',
    'modules/ismp/state-machines/evm/src/substrate_evm.rs',
    'modules/ismp/state-machines/evm/src/tendermint.rs',
    'modules/ismp/state-machines/evm/src/types.rs',
    'modules/ismp/state-machines/evm/src/utils.rs',
    'modules/ismp/state-machines/pharos/src/lib.rs',
    'modules/ismp/state-machines/substrate/src/lib.rs',
    'modules/pallets/bandwidth/src/abi.rs',
    'modules/pallets/bandwidth/src/lib.rs',
    'modules/pallets/bandwidth/src/types.rs',
    'modules/pallets/beefy-consensus-proofs/src/lib.rs',
    'modules/pallets/beefy-consensus-proofs/src/types.rs',
    'modules/pallets/call-decompressor/src/lib.rs',
    'modules/pallets/consensus-incentives/src/impls.rs',
    'modules/pallets/consensus-incentives/src/lib.rs',
    'modules/pallets/fishermen/src/extension.rs',
    'modules/pallets/fishermen/src/lib.rs',
    'modules/pallets/host-executive/src/lib.rs',
    'modules/pallets/hyper-fungible-token/src/error.rs',
    'modules/pallets/hyper-fungible-token/src/impls.rs',
    'modules/pallets/hyper-fungible-token/src/lib.rs',
    'modules/pallets/hyper-fungible-token/src/module.rs',
    'modules/pallets/hyper-fungible-token/src/types.rs',
    'modules/pallets/intents-coprocessor/src/lib.rs',
    'modules/pallets/intents-coprocessor/src/types.rs',
    'modules/pallets/ismp/src/child_trie.rs',
    'modules/pallets/ismp/src/dispatcher.rs',
    'modules/pallets/ismp/src/errors.rs',
    'modules/pallets/ismp/src/events.rs',
    'modules/pallets/ismp/src/fee_handler.rs',
    'modules/pallets/ismp/src/host.rs',
    'modules/pallets/ismp/src/impls.rs',
    'modules/pallets/ismp/src/lib.rs',
    'modules/pallets/ismp/src/utils.rs',
    'modules/pallets/messaging-incentives/src/lib.rs',
    'modules/pallets/mmr/primitives/src/lib.rs',
    'modules/pallets/mmr/src/lib.rs',
    'modules/pallets/mmr/src/mmr/mmr.rs',
    'modules/pallets/mmr/src/mmr/mod.rs',
    'modules/pallets/mmr/src/mmr/storage.rs',
    'modules/pallets/relayer/src/accumulate.rs',
    'modules/pallets/relayer/src/lib.rs',
    'modules/pallets/relayer/src/outbound_consensus.rs',
    'modules/pallets/relayer/src/outbound_request.rs',
    'modules/pallets/relayer/src/withdrawal.rs',
    'modules/pallets/state-coprocessor/src/impls.rs',
    'modules/pallets/state-coprocessor/src/lib.rs',
    'modules/trees/ethereum/src/lib.rs',
    'modules/trees/ethereum/src/node_codec.rs',
    'modules/trees/ethereum/src/storage_proof.rs',
    'modules/utils/bls-utils/src/bls.rs',
    'modules/utils/bls-utils/src/lib.rs',
    'modules/utils/bls-utils/src/ssz/byte_vector.rs',
    'modules/utils/bls-utils/src/ssz/mod.rs',
    'modules/utils/crypto/src/lib.rs',
    'modules/utils/crypto/src/verification.rs',
    'parachain/runtimes/gargantua/src/genesis_config.rs',
    'parachain/runtimes/gargantua/src/ismp.rs',
    'parachain/runtimes/gargantua/src/lib.rs',
    'parachain/runtimes/gargantua/src/xcm.rs',
    'parachain/runtimes/nexus/src/genesis_config.rs',
    'parachain/runtimes/nexus/src/ismp.rs',
    'parachain/runtimes/nexus/src/lib.rs',
    'parachain/runtimes/nexus/src/xcm.rs',
    'sdk/packages/core/contracts/apps/HyperApp.sol',
    'sdk/packages/core/contracts/apps/HyperFungibleToken.sol',
    'sdk/packages/core/contracts/apps/HyperFungibleTokenUpgradeable.sol',
    'sdk/packages/core/contracts/apps/WrappedHyperFungibleToken.sol',
    'sdk/packages/core/contracts/apps/WrappedHyperFungibleTokenUpgradeable.sol',
    'sdk/packages/core/contracts/libraries/Message.sol',
    'sdk/packages/core/contracts/libraries/StateMachine.sol',
    'sdk/packages/core/contracts/vaults/StreamingYieldVault.sol',
]

target_scopes = [
    'Critical. An unprivileged attacker can forge, replay, or smuggle a consensus update, cross-chain request, response, or timeout so Hyperbridge accepts false remote state or executes an unauthorized transaction.',
    'Critical. An unprivileged attacker can mint, unlock, withdraw, refund, or redirect bridged assets, escrowed order funds, relayer fees, or other protocol balances they do not own.',
    'Critical. An unprivileged attacker can bypass proof verification, state-machine binding, source-app binding, or challenge-period enforcement and settle a message, order, or governance action from an unauthenticated origin.',
    'High. An unprivileged attacker can execute the same request, response, timeout, fill, cancellation, withdrawal, or reward claim more than once by breaking commitment, receipt, nonce, or replay protections.',
    'High. An unprivileged attacker can permanently lock or burn user funds, refunds, withdrawals, fills, or claims by corrupting custody, timeout, or settlement accounting in a production flow.',
    'High. An unprivileged attacker can manipulate destination execution, beneficiary selection, token amount, bandwidth credit, reward payout, or settlement state so valid protocol activity resolves to the wrong recipient or wrong value.',
]

HYPERBRIDGE_ALLOWED_IMPACT_SCOPE = """## Hyperbridge Impact Gate
Accept only production bridge/runtime/pallet/contract impacts that match the live bounty:
stealing or loss of funds, unauthorized transaction or execution, transaction manipulation,
logic attacks, replay/double-claim/double-settlement, or false proof/state acceptance.
Discard: malicious peer or node behavior, compromised relayer/prover/operator assumptions,
front-run-only ideas, generic gas or network DoS, imported dependency bugs, style issues,
compiler-version complaints, and testnet-only behavior."""

HYPERBRIDGE_AUDIT_PIVOTS = """## Hyperbridge Pivots
- Consensus proofs, state proofs, challenge periods, and state commitments must never let false remote state become trusted.
- Request, response, and timeout paths must bind chain id, module/app identity, commitment uniqueness, and one-time receipt handling on both Substrate and EVM.
- Bridged assets, order escrow, refunds, paymaster balances, relayer rewards, and bandwidth balances must move exactly once and only to the rightful beneficiary and amount.
- Cross-chain admin or host-management effects must not be reachable through malformed proofs, wrong module bindings, or unauthenticated message flow."""


def question_generator(target_file: str) -> str:
    """
    Generate security questions for one Hyperbridge target.
    """

    prompt = f"""
    Draft 18 to 24 Hyperbridge exploit questions for this exact file:
    {target_file}

    Focus:
    Stay on proof verification, state commitment handling, request/response/timeout routing,
    bridged asset custody, intent settlement and cancellation, relayer reward accounting,
    bandwidth accounting, call dispatch, and cross-chain host or governance execution.

    {HYPERBRIDGE_ALLOWED_IMPACT_SCOPE}

    {HYPERBRIDGE_AUDIT_PIVOTS}

    Rules:
    * `File Name:` must be this file. `Scope:` must select exactly one `target_scopes` item.
    * Use the repository context already in hand. Do not request more code.
    * The attacker is strictly unprivileged: a normal external user or contract using public calls, messages, proofs, callbacks, or settlement inputs.
    * Do not assume admin, governance, validator, collator, relayer, prover, node, peer chain, host manager, leaked key, whitelist, or off-repo infra control.
    * Never model a malicious peer, malicious node, or malicious relayer as the root cause.
    * Ignore tests, mocks, fixtures, benches, docs, readmes, generated files, `.toml`, version-only issues, dependency-only issues, and front-run-only ideas.
    * Prefer critical paths first, but include strong high-severity questions when justified.
    * Name the exact corrupted value: consensus state, state commitment, request commitment, request or response receipt, timeout result, beneficiary, amount, custody balance, order fill state, reward balance, bandwidth allowance, or host parameter.
    * Every question must be testable with a focused Rust or Solidity unit, integration, property, or fuzz-style test.

    Each question must include target symbol, attacker input, required state, verification or settlement path, broken invariant, corrupted value, scoped impact, and proof idea.

    Return Python only.

    questions = [
    "[File: {target_file}] [Symbol: symbol_or_contract] Can attacker-controlled INPUT under REQUIRED_STATE pass VERIFY_OR_SETTLE_PATH and break CROSS_CHAIN_INVARIANT, corrupting EXACT_VALUE with scoped impact SCOPE_IMPACT? Proof idea: write a focused repo test that drives ENTRYPOINT_OR_MESSAGE_FLOW and asserts the expected authenticity, one-time, beneficiary, or accounting property.",
    ]
    """
    return prompt


def audit_format(question: str) -> str:
    """
    Generate a focused Hyperbridge exploit-question validation prompt.
    """
    return f"""# HYPERBRIDGE REVIEW

## Submitted Question
{question}

## Scope Limits
- Review Hyperbridge production bridge, runtime, pallet, and smart-contract logic only.
- The attacker must enter through unprivileged calls, messages, proofs, callbacks, or settlement inputs.
- Ignore malicious peers, malicious nodes, compromised relayers, front-run-only claims, and excluded bounty families.

## Decision Standard
Treat it as valid only if unprivileged input can cause false proof acceptance, unauthorized execution, wrongful asset movement, duplicate settlement, 
wrong beneficiary or amount, or broken timeout/refund/reward accounting. Reject claims that require privileged operators, malicious infrastructure, or 
non-production artifacts.

## Required Impacts
{HYPERBRIDGE_ALLOWED_IMPACT_SCOPE}

{HYPERBRIDGE_AUDIT_PIVOTS}

## Review Path
1. Trace the exact verification, routing, settlement, withdrawal, or claim path.
2. Compare the intended chain, module, beneficiary, amount, receipt, or commitment to the actual stored or executed result.
3. Name the wrong root, receipt, commitment, beneficiary, amount, order state, balance, or host parameter.
4. Reject if proof checks, challenge periods, duplicate guards, auth checks, or accounting invariants already stop the path.

## Output
If valid:

### Title
[Clear vulnerability statement] - ([File: file_path])

### Summary
### Finding Description
### Impact Explanation
### Likelihood Explanation
### Recommendation
### Proof of Concept

If invalid, output exactly:
#NoVulnerability found for this question.
"""


def scan_format(report: str) -> str:
    """
    Generate a cross-project analog scan prompt for Hyperbridge issues.
    """
    prompt = f"""# HYPERBRIDGE ANALOG SCAN

## External Report
{report}

## Task
Use the external report only as a bug-class seed. Reason only from this Hyperbridge repository and find a real local analog in proof verification, 
request or timeout handling, bridge custody, intent settlement, reward claims, bandwidth accounting, paymaster logic, or host-management execution.

## Required Impacts
{HYPERBRIDGE_ALLOWED_IMPACT_SCOPE}

{HYPERBRIDGE_AUDIT_PIVOTS}

## Method
- First reduce the external report to its core broken invariant and attacker primitive.
- Internally generate 2 to 4 Hyperbridge candidate paths, then keep only the strongest one with exact file and function support.
- Prefer public-entrypoint paths that let an unprivileged attacker cause false state acceptance, unauthorized execution, wrong beneficiary or amount, duplicate settlement or claim, or fund loss/lock.
- Reject anything that needs a malicious peer, node, relayer, prover, admin, governance actor, leaked key, or front-run-only conditions.
- Name the exact corrupted value and show why existing guards do not stop the path.
- Do not answer with uncertainty, missing-context, or external-protocol analysis. Either produce a concrete Hyperbridge issue from local code evidence or return `#NoVulnerability found for this question.`
- If no locally provable analog survives these checks, return `#NoVulnerability found for this question.`

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
"""
    return prompt


def validation_format(report: str) -> str:
    """
    Generate a strict Hyperbridge validation prompt for security claims.
    """
    prompt = f"""# HYPERBRIDGE CLAIM VALIDATION

## Security Claim
{report}

## Rules
- Validate only the submitted claim against Hyperbridge production bridge, runtime, pallet, and contract logic in this repository.
- Do not widen the claim, change the target scope, or raise severity without evidence.
- A valid issue must come from an unprivileged external attacker using public calls, messages, proofs, callbacks, or settlement inputs exposed by scoped code.
- Reject malicious peer or node behavior, compromised relayer/prover/operator assumptions, leaked keys, privileged governance or validator powers, 
off-repo infra control, 
front-run-only claims, test or mock artifacts, docs, readmes, generated files, and `.toml`.
- The final impact must match one `target_scopes` item or the Hyperbridge impact gate below and must name the exact corrupted value.

## Required Impacts
{HYPERBRIDGE_ALLOWED_IMPACT_SCOPE}

{HYPERBRIDGE_AUDIT_PIVOTS}

## Required Checks
1. Exact file and function references in scoped code.
2. A clear invariant tied to proof authenticity, chain or module binding, one-time settlement, custody, payout, or beneficiary correctness.
3. A reachable exploit path from attacker input to bad state, bad execution, bad payout, or bad settlement.
4. Existing guards reviewed and shown insufficient.
5. Exact wrong value named: consensus state, state commitment, receipt, commitment, beneficiary, amount, custody balance, order state, reward balance,
 bandwidth allowance, or host parameter.
6. A reproducible proof path via Rust or Solidity unit, integration, property, or fuzz-style testing.

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
[Concrete allowed repository impact and severity rationale]

## Likelihood Explanation
[Attacker capability, required conditions, feasibility, repeatability]

## Recommendation
[Specific fix guidance]

## Proof of Concept
[Minimal reproducible steps or test plan]

If invalid, output exactly:
#NoVulnerability found for this question.

Output only one of the two outcomes above. No extra text.
"""
    return prompt
