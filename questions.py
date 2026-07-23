import json
import os

MAX_REPO = 25
SOURCE_REPO = 'chainwayxyz/clementine'
REPO_NAME = 'clementine'
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
    'bridge-circuit-host/src/bridge_circuit_host.rs',
    'bridge-circuit-host/src/docker.rs',
    'bridge-circuit-host/src/lib.rs',
    'bridge-circuit-host/src/seal_format.rs',
    'bridge-circuit-host/src/structs.rs',
    'bridge-circuit-host/src/utils.rs',
    'circuits-lib/src/bridge_circuit/constants.rs',
    'circuits-lib/src/bridge_circuit/groth16.rs',
    'circuits-lib/src/bridge_circuit/groth16_verifier.rs',
    'circuits-lib/src/bridge_circuit/lc_proof.rs',
    'circuits-lib/src/bridge_circuit/merkle_tree.rs',
    'circuits-lib/src/bridge_circuit/mod.rs',
    'circuits-lib/src/bridge_circuit/spv.rs',
    'circuits-lib/src/bridge_circuit/storage_proof.rs',
    'circuits-lib/src/bridge_circuit/structs.rs',
    'circuits-lib/src/bridge_circuit/transaction.rs',
    'circuits-lib/src/common/constants.rs',
    'circuits-lib/src/common/hashes.rs',
    'circuits-lib/src/common/mod.rs',
    'circuits-lib/src/common/zkvm.rs',
    'circuits-lib/src/header_chain/mmr_guest.rs',
    'circuits-lib/src/header_chain/mmr_native.rs',
    'circuits-lib/src/header_chain/mod.rs',
    'circuits-lib/src/lib.rs',
    'circuits-lib/src/work_only/mod.rs',
    'core/src/actor.rs',
    'core/src/aggregator.rs',
    'core/src/bin/cli.rs',
    'core/src/bitcoin_syncer.rs',
    'core/src/bitvm_client.rs',
    'core/src/builder/address.rs',
    'core/src/builder/block_cache.rs',
    'core/src/builder/mod.rs',
    'core/src/builder/script.rs',
    'core/src/builder/sighash.rs',
    'core/src/builder/transaction/challenge.rs',
    'core/src/builder/transaction/creator.rs',
    'core/src/builder/transaction/deposit_signature_owner.rs',
    'core/src/builder/transaction/input.rs',
    'core/src/builder/transaction/mod.rs',
    'core/src/builder/transaction/operator_assert.rs',
    'core/src/builder/transaction/operator_collateral.rs',
    'core/src/builder/transaction/operator_reimburse.rs',
    'core/src/builder/transaction/output.rs',
    'core/src/builder/transaction/sign.rs',
    'core/src/builder/transaction/txhandler.rs',
    'core/src/citrea.rs',
    'core/src/cli.rs',
    'core/src/compatibility.rs',
    'core/src/config/env.rs',
    'core/src/config/mod.rs',
    'core/src/config/protocol.rs',
    'core/src/constants.rs',
    'core/src/database/aggregator.rs',
    'core/src/database/bitcoin_syncer.rs',
    'core/src/database/header_chain_prover.rs',
    'core/src/database/migrations/0001_add_last_bump_block_height.down.sql',
    'core/src/database/migrations/0001_add_last_bump_block_height.up.sql',
    'core/src/database/migrations/0002_txsender_seen_at_height_no_bitcoin_syncer_fk.down.sql',
    'core/src/database/migrations/0002_txsender_seen_at_height_no_bitcoin_syncer_fk.up.sql',
    'core/src/database/migrations/0003_tx_sender_and_lcp_update.down.sql',
    'core/src/database/migrations/0003_tx_sender_and_lcp_update.up.sql',
    'core/src/database/migrations/0004_verifier_lcp_syncer_handler.down.sql',
    'core/src/database/migrations/0004_verifier_lcp_syncer_handler.up.sql',
    'core/src/database/mod.rs',
    'core/src/database/operator.rs',
    'core/src/database/pgmq.sql',
    'core/src/database/schema.sql',
    'core/src/database/state_machine.rs',
    'core/src/database/verifier.rs',
    'core/src/database/wrapper.rs',
    'core/src/deposit.rs',
    'core/src/encryption.rs',
    'core/src/errors.rs',
    'core/src/extended_bitcoin_rpc.rs',
    'core/src/header_chain_prover.rs',
    'core/src/lib.rs',
    'core/src/main.rs',
    'core/src/musig2.rs',
    'core/src/operator.rs',
    'core/src/rpc/aggregator.rs',
    'core/src/rpc/clementine.proto',
    'core/src/rpc/ecdsa_verification_sig.rs',
    'core/src/rpc/error.rs',
    'core/src/rpc/interceptors.rs',
    'core/src/rpc/mod.rs',
    'core/src/rpc/operator.rs',
    'core/src/rpc/parser/mod.rs',
    'core/src/rpc/parser/operator.rs',
    'core/src/rpc/parser/verifier.rs',
    'core/src/rpc/verifier.rs',
    'core/src/servers.rs',
    'core/src/states/context.rs',
    'core/src/states/event.rs',
    'core/src/states/kickoff.rs',
    'core/src/states/matcher.rs',
    'core/src/states/mod.rs',
    'core/src/states/round.rs',
    'core/src/states/task.rs',
    'core/src/task/aggregator_metric_publisher.rs',
    'core/src/task/entity_metric_publisher.rs',
    'core/src/task/lcp_syncer.rs',
    'core/src/task/manager.rs',
    'core/src/task/mod.rs',
    'core/src/task/payout_checker.rs',
    'core/src/task/status_monitor.rs',
    'core/src/task/tx_sender.rs',
    'core/src/tx_sender_ext.rs',
    'core/src/tx_sender_queue.rs',
    'core/src/utils.rs',
    'core/src/verifier.rs',
    'crates/clementine-config/src/grpc.rs',
    'crates/clementine-config/src/lib.rs',
    'crates/clementine-config/src/protocol.rs',
    'crates/clementine-config/src/telemetry.rs',
    'crates/clementine-config/src/tx_sender.rs',
    'crates/clementine-errors/src/lib.rs',
    'crates/clementine-extended-rpc/src/client.rs',
    'crates/clementine-extended-rpc/src/lib.rs',
    'crates/clementine-extended-rpc/src/retry.rs',
    'crates/clementine-primitives/src/lib.rs',
    'crates/clementine-tx-sender/migrations/0001_init.up.sql',
    'crates/clementine-tx-sender/src/citrea/data_serialization.rs',
    'crates/clementine-tx-sender/src/citrea/mod.rs',
    'crates/clementine-tx-sender/src/citrea/reveal_scripts.rs',
    'crates/clementine-tx-sender/src/citrea/sync.rs',
    'crates/clementine-tx-sender/src/client.rs',
    'crates/clementine-tx-sender/src/config.rs',
    'crates/clementine-tx-sender/src/confirmations.rs',
    'crates/clementine-tx-sender/src/cpfp.rs',
    'crates/clementine-tx-sender/src/db/citrea.rs',
    'crates/clementine-tx-sender/src/db/mod.rs',
    'crates/clementine-tx-sender/src/db/tx_sender.rs',
    'crates/clementine-tx-sender/src/db/wrapper.rs',
    'crates/clementine-tx-sender/src/jsonrpc/client.rs',
    'crates/clementine-tx-sender/src/jsonrpc/mod.rs',
    'crates/clementine-tx-sender/src/jsonrpc/server.rs',
    'crates/clementine-tx-sender/src/lib.rs',
    'crates/clementine-tx-sender/src/main.rs',
    'crates/clementine-tx-sender/src/nonstandard.rs',
    'crates/clementine-tx-sender/src/rbf.rs',
    'crates/clementine-tx-sender/src/rpc_errors.rs',
    'crates/clementine-tx-sender/src/signer.rs',
    'crates/clementine-tx-sender/src/task.rs',
    'crates/clementine-utils/src/address.rs',
    'crates/clementine-utils/src/lib.rs',
    'crates/clementine-utils/src/sign.rs',
    'crates/clementine-utils/src/tracing.rs',
    'crates/clementine-utils/src/traits.rs',
    'crates/tx-sender-jsonrpc-client/src/lib.rs',
    'crates/tx-sender-types/src/citrea.rs',
    'crates/tx-sender-types/src/clementine.rs',
    'crates/tx-sender-types/src/lib.rs',
    'risc0-circuits/bridge-circuit/guest/src/main.rs',
    'risc0-circuits/bridge-circuit/src/lib.rs',
    'risc0-circuits/header-chain/guest/src/main.rs',
    'risc0-circuits/header-chain/src/lib.rs',
    'risc0-circuits/work-only/guest/src/main.rs',
    'risc0-circuits/work-only/src/lib.rs',
]

target_scopes = [
    'Critical. An unprivileged attacker can make Clementine release, redirect, double-spend, or permanently lock bridged BTC, operator collateral, reimbursement outputs, or bridge-controlled UTXOs.',
    'Critical. An unprivileged attacker can make header-chain, work-only, bridge-circuit, SPV, light-client, storage-proof, or network/method binding logic accept a forged, replayed, stale, or wrong-context proof that changes bridge outcomes.',
    'High. An unprivileged attacker can replay, confuse, exhaust, or misbind MuSig2 nonce/session/signature material into an unauthorized move, payout, reimbursement, or challenge transaction.',
    'High. An unprivileged attacker can bypass RPC, mTLS, actor-role, parser, or database/state-machine authorization and trigger privileged verifier, operator, aggregator, or tx-sender actions.',
    'High. An unprivileged attacker can abuse automation, tx-sender, RBF/CPFP, queueing, fee, or UTXO-selection logic to spend the wrong input, strand reimbursements, or burn slashable funds during normal bridge flow.',
    'Medium. An unprivileged attacker can exploit watchtower ordering, canonical-chain selection, deposit/finality tracking, or Citrea/Bitcoin sync mismatches to cause honest bridge actions to be wrongly accepted, rejected, or stuck with material loss.',
]

CLEMENTINE_ALLOWED_IMPACT_SCOPE = '## Clementine Allowed Impact Gate\nOnly accept repository-relevant impacts:\n- Critical/High/Medium theft, loss, permanent lock, or slashable exposure of bridged BTC, operator collateral, reimbursement outputs, bridge-controlled UTXOs, or tx-sender-managed balances.\n- Acceptance of a forged, replayed, stale, cross-network, or otherwise invalid header-chain, work-only, bridge, SPV, light-client, or storage proof that changes deposit, withdrawal, or challenge outcomes.\n- Unauthorized state transition in deposit, payout, challenge, reimbursement, round, or watchtower flow that breaks bridge safety/liveness with material fund impact.\n- Authentication or authorization bypass in gRPC, mTLS, actor roles, signer flow, or database-backed state handling that grants privileged bridge actions to the wrong party.\nOut of scope: tests, mocks, fixtures, scripts, docs-only issues, local tooling, manifest/build/generated files, operator config mistakes without a code bug, privileged key compromise, honest external chain/node behavior unless scoped validation fails, fee-only issues, crashes, style, and dependency-only behavior.'

CLEMENTINE_AUDIT_PIVOTS = '## Smart Audit Pivots\n- Deposit path: nonce aggregation, partial signatures, move-tx creation, deposit finalization, and session lifecycle must bind the right deposit, round, keys, and scripts.\n- Withdrawal/challenge path: kickoff, payout, watchtower challenge, operator assert/disprove, reimbursement, and collateral handling must preserve canonical-chain and script-path invariants.\n- Proof path: header-chain -> work-only -> bridge-circuit -> light-client/storage/SPV verification must bind method IDs, network, block data, work, and witness contents correctly.\n- Trust-boundary path: gRPC/mTLS auth, actor RPCs, parser/interceptor logic, tx-sender RPC, DB state transitions, queues, and automation tasks must not let the wrong party advance bridge state or spend funds.'


def question_generator(target_file: str) -> str:
    """
    Generate security questions for one Clementine target.
    """

    prompt = f"""
    Generate Clementine security questions for this exact target file:

    {target_file}

    Project lens:
    Clementine is a BitVM-based BTC <-> Citrea bridge. Focus on deposit signing, move tx creation, payout/challenge flows, proof verification, actor RPC auth, database/state transitions, and tx-sender automation.

    Impact gate:
    {CLEMENTINE_ALLOWED_IMPACT_SCOPE}

    {CLEMENTINE_AUDIT_PIVOTS}

    Rules:
    * Treat `File Name:` as the exact file and `Scope:` as the only impact.
    * Assume repo context is accessible; do not ask for code.
    * The attacker is strictly unprivileged. Do not rely on operator, verifier, aggregator, watchtower, admin, signer, database, or infrastructure control unless the bug shows how an unprivileged attacker reaches the same effect through scoped code.
    * Trusted key compromise, malicious deployment, and off-repo infrastructure failures are out of scope unless scoped code fails to authenticate, bind, or validate them.
    * Exclude tests, mocks, fixtures, scripts, docs-only issues, local tooling, manifest/build/generated files, fee-only issues, crashes, style, and dependency-only behavior.
    * Generate 18 to 26 high-signal questions with non-overlapping root causes.
    * Name the exact corrupted value: bridged BTC amount, collateral UTXO, reimbursement output, nonce session, partial signature set, method ID, network binding, total work, block hash, storage slot, DB state, queue item, or RPC auth decision.
    * Every question must be testable with a Rust unit, integration, property, or fuzz-style test.

    Each question must include target symbol, attacker-controlled input, required state, call path, broken invariant, corrupted value, scoped impact, and proof idea.

    Output only valid Python. No markdown. No explanations.

    questions = [
    "[File: {target_file}] [Symbol: symbol_or_module] Can attacker-controlled INPUT under REQUIRED_STATE reach CALL_PATH and violate BRIDGE_OR_PROOF_INVARIANT, corrupting EXACT_VALUE_AT_RISK with scoped impact SCOPE_IMPACT? Proof idea: write a Rust test that drives ENTRYPOINT through the vulnerable state transition and asserts EXPECTED_SAFETY_PROPERTY.",
    ]
    """
    return prompt


def audit_format(question: str) -> str:
    """
    Generate a focused Clementine exploit-question validation prompt.
    """
    return f"""# CLEMENTINE QUESTION REVIEW

## Exploit Question
{question}

## Scope Rules
- Audit only Clementine production code in this repository.
- Ignore tests, mocks, fixtures, scripts, generated artifacts, local tooling, and docs-only issues with no code-level impact.
- Do not ask for repo contents or claim files are missing.

## Objective
    Decide whether the question leads to a real Clementine vulnerability. The attacker must be unprivileged and must enter through deposit, payout, challenge, proof, RPC, actor, automation, database, or tx-sender flows available in scoped code.

    Reject claims needing privileged key compromise, malicious deployment, off-repo infrastructure control, or honest external chain behavior without a scoped validation failure. Reject any claim that needs the attacker to already be operator, verifier, aggregator, watchtower, or admin. Prefer #NoVulnerability unless the path proves material fund loss, invalid proof acceptance, unauthorized privileged action, or broken bridge functionality.

## Required Impacts
{CLEMENTINE_ALLOWED_IMPACT_SCOPE}

{CLEMENTINE_AUDIT_PIVOTS}

## Method
1. Trace the unprivileged entrypoint.
2. Map it to exact scoped files and functions.
3. Follow the full path through actor logic, transaction construction, proof validation, DB state transitions, and final spend or state effects.
4. Identify the exact corrupted value and who loses funds, authority, or liveness.
5. Reject if existing guards preserve the invariant or if impact is immaterial.

## Reject Immediately
- Privileged key compromise, operator config mistakes, or malicious deployment assumptions without a scoped code bypass.
- Honest Bitcoin, Citrea, postgres, or external service behavior unless scoped validation/binding is missing.
- View-only mismatches, harmless deserialization differences, fee-only issues, logs, style, dependency-only behavior, tests, mocks, fixtures, scripts, or docs-only issues.

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
    Generate a cross-project analog scan prompt for Clementine issues.
    """
    prompt = f"""# ANALOG SCAN PROMPT

## External Report
{report}

## Task
Use the external report only as a bug-class seed. Search Clementine deposit, withdrawal, challenge, proof, RPC, DB, and tx-sender code for a native analog with concrete bridge impact.

## Required Impacts
{CLEMENTINE_ALLOWED_IMPACT_SCOPE}

{CLEMENTINE_AUDIT_PIVOTS}

Report only if this repository has its own reachable root cause, unprivileged trigger, broken invariant, exact corrupted value, and matching target scope or allowed impact. Reject privileged assumptions, malicious deployment, external-system-only issues, dependency-only behavior, and anything outside the production surface.

## Work Plan
1. Classify the external bug into one Clementine invariant.
2. Map it to exact scoped files/functions.
3. Trace attacker input through production validation and state updates.
4. Identify the wrong BTC amount, collateral UTXO, signature/session object, proof field, DB state, or authorization decision.
5. Reject if existing guards preserve the invariant or the impact is not material.

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
    Generate a strict Clementine validation prompt for security claims.
    """
    prompt = f"""# VALIDATION PROMPT

## Security Claim
{report}

## Rules
- Validate only the submitted claim against Clementine production code in this repository.
- Do not invent a stronger claim, change target scope, or upgrade severity without evidence.
- A valid issue must be triggered by an unprivileged external attacker using only capabilities exposed by scoped code.
- Trusted key compromise, malicious deployment, and off-repo infra control are out unless the code fails to authenticate, bind, or validate them.
- Reject any claim that needs the attacker to already hold operator, verifier, aggregator, watchtower, admin, database, or infrastructure privileges.
- Reject tests, mocks, fixtures, scripts, local tooling, docs-only issues, manifest/build/generated-file issues, fee-only issues, crashes, style, and dependency-only bugs.
- The final impact must match one `target_scopes` item or allowed impact below and identify the exact corrupted value.

## Required Impacts
{CLEMENTINE_ALLOWED_IMPACT_SCOPE}

{CLEMENTINE_AUDIT_PIVOTS}

## Required Checks
1. Exact file/function references in scoped code.
2. Clear broken Clementine invariant tied to funds, proof validity, actor authority, or bridge state correctness.
3. Reachable exploit path: preconditions -> attacker input -> production call path -> bad value.
4. Existing guards reviewed and shown insufficient.
5. Exact wrong value named: bridged BTC amount, collateral or reimbursement UTXO, nonce session, partial signature set, method ID, total work, block hash, storage proof field, DB state, queue item, or auth decision.
6. Reproducible proof path: Rust unit, integration, property, or fuzz-style test.

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
