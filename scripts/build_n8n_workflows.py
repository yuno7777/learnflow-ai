"""Build script generating standard n8n workflow JSON blueprints in `automation/n8n-workflows/`.

===============================================================================
WHAT THIS FILE IS RESPONSIBLE FOR:
===============================================================================
This script automates the generation of importable n8n workflow JSON definitions:
1. DRY Security Implementation:
   - Shared JavaScript code snippets (`VERIFY_JS` and `SIGN_JS`) are injected into every
     generated n8n workflow file.
   - Ensures HMAC-SHA256 signature verification and callback signing logic is identical
     across all workflows, preventing configuration drift.
2. Five Core Automation Pipelines:
   - `01-learner-onboarding.json`: Welcome email generation upon student creation.
   - `02-inactive-learner-reminder.json`: Scheduled daily cron sweep for inactive learners.
   - `03-support-ticket-triage.json`: AI classification and suggested answer drafting.
   - `04-document-processed.json`: Ingestion notification or automatic retry trigger.
   - `05-weekly-analytics-summary.json`: Monday morning executive KPI email digest.

===============================================================================
RUNNING THIS SCRIPT:
===============================================================================
`python scripts/build_n8n_workflows.py`
Generates clean JSON documents inside `automation/n8n-workflows/`.
"""

from __future__ import annotations

import json
import pathlib

OUT = pathlib.Path(__file__).resolve().parent.parent / "automation" / "n8n-workflows"

# Node.js snippet verifying incoming LearnFlow HMAC-SHA256 webhook signatures
VERIFY_JS = """// Verify the LearnFlow HMAC signature over the RAW request body.
// Header form: t=<unix>,v1=<hex>;  mac = HMAC_SHA256(secret, `${t}.${rawBody}`)
const crypto = require('crypto');
const item = $input.first().json;
const header = (item.headers || {})['x-learnflow-signature'] || '';
const raw = item.rawBody !== undefined ? item.rawBody : JSON.stringify(item.body);
const parts = Object.fromEntries(header.split(',').map(p => p.split('=')));
const expected = crypto
  .createHmac('sha256', $env.LEARNFLOW_WEBHOOK_SECRET)
  .update(`${parts.t}.${raw}`)
  .digest('hex');
const fresh = Math.abs(Date.now() / 1000 - Number(parts.t)) < 300;
if (!fresh) { throw new Error('LearnFlow signature outside tolerance window'); }
if (!parts.v1 || expected.length !== parts.v1.length ||
    !crypto.timingSafeEqual(Buffer.from(expected), Buffer.from(parts.v1))) {
  throw new Error('Invalid LearnFlow signature');
}
return [{ json: item.body }];"""

# Node.js snippet generating outbound LearnFlow HMAC-SHA256 callback signatures
SIGN_JS = """// Sign the callback body so FastAPI can verify this came from n8n.
const crypto = require('crypto');
const body = JSON.stringify($input.first().json);
const t = Math.floor(Date.now() / 1000);
const mac = crypto.createHmac('sha256', $env.LEARNFLOW_WEBHOOK_SECRET)
  .update(`${t}.${body}`).digest('hex');
return [{ json: { body, signature: `t=${t},v1=${mac}` } }];"""

CALLBACK_URL = (
    "={{ $json.callback_url || $env.LEARNFLOW_API_URL + "
    "'/api/v1/webhooks/n8n/callback' }}"
)
API_KEY_HEADER = [{"name": "X-API-Key", "value": "={{ $env.LEARNFLOW_API_KEY }}"}]
SIGNED_HEADERS = [
    {"name": "X-LearnFlow-Signature", "value": "={{ $json.signature }}"},
    {"name": "Content-Type", "value": "application/json"},
]


def node(name, type_, params, pos, version=1):
    """Constructs an individual n8n workflow node dictionary."""
    return {
        "parameters": params,
        "id": name.lower().replace(" ", "-").replace(":", "").replace("?", ""),
        "name": name,
        "type": type_,
        "typeVersion": version,
        "position": pos,
    }


def http(name, url, pos, method="POST", body=None, headers=None, send_body=True):
    """Constructs an HTTP request node."""
    params = {
        "method": method,
        "url": url,
        "sendHeaders": True,
        "headerParameters": {"parameters": headers or API_KEY_HEADER},
        "options": {"response": {"response": {"neverError": False}}, "timeout": 30000},
    }
    if send_body:
        params.update(
            {
                "sendBody": True,
                "specifyBody": "json",
                "jsonBody": body or "={{ JSON.stringify($json) }}",
            }
        )
    return node(name, "n8n-nodes-base.httpRequest", params, pos, 4.2)


def code(name, js, pos):
    """Constructs a JavaScript Code execution node."""
    return node(name, "n8n-nodes-base.code", {"jsCode": js}, pos, 2)


def webhook(name, path, pos):
    """Constructs an incoming Webhook trigger node."""
    return node(
        name,
        "n8n-nodes-base.webhook",
        {
            "httpMethod": "POST",
            "path": path,
            "responseMode": "responseNode",
            "options": {"rawBody": True},
        },
        pos,
        2,
    )


def respond(name, pos):
    """Constructs a Webhook response node."""
    return node(
        name,
        "n8n-nodes-base.respondToWebhook",
        {
            "respondWith": "json",
            "responseBody": "={{ JSON.stringify({ executionId: $execution.id, accepted: true }) }}",
        },
        pos,
        1,
    )


def email(name, to, subject, text, pos):
    """Constructs an Email sending node."""
    return node(
        name,
        "n8n-nodes-base.emailSend",
        {
            "fromEmail": "={{ $env.LEARNFLOW_EMAIL_FROM }}",
            "toEmail": to,
            "subject": subject,
            "text": text,
            "options": {},
        },
        pos,
        2.1,
    )


def if_node(name, left, right, pos, op_type="number", operation="lt"):
    """Constructs a conditional branching node."""
    return node(
        name,
        "n8n-nodes-base.if",
        {
            "conditions": {
                "options": {"caseSensitive": True, "version": 2},
                "conditions": [
                    {
                        "id": "c1",
                        "leftValue": left,
                        "rightValue": right,
                        "operator": {"type": op_type, "operation": operation},
                    }
                ],
                "combinator": "and",
            }
        },
        pos,
        2,
    )


def callback_builder(steps, output, pos):
    """Constructs JavaScript node formatting step execution results for FastAPI callback."""
    js = (
        "return [{ json: {\n"
        "  execution_id: $('Verify signature').first().json.execution_id,\n"
        "  status: 'succeeded',\n"
        "  n8n_execution_id: String($execution.id),\n"
        f"  output: {json.dumps(output)},\n"
        f"  steps: {json.dumps(steps)}\n"
        "} }];"
    )
    return code("Build callback", js, pos)


def chain(*names):
    """Helper connecting nodes sequentially in a linear chain."""
    return {
        a: {"main": [[{"node": b, "type": "main", "index": 0}]]}
        for a, b in zip(names, names[1:], strict=False)
    }


def branch(name, true_node, false_node):
    """Helper connecting true and false branches of an if_node."""
    return {
        name: {
            "main": [
                [{"node": true_node, "type": "main", "index": 0}],
                [{"node": false_node, "type": "main", "index": 0}],
            ]
        }
    }


def save(filename, name, nodes, connections, tags):
    """Serializes and writes n8n workflow JSON document."""
    OUT.mkdir(parents=True, exist_ok=True)
    doc = {
        "name": name,
        "nodes": nodes,
        "connections": connections,
        "active": False,
        "settings": {
            "executionOrder": "v1",
            "saveDataErrorExecution": "all",
            "saveDataSuccessExecution": "all",
            "saveExecutionProgress": True,
            "errorWorkflow": "",
        },
        "staticData": None,
        "tags": tags,
        "pinData": {},
        "versionId": "1",
        "meta": {"instanceId": "learnflow"},
    }
    (OUT / filename).write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
    print("wrote", filename)


API = "={{ $env.LEARNFLOW_API_URL }}"


def build_onboarding():
    """Generates the learner onboarding workflow JSON."""
    nodes = [
        webhook("Webhook: learner created", "learnflow/learner-onboarding", [-520, 300]),
        code("Verify signature", VERIFY_JS, [-300, 300]),
        http(
            "Fetch learner",
            API + "/api/v1/learners/{{ $json.data.learner_id }}",
            [-80, 300],
            method="GET",
            send_body=False,
        ),
        http(
            "Generate welcome message",
            API + "/api/v1/assistant/ask",
            [140, 300],
            body=(
                "={{ JSON.stringify({ question: 'Write a two paragraph welcome message for ' "
                "+ $json.full_name + ' who is starting their first course.', "
                "learner_id: $json.id }) }}"
            ),
        ),
        email(
            "Send welcome email",
            "={{ $('Fetch learner').item.json.email }}",
            "Welcome to your training programme",
            "={{ $json.answer }}",
            [360, 300],
        ),
        http(
            "Record activity",
            API + "/api/v1/learners/{{ $('Fetch learner').item.json.id }}/activity",
            [580, 300],
            send_body=False,
        ),
        callback_builder(
            [
                {"name": "n8n:fetch_learner", "kind": "http", "status": "succeeded"},
                {"name": "n8n:generate_welcome", "kind": "ai", "status": "succeeded"},
                {"name": "n8n:send_welcome_email", "kind": "email", "status": "succeeded"},
            ],
            {"welcome_sent": True},
            [800, 300],
        ),
        code("Sign callback", SIGN_JS, [1020, 300]),
        http("POST callback", CALLBACK_URL, [1240, 300], body="={{ $json.body }}",
             headers=SIGNED_HEADERS),
        respond("Respond", [1460, 300]),
    ]
    connections = chain(
        "Webhook: learner created", "Verify signature", "Fetch learner",
        "Generate welcome message", "Send welcome email", "Record activity",
        "Build callback", "Sign callback", "POST callback", "Respond",
    )
    save("01-learner-onboarding.json", "LearnFlow - Learner onboarding", nodes, connections,
         ["learnflow", "onboarding"])


def build_inactivity():
    """Generates the inactive learner reminder cron workflow JSON."""
    nodes = [
        node(
            "Every day at 07:00",
            "n8n-nodes-base.scheduleTrigger",
            {"rule": {"interval": [{"field": "cronExpression", "expression": "0 7 * * *"}]}},
            [-520, 300],
            1.2,
        ),
        http(
            "Fetch inactive learners",
            API + "/api/v1/analytics/inactive-learners"
            "?threshold_days={{ $env.LEARNFLOW_INACTIVITY_DAYS || 14 }}&max_reminders=3",
            [-300, 300],
            method="GET",
            send_body=False,
        ),
        code(
            "Split learners",
            "return $input.first().json.learners.map(l => ({ json: l }));",
            [-80, 300],
        ),
        if_node("Under reminder limit?", "={{ $json.reminders_sent }}", 3, [140, 300]),
        http(
            "Draft reminder",
            API + "/api/v1/assistant/ask",
            [360, 220],
            body=(
                "={{ JSON.stringify({ question: 'Write a short supportive nudge for ' "
                "+ $json.full_name + ' who has been inactive for ' + $json.days_inactive "
                "+ ' days on ' + ($json.course_title || 'their course') + '.', "
                "learner_id: $json.id }) }}"
            ),
        ),
        email(
            "Send reminder",
            "={{ $('Split learners').item.json.email }}",
            "Pick up where you left off",
            "={{ $json.answer }}",
            [580, 220],
        ),
        http(
            "Escalate: open ticket",
            API + "/api/v1/tickets",
            [580, 420],
            body=(
                "={{ JSON.stringify({ subject: 'Escalation: ' + $json.full_name "
                "+ ' unresponsive', body: 'Learner reached the reminder limit without "
                "re-engaging.', learner_id: $json.id, triage: false }) }}"
            ),
        ),
    ]
    connections = chain(
        "Every day at 07:00", "Fetch inactive learners", "Split learners", "Under reminder limit?"
    )
    connections.update(branch("Under reminder limit?", "Draft reminder", "Escalate: open ticket"))
    connections.update(chain("Draft reminder", "Send reminder"))
    save("02-inactive-learner-reminder.json", "LearnFlow - Inactive learner reminders",
         nodes, connections, ["learnflow", "engagement"])


def build_triage():
    """Generates the support ticket triage workflow JSON."""
    nodes = [
        webhook("Webhook: ticket created", "learnflow/ticket-triage", [-520, 300]),
        code("Verify signature", VERIFY_JS, [-300, 300]),
        http("Fetch ticket", API + "/api/v1/tickets/{{ $json.data.ticket_id }}", [-80, 300],
             method="GET", send_body=False),
        http(
            "Retrieve knowledge",
            API + "/api/v1/rag/query",
            [140, 300],
            body="={{ JSON.stringify({ question: $json.subject + ' ' + $json.body, top_k: 4 }) }}",
        ),
        if_node("Low confidence?", "={{ $json.confidence }}", 0.6, [360, 300]),
        http(
            "Escalate to human",
            API + "/api/v1/tickets/{{ $('Fetch ticket').item.json.id }}",
            [580, 200],
            method="PATCH",
            body='={{ JSON.stringify({ status: "escalated", escalated: true }) }}',
        ),
        http(
            "Mark triaged",
            API + "/api/v1/tickets/{{ $('Fetch ticket').item.json.id }}",
            [580, 420],
            method="PATCH",
            body='={{ JSON.stringify({ status: "triaged" }) }}',
        ),
        callback_builder(
            [
                {"name": "n8n:retrieve_knowledge", "kind": "rag", "status": "succeeded"},
                {"name": "n8n:route_ticket", "kind": "http", "status": "succeeded"},
            ],
            {"triaged": True},
            [800, 300],
        ),
        code("Sign callback", SIGN_JS, [1020, 300]),
        http("POST callback", CALLBACK_URL, [1240, 300], body="={{ $json.body }}",
             headers=SIGNED_HEADERS),
        respond("Respond", [1460, 300]),
    ]
    connections = chain(
        "Webhook: ticket created", "Verify signature", "Fetch ticket",
        "Retrieve knowledge", "Low confidence?",
    )
    connections.update(branch("Low confidence?", "Escalate to human", "Mark triaged"))
    connections["Escalate to human"] = {
        "main": [[{"node": "Build callback", "type": "main", "index": 0}]]
    }
    connections["Mark triaged"] = {
        "main": [[{"node": "Build callback", "type": "main", "index": 0}]]
    }
    connections.update(chain("Build callback", "Sign callback", "POST callback", "Respond"))
    save("03-support-ticket-triage.json", "LearnFlow - Support ticket triage",
         nodes, connections, ["learnflow", "support"])


def build_document_processed():
    """Generates the document processed notification workflow JSON."""
    nodes = [
        webhook("Webhook: document indexed", "learnflow/document-processed", [-520, 300]),
        code("Verify signature", VERIFY_JS, [-300, 300]),
        http("Fetch document", API + "/api/v1/documents/{{ $json.data.document_id }}", [-80, 300],
             method="GET", send_body=False),
        if_node("Indexed?", "={{ $json.status }}", "indexed", [140, 300],
                op_type="string", operation="equals"),
        email(
            "Notify admins",
            "={{ $env.LEARNFLOW_ADMIN_EMAIL }}",
            "=Document indexed: {{ $json.filename }}",
            "={{ $json.filename }} finished indexing: {{ $json.word_count }} words across "
            "{{ $json.page_count }} pages.",
            [360, 200],
        ),
        http(
            "Retry ingestion",
            API + "/api/v1/documents/{{ $json.id }}/reindex",
            [360, 420],
            send_body=False,
        ),
        callback_builder(
            [{"name": "n8n:notify_admins", "kind": "email", "status": "succeeded"}],
            {"notified": True},
            [580, 300],
        ),
        code("Sign callback", SIGN_JS, [800, 300]),
        http("POST callback", CALLBACK_URL, [1020, 300], body="={{ $json.body }}",
             headers=SIGNED_HEADERS),
        respond("Respond", [1240, 300]),
    ]
    connections = chain(
        "Webhook: document indexed", "Verify signature", "Fetch document", "Indexed?"
    )
    connections.update(branch("Indexed?", "Notify admins", "Retry ingestion"))
    connections["Notify admins"] = {
        "main": [[{"node": "Build callback", "type": "main", "index": 0}]]
    }
    connections["Retry ingestion"] = {
        "main": [[{"node": "Build callback", "type": "main", "index": 0}]]
    }
    connections.update(chain("Build callback", "Sign callback", "POST callback", "Respond"))
    save("04-document-processed.json", "LearnFlow - Document processing notification",
         nodes, connections, ["learnflow", "ingestion"])


def build_weekly_summary():
    """Generates the weekly analytics email digest workflow JSON."""
    digest_js = """const overview = $('Fetch overview').first().json;
const usage = $('Fetch AI usage').first().json;
const flows = $('Fetch workflow stats').first().json;
const failing = flows.filter(f => f.total > 0 && f.success_rate < 0.9);
const lines = [
  `Learners: ${overview.total_learners} (${overview.active_learners} active)`,
  `Completion rate: ${(overview.course_completion_rate * 100).toFixed(1)}%`,
  `Open tickets: ${overview.open_support_tickets}`,
  `AI spend (7d): $${usage.total_cost_usd.toFixed(4)} over ${usage.total_requests} calls`,
  `Workflow success: ${(overview.workflow_success_rate * 100).toFixed(1)}%`,
  failing.length
    ? `Needs attention: ${failing.map(f => f.name).join(', ')}`
    : 'All workflows healthy',
];
return [{ json: { digest: lines.join('\\n') } }];"""

    nodes = [
        node(
            "Every Monday 08:00",
            "n8n-nodes-base.scheduleTrigger",
            {"rule": {"interval": [{"field": "cronExpression", "expression": "0 8 * * 1"}]}},
            [-520, 300],
            1.2,
        ),
        http("Fetch overview", API + "/api/v1/analytics/overview", [-300, 300],
             method="GET", send_body=False),
        http("Fetch AI usage", API + "/api/v1/analytics/usage?days=7", [-80, 220],
             method="GET", send_body=False),
        http("Fetch workflow stats", API + "/api/v1/analytics/workflows", [-80, 400],
             method="GET", send_body=False),
        code("Compose digest", digest_js, [140, 300]),
        email(
            "Email digest",
            "={{ $env.LEARNFLOW_ADMIN_EMAIL }}",
            "LearnFlow weekly summary",
            "={{ $json.digest }}",
            [360, 300],
        ),
    ]
    connections = chain("Every Monday 08:00", "Fetch overview")
    connections["Fetch overview"] = {
        "main": [
            [
                {"node": "Fetch AI usage", "type": "main", "index": 0},
                {"node": "Fetch workflow stats", "type": "main", "index": 0},
            ]
        ]
    }
    connections.update(chain("Fetch AI usage", "Compose digest"))
    connections["Fetch workflow stats"] = {
        "main": [[{"node": "Compose digest", "type": "main", "index": 0}]]
    }
    connections.update(chain("Compose digest", "Email digest"))
    save("05-weekly-analytics-summary.json", "LearnFlow - Weekly analytics summary",
         nodes, connections, ["learnflow", "analytics"])


if __name__ == "__main__":
    build_onboarding()
    build_inactivity()
    build_triage()
    build_document_processed()
    build_weekly_summary()
