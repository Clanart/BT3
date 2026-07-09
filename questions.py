import json
import os

from decouple import config

# todo: if scope_files is: 500 > 50, 300 > 30 , 100 > 10
MAX_REPO = 20
# todo: the path from https:///github.com/dfinity/ICRC-1
SOURCE_REPO = "Near-One/omni-bridge"
# todo: the name of the repository
REPO_NAME = "omni-bridge"
run_number = os.environ.get('GITHUB_RUN_NUMBER') or os.environ.get('CI_PIPELINE_IID', '0')


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
    "near/omni-bridge/src/btc.rs",
    "near/omni-bridge/src/lib.rs",
    "near/omni-bridge/src/migrate.rs",
    "near/omni-bridge/src/storage.rs",
    "near/omni-bridge/src/token_lock.rs",
    "near/omni-prover/evm-prover/src/lib.rs",
    "near/omni-prover/mpc-omni-prover/src/lib.rs",
    "near/omni-prover/wormhole-omni-prover-proxy/src/byte_utils.rs",
    "near/omni-prover/wormhole-omni-prover-proxy/src/lib.rs",
    "near/omni-prover/wormhole-omni-prover-proxy/src/parsed_vaa.rs",
    "near/omni-token/src/lib.rs",
    "near/omni-token/src/migrate.rs",
    "near/omni-token/src/omni_ft.rs",
    "near/omni-types/src/bounded_string.rs",
    "near/omni-types/src/btc.rs",
    "near/omni-types/src/errors.rs",
    "near/omni-types/src/evm/events.rs",
    "near/omni-types/src/evm/header.rs",
    "near/omni-types/src/evm/mod.rs",
    "near/omni-types/src/evm/receipt.rs",
    "near/omni-types/src/hex_types.rs",
    "near/omni-types/src/lib.rs",
    "near/omni-types/src/locker_args.rs",
    "near/omni-types/src/mpc_types.rs",
    "near/omni-types/src/near_events.rs",
    "near/omni-types/src/prover_args.rs",
    "near/omni-types/src/prover_result.rs",
    "near/omni-types/src/sol_address.rs",
    "near/omni-types/src/starknet/events.rs",
    "near/omni-types/src/starknet/mod.rs",
    "near/omni-types/src/utils.rs",
    "near/token-deployer/src/lib.rs",
    "near/token-deployer/src/migrate.rs",
    "evm/src/common/Borsh.sol",
    "evm/src/common/IBridgeToken.sol",
    "evm/src/common/ICustomMinter.sol",
    "evm/src/eNear/contracts/ENearProxy.sol",
    "evm/src/eNear/contracts/IENear.sol",
    "evm/src/omni-bridge/contracts/BridgeToken.sol",
    "evm/src/omni-bridge/contracts/BridgeTypes.sol",
    "evm/src/omni-bridge/contracts/HlBridgeToken.sol",
    "evm/src/omni-bridge/contracts/OmniBridge.sol",
    "evm/src/omni-bridge/contracts/OmniBridgeWormhole.sol",
    "evm/src/omni-bridge/contracts/SelectivePausableUpgradable.sol",
    "solana/programs/bridge_token_factory/src/constants.rs",
    "solana/programs/bridge_token_factory/src/error.rs",
    "solana/programs/bridge_token_factory/src/instructions/admin/change_config.rs",
    "solana/programs/bridge_token_factory/src/instructions/admin/initialize.rs",
    "solana/programs/bridge_token_factory/src/instructions/admin/mod.rs",
    "solana/programs/bridge_token_factory/src/instructions/admin/pause.rs",
    "solana/programs/bridge_token_factory/src/instructions/admin/update_metadata.rs",
    "solana/programs/bridge_token_factory/src/instructions/mod.rs",
    "solana/programs/bridge_token_factory/src/instructions/user/deploy_token.rs",
    "solana/programs/bridge_token_factory/src/instructions/user/finalize_transfer.rs",
    "solana/programs/bridge_token_factory/src/instructions/user/finalize_transfer_sol.rs",
    "solana/programs/bridge_token_factory/src/instructions/user/get_version.rs",
    "solana/programs/bridge_token_factory/src/instructions/user/init_transfer.rs",
    "solana/programs/bridge_token_factory/src/instructions/user/init_transfer_sol.rs",
    "solana/programs/bridge_token_factory/src/instructions/user/log_metadata.rs",
    "solana/programs/bridge_token_factory/src/instructions/user/mod.rs",
    "solana/programs/bridge_token_factory/src/instructions/wormhole_cpi.rs",
    "solana/programs/bridge_token_factory/src/lib.rs",
    "solana/programs/bridge_token_factory/src/state/config.rs",
    "solana/programs/bridge_token_factory/src/state/message/deploy_token.rs",
    "solana/programs/bridge_token_factory/src/state/message/finalize_transfer.rs",
    "solana/programs/bridge_token_factory/src/state/message/init_transfer.rs",
    "solana/programs/bridge_token_factory/src/state/message/log_metadata.rs",
    "solana/programs/bridge_token_factory/src/state/message/mod.rs",
    "solana/programs/bridge_token_factory/src/state/mod.rs",
    "solana/programs/bridge_token_factory/src/state/used_nonces.rs",
    "starknet/src/bridge_token.cairo",
    "starknet/src/bridge_types.cairo",
    "starknet/src/lib.cairo",
    "starknet/src/omni_bridge.cairo",
    "starknet/src/utils/borsh.cairo",
    "starknet/src/utils.cairo",
]

target_scopes = [
    "Critical. Direct theft, unauthorized release, or unauthorized mint of native or bridged assets across NEAR, EVM, Solana, or StarkNet",
    "Critical. Permanent freezing, irrecoverable lock, or unclaimable settlement of user or protocol funds in bridge, token, or vault flows",
    "High. Cross-chain replay, double-finalization, nonce reuse, or duplicate settlement that enables double-spend or unbacked supply",
    "High. Proof, signature, MPC, Wormhole, or light-client verification bypass enabling unauthorized transfer finalization, token deployment, or message execution",
    "High. Balance, decimal, fee, token-mapping, or accounting corruption that breaks bridge collateralization or misdirects value",
]



def question_generator(target_file: str) -> str:
    """
    Generate exploit-focused audit + fuzzing questions for one Omni Bridge production target.

    ```
    target_file format:
    "'File Name: near/omni-bridge/src/lib.rs -> Scope: Critical. Direct theft, unauthorized release, or unauthorized mint of native or bridged assets across NEAR, EVM, Solana, or StarkNet'"
    ```
    """

    prompt = f"""
    ```

    Generate exploit-focused security audit and fuzzing questions for this exact Omni Bridge target:

    {target_file}

    Use live context from the project if available: NEAR omni-bridge settlement, fee, fast-transfer, BTC/UTXO, token-lock, and migration flows; omni-token mint/burn logic; token-deployer flows; omni-types payload serialization/parsing; EVM OmniBridge/OmniBridgeWormhole/BridgeToken/eNear contracts; Solana bridge_token_factory instruction/state/message flows; StarkNet omni_bridge and bridge_token flows; EVM/Wormhole/MPC prover verification; nonce tracking; finalised-transfer bookkeeping; decimal normalization; metadata logging; cross-chain token mapping; and cryptographic signature/proof validation.

    Protocol focus:
    This repository implements a multi-chain bridge between NEAR and foreign chains using MPC-signed outbound transfers and proof- or signature-verified inbound transfers. The audit focus is whether an unprivileged attacker can cause unauthorized transfer finalization, duplicate settlement, proof/signature replay, token deployment abuse, accounting drift, collateral mismatch, or permanent fund lock across NEAR, EVM, Solana, StarkNet, Wormhole-backed chains, and supported UTXO flows.

    Core invariants:

    * Transfers must settle at most once per legitimate origin event, nonce, or signed payload across every supported chain and prover path.
    * Funds locked, burned, minted, unlocked, or fee-routed on one chain must stay fully backed and correctly accounted for on the destination chain.
    * Proof verification, Wormhole/VAA parsing, MPC/ECDSA/eth-signature checks, and message serialization must reject forged, replayed, malformed, stale, cross-domain, or differently-encoded payloads.
    * Token deployment, metadata propagation, token mapping, and controller/locker interactions must not let attackers hijack canonical asset identity or mint unbacked representations.
    * Decimal normalization, fee handling, refunds, native-token wrapping, and fast-transfer or UTXO settlement logic must not leak value, strand funds, or create accounting drift outside documented behavior.

    Rules:

    * Treat `File Name:` as the exact file/module.
    * Treat `Scope:` as the ONLY impact to target.
    * Assume full repo context is accessible.
    * Do not ask for code or say anything is missing.
    * Attacker is unprivileged: bridge user, token holder, contract caller, relayer submitting public proofs/messages, token deployer candidate, recipient string controller, or user controlling public cross-chain inputs.
    * Do not rely on malicious operators, guardians, colluding MPC threshold signers, leaked keys, privileged addresses, governance abuse, social engineering, front-run-only paths, network-level DoS, chain reorg assumptions, oracle-only failures, or public-mainnet testing.
    * Do not generate questions that depend only on known out-of-scope classes from SECURITY.md such as unbounded gas/storage consumption, griefing without asset/security impact, Wormhole guardian compromise, NEAR base-chain attacks, decimal dust from normalization, or intentional rejected-relayer stake forfeiture.
    * Generate 20 to 30 high-signal questions.
    * At least 70% must be multi-step flow, invariant, fuzz, accounting, replay, verifier, settlement, or cross-module questions.
    * Every question must be testable by PoC, unit test, fuzz test, invariant test, or differential test.
    * Avoid generic checklist questions and repeated root causes.
    * Every question must target a plausible valid issue.

    High-value attack surfaces:

    * Settlement flows: `init_transfer`, `fin_transfer`, `deploy_token`, `log_metadata`, fee claim, fast transfer, native-token transfer, BTC/Zcash/UTXO settlement, and token lock/unlock paths.
    * Verification and replay boundaries: MPC signature validation, ECDSA recovery, Wormhole/VAA parsing, light-client or prover result handling, nonce/finalization bitmaps, chain-id/domain separation, and stale-proof handling.
    * Asset identity and accounting: token mappings, wrapped-token deployment, metadata propagation, decimals normalization, fee/native fee accounting, storage/refund accounting, and bridge-token mint/burn/unlock symmetry.
    * Cross-chain parsing and serialization: Borsh/ABI/Cairo/Anchor payload encoding, proof args/results, event/header parsing, receipt decoding, ByteArray/string/account/address conversion, and signer/recipient binding.
    * Upgrade, migration, and cross-module state: migration paths, token/controller updates, pause-gated flows, bridge factory state, used-nonce tracking, and inter-contract callbacks that can desynchronize custody or authorization.

    Impact mapping:

    * Critical: Direct theft, unauthorized release, or unauthorized mint of native or bridged assets across NEAR, EVM, Solana, or StarkNet.
    * Critical: Permanent freezing, irrecoverable lock, or unclaimable settlement of user or protocol funds in bridge, token, or vault flows.
    * High: Cross-chain replay, double-finalization, nonce reuse, or duplicate settlement that enables double-spend or unbacked supply.
    * High: Proof, signature, MPC, Wormhole, or light-client verification bypass enabling unauthorized transfer finalization, token deployment, or message execution.
    * High: Balance, decimal, fee, token-mapping, or accounting corruption that breaks bridge collateralization or misdirects value.

    Each question must include:

    1. target function/module;
    2. attacker action;
    3. preconditions;
    4. call sequence;
    5. invariant tested;
    6. scoped impact;
    7. proof idea.

    Output only valid Python. No markdown. No explanations.

    questions = [
    "[File: {target_file}] [Function: symbol_or_module] Can an unprivileged ATTACKER_ACTION under PRECONDITIONS trigger CALL_SEQUENCE, violating INVARIANT, causing scoped impact: SCOPE_IMPACT? Proof idea: fuzz/state-test PARAMETERS and assert EXPECTED_PROPERTY.",
    ]
    """
    return prompt


def audit_format(question: str) -> str:
    """
    Generate a focused Omni Bridge exploit-question validation prompt.
    """
    return f"""# QUESTION SCAN PROMPT

## Exploit Question
{question}

Main Focus should be on production Omni Bridge files from `scope_files`, especially:
- near/omni-bridge/src
- near/omni-prover/*/src
- near/omni-token/src
- near/omni-types/src
- near/token-deployer/src
- evm/src/common
- evm/src/eNear/contracts
- evm/src/omni-bridge/contracts
- solana/programs/bridge_token_factory/src
- starknet/src
Issues outside those production files are out of scope unless required as direct supporting context.


## Scope Rules
- Audit only production Omni Bridge code.
- Do not ask for repo contents or claim files are missing.
- Ignore tests, docs, mocks, e2e assets, scripts, configs, build files, IDE files, package metadata, vendored libraries, and local-only fixtures.

## Objective
Decide whether the question leads to a real, reachable Omni Bridge vulnerability.
The attacker must be unprivileged and enter through public bridge calls, token callbacks, proof/message submission, metadata/deploy flows, recipient-controlled inputs, or other public cross-chain inputs.
The impact must match one of the allowed Omni Bridge impacts below.
Prefer #NoVulnerability unless the path is concrete, local-testable, and bounty-grade.

## Allowed Impact Scope
Only these impacts are valid:
- Critical. Direct theft, unauthorized release, or unauthorized mint of native or bridged assets across NEAR, EVM, Solana, or StarkNet.
- Critical. Permanent freezing, irrecoverable lock, or unclaimable settlement of user or protocol funds in bridge, token, or vault flows.
- High. Cross-chain replay, double-finalization, nonce reuse, or duplicate settlement that enables double-spend or unbacked supply.
- High. Proof, signature, MPC, Wormhole, or light-client verification bypass enabling unauthorized transfer finalization, token deployment, or message execution.
- High. Balance, decimal, fee, token-mapping, or accounting corruption that breaks bridge collateralization or misdirects value.

## Method
1. Trace the attacker-controlled entrypoint.
2. Map it to exact production Omni Bridge files/functions.
3. Check the relevant guard: proof/signature validation, nonce/finalization checks, fee/accounting checks, token mapping and deployment checks, parser bounds, callback sequencing, or bridge collateralization invariants.
4. Decide whether the questioned invariant can actually break under intended deployment.
5. Prove root cause with file/function/line references.
6. Confirm realistic likelihood and exact scoped impact.
7. Reject if current validation already prevents the exploit.

## Reject Immediately
- Requires trusted role, leaked key, malicious operator behavior, colluding MPC threshold signers, compromised Wormhole guardians, or privileged operator access.
- Requires third-party dependency compromise, Sybil/51% attack, phishing, public-mainnet testing, chain attack assumptions, or network-level DoS only.
- Only affects tests, docs, configs, scripts, mocks, local fixtures, vendored libraries, or local deployment choices.
- External dependency behavior is the only cause.
- Impact is only logging, observability, local misconfiguration, non-security correctness, harmless revert, stale read, rejected update, decimal dust, rejected relayer stake forfeiture, griefing without security impact, or theoretical risk.
- No concrete scoped impact or no realistic exploit path.

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
    Generate a short cross-project analog scan prompt for Omni Bridge.
    """
    prompt = f"""# ANALOG SCAN PROMPT

## External Report
{report}

Main Focus should be on production Omni Bridge files from `scope_files`, especially:
- near/omni-bridge/src
- near/omni-prover/*/src
- near/omni-token/src
- near/omni-types/src
- near/token-deployer/src
- evm/src/common
- evm/src/eNear/contracts
- evm/src/omni-bridge/contracts
- solana/programs/bridge_token_factory/src
- starknet/src
Issues outside those production files are out of scope unless required as direct supporting context.


## Access Rules (Strict)
- Treat production Omni Bridge files in the provided scope as accessible context.
- Do not claim missing/inaccessible files.
- Do not ask for repository contents.
- Do not scan tests, docs, build files, IDE files, configs, resources, local fixtures, vendored libraries, package metadata, or e2e assets as audited targets.

## Objective
Use the external report's vulnerability class as a hint to find valid issues based on the Omni Bridge bounty scope.
Focus on reachable issues triggered by an unprivileged bridge user, proof/message submitter, token holder, token deployer candidate, recipient/input controller, or other public protocol input user.
Only report an analog if this codebase has its own reachable root cause and the impact matches one of the allowed Omni Bridge impacts below.

## Allowed Impact Scope
Only these impacts are valid:
- Critical. Direct theft, unauthorized release, or unauthorized mint of native or bridged assets across NEAR, EVM, Solana, or StarkNet.
- Critical. Permanent freezing, irrecoverable lock, or unclaimable settlement of user or protocol funds in bridge, token, or vault flows.
- High. Cross-chain replay, double-finalization, nonce reuse, or duplicate settlement that enables double-spend or unbacked supply.
- High. Proof, signature, MPC, Wormhole, or light-client verification bypass enabling unauthorized transfer finalization, token deployment, or message execution.
- High. Balance, decimal, fee, token-mapping, or accounting corruption that breaks bridge collateralization or misdirects value.

## Method
1. Classify vuln type: replay/double-finalization, proof/signature bypass, asset-accounting bug, token-mapping collision, parser/serialization issue, callback/state desync, or verifier/domain-separation flaw.
2. Map to Omni Bridge components and exact production files.
3. Prove root cause with exact file/function/module/line references.
4. Confirm concrete scoped impact and realistic likelihood.
5. Explain the attacker-controlled entry path and why this repository's code is a necessary vulnerable step.
6. Reject if the impact does not match one of the allowed Omni Bridge impacts above.

## Disqualify Immediately
- No reachable attacker-controlled entry path.
- Requires trusted role, leaked key, malicious operator behavior, colluding MPC threshold signers, compromised Wormhole guardians, or privileged operator access.
- Requires third-party dependency compromise, Sybil/51% attack, phishing, public-mainnet testing, chain attack assumptions, or network-level DoS only.
- External dependency behavior is the only cause.
- Test/docs/config/build-only issue.
- Known out-of-scope issue class from SECURITY.md such as decimal dust, rejected relayer stake forfeiture, griefing, or unbounded gas/storage consumption only.
- Theoretical-only issue with no protocol impact.
- Impact is only local misconfiguration, observability noise, logging noise, harmless revert, stale read, or non-security correctness.
- Impact or likelihood missing.

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



def validation_format(report: str) -> str:
    """
    Generate a strict Omni Bridge bounty-style validation prompt for security claims.
    """
    prompt = f"""# VALIDATION PROMPT

## Security Claim
{report}

Main Focus should be on production Omni Bridge files from `scope_files`, especially:
- near/omni-bridge/src
- near/omni-prover/*/src
- near/omni-token/src
- near/omni-types/src
- near/token-deployer/src
- evm/src/common
- evm/src/eNear/contracts
- evm/src/omni-bridge/contracts
- solana/programs/bridge_token_factory/src
- starknet/src
Issues outside those production files are out of scope unless required as direct supporting context.


## Rules
- Validate only the submitted claim.
- Check SECURITY.md, Researcher.md if present, and the Omni Bridge bounty scope for scope, exclusions, and valid impact classes.
- Do not create a new vulnerability if the submitted claim is weak or invalid.
- Do not upgrade severity unless the provided evidence proves the higher impact.
- Reject malicious-operator-only, privileged-address-only, leaked-key, colluding-threshold-only, Wormhole-guardian-compromise-only, host-compromise, best-practice, docs/style, config/test-only, gas-optimization-only, front-run-only, network-level-DoS-only, and purely theoretical issues.
- Reject if the exploit requires unrealistic assumptions, victim mistakes, phishing/social engineering, third-party dapp/oracle compromise, governance or 51% control, sybil/centralization assumptions, public-mainnet DoS testing, NEAR base-chain attacks, or unsupported protocol behavior.
- A valid report must be triggerable by an unprivileged user through bridge transactions, token callbacks, proof or message submission, deploy-token or metadata flows, or another public cross-chain protocol input, unless the claim proves privilege escalation from an unprivileged path.
- The final impact must match an in-scope bounty impact, not just a generic code bug.
- Reject any issue whose final impact is not one of the allowed Omni Bridge impacts listed below.
- Reject issues that only restate known out-of-scope behavior in SECURITY.md, including decimal normalization dust, rejected relayer stake forfeiture, griefing-only outcomes, or unbounded gas/storage consumption without a qualifying bridge-security impact.
- Prefer #NoVulnerability over speculative reports.

## In-Scope Protocol Areas
The claim must affect production in-scope Omni Bridge code or systems, such as:
- NEAR bridge flows: `ft_on_transfer`, `init_transfer`, `fin_transfer`, `deploy_token`, fee claim, fast transfer, token lock, UTXO/BTC settlement, migrations, and callback/accounting paths.
- Proof and verifier flows: EVM prover, Wormhole prover proxy, MPC prover logic, VAA/parsing helpers, signature recovery, domain separation, and proof result handling.
- Token and asset logic: omni-token, bridge-token deployment, token-deployer, metadata propagation, token mapping, wrapped/native asset custody, decimals normalization, and mint/burn/unlock symmetry.
- Foreign-chain bridge contracts/programs: EVM OmniBridge/OmniBridgeWormhole/eNear, Solana `bridge_token_factory`, StarkNet `omni_bridge`, their nonce/finalization logic, deploy-token/finalize/init-transfer handlers, and associated serialization or state modules.
- Shared type/parsing logic: omni-types, Borsh/ABI/Cairo/Anchor payload shaping, event/header/receipt parsing, and address/string conversion used by production settlement paths.

Reject third-party dapps, unlisted public websites, tests, docs, examples, mocks, generated files, local deployment helpers, vendored libraries, e2e tooling, and issues that only affect local developer tooling unless the submitted claim proves a direct in-scope Omni Bridge security impact.

## Allowed Impact Scope
Only these impacts are valid:
- Critical. Direct theft, unauthorized release, or unauthorized mint of native or bridged assets across NEAR, EVM, Solana, or StarkNet.
- Critical. Permanent freezing, irrecoverable lock, or unclaimable settlement of user or protocol funds in bridge, token, or vault flows.
- High. Cross-chain replay, double-finalization, nonce reuse, or duplicate settlement that enables double-spend or unbacked supply.
- High. Proof, signature, MPC, Wormhole, or light-client verification bypass enabling unauthorized transfer finalization, token deployment, or message execution.
- High. Balance, decimal, fee, token-mapping, or accounting corruption that breaks bridge collateralization or misdirects value.

Informational, non-security correctness, observability/logging-only, harmless reject/revert, stale read without consensus/state/accounting/security impact, local misconfiguration, and non-demonstrably-exploitable reports are invalid for this validation output.

If the submitted claim does not concretely prove one of the allowed Omni Bridge impacts above, it is invalid.

## Required Validation Checks
All must pass:
1. Exact in-scope file, function, and line/code references.
2. Clear root cause and broken protocol/security/accounting/authentication/certification assumption.
3. Reachable exploit path: preconditions -> attacker action -> trigger -> bad result.
4. Existing checks/guards reviewed and shown insufficient.
5. Concrete impact that exactly matches one allowed Omni Bridge impact above, with realistic likelihood.
6. Reproducible safe proof path: runnable PoC, deterministic integration test, invariant/fuzz test, differential test, or exact local manual steps.
7. No obvious rejection reason from SECURITY.md, Researcher.md if present, known issues, privileges, or scope exclusions.

## Silent Triage Questions
Before output, internally answer:
- Can a normal external user trigger this through a public Omni Bridge protocol path?
- Does the code actually behave as claimed?
- Is the impact caused by Omni Bridge production code, not by an external dependency alone?
- Is the theft/replay/mis-accounting/funds-freeze impact concrete, not hypothetical?
- Does the claim avoid malicious operator, privileged address, leaked key, mainnet DoS, governance, and third-party compromise assumptions?
- Would a bounty triager accept the proof?
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
[Concrete allowed Omni Bridge bounty impact and severity rationale]

## Likelihood Explanation
[Attacker capability, required conditions, feasibility, repeatability]

## Recommendation
[Specific fix guidance]

## Proof of Concept
[Minimal reproducible steps or fuzz/invariant/fork test plan]

If invalid, output exactly:
#NoVulnerability found for this question.

Output only one of the two outcomes above. No extra text.
"""
    return prompt
